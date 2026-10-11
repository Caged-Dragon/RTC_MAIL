"""Mail logic on Supabase Postgres: discover mailboxes, pull received mail, send mail through Resend."""
import html as htmllib
import logging
import re
import threading
import time
from datetime import datetime, timezone
from email.utils import formataddr, parseaddr

from . import config, resend_client
from .db import conn

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")
MAX_RECIPIENTS = 50          # per field (to / cc / bcc)
MAX_SUBJECT_LEN = 500
_CONFIG_ERROR_STATUSES = {400, 401, 403}   # Resend problems that retrying per-message cannot fix
_sync_lock = threading.Lock()


class SentNotSaved(Exception):
    """The message was delivered to Resend but could not be stored in the Sent folder."""


def norm_ts(value) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    s = str(value).strip().replace(" ", "T", 1)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if re.search(r"[+-]\d\d$", s):
        s += ":00"
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return datetime.now(timezone.utc)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def make_snippet(text: str, html: str) -> str:
    src = text or re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)
    src = re.sub(r"<[^>]+>", " ", src)
    return re.sub(r"\s+", " ", htmllib.unescape(src)).strip()[:160]


def as_list(value) -> list:
    if not value:
        return []
    return [value] if isinstance(value, str) else list(value)


def ensure_mailboxes() -> None:
    with conn() as c:
        c.execute(
            """insert into mailboxes (business_email_id, business_id, email_address, local_part, display_name, mailbox_type)
               select b.business_email_id,
                      (select min(business_id) from mailboxes),
                      lower(b.email_address), lower(split_part(b.email_address, '@', 1)), b.display_name,
                      case p.purpose_code when 'admin' then 'admin' when 'sales' then 'sales'
                           when 'support' then 'support' when 'billing' then 'billing'
                           when 'orders' then 'operations' else 'general' end::mailbox_type
               from business_email_addresses b
               join business_email_purposes p using (email_purpose_id)
               where b.is_active and lower(split_part(b.email_address, '@', 2)) = %s
                 and not exists (select 1 from mailboxes m where m.business_email_id = b.business_email_id)
                 and not exists (select 1 from mailboxes m where m.email_address = lower(b.email_address))
                 and (select min(business_id) from mailboxes) is not null
               on conflict do nothing""",
            (config.MAIL_DOMAIN,),
        )


def list_mailboxes(user=None) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            """select distinct on (m.email_address)
                      m.mailbox_id, m.local_part, m.email_address as address,
                      coalesce(nullif(m.display_name, ''), b.display_name, m.local_part) as label,
                      m.is_receiving_enabled, m.is_sending_enabled
               from mailboxes m
               join business_email_addresses b on b.business_email_id = m.business_email_id and b.is_active
               where m.is_active and m.email_address like %s and m.local_part = any(%s)
               order by m.email_address, m.mailbox_id""",
            ("%@" + config.MAIL_DOMAIN, config.REQUIRED_LOCALS),
        ).fetchall()
    req = {n: i for i, n in enumerate(config.REQUIRED_LOCALS)}
    rows.sort(key=lambda r: (req.get(r["local_part"], 99), r["local_part"]))
    if user is not None and not user.is_admin:
        rows = [r for r in rows if r["mailbox_id"] in user.mailbox_ids]
    return rows


def _box_by_local(local: str, user, sending=False) -> dict:
    for r in list_mailboxes(user):
        if r["local_part"] == local:
            if sending and not r["is_sending_enabled"]:
                raise ValueError("Sending is disabled for this mailbox")
            return r
    raise LookupError("Unknown mailbox")


def _addr(raw: str) -> tuple[str, str]:
    name, a = parseaddr(raw or "")
    return name, a.strip().lower()


def _is_company_mail(item: dict) -> bool:
    """True if any recipient is on the company domain (and the mail is therefore ours to keep)."""
    suffix = "@" + config.MAIL_DOMAIN
    return any(
        _addr(raw)[1].endswith(suffix)
        for key in ("to", "cc", "bcc")
        for raw in as_list(item.get(key))
    )


def _catch_all_box(boxes: dict) -> dict | None:
    """The mailbox that receives mail for unknown company addresses: admin@, else the first receiving mailbox."""
    for b in boxes.values():
        if b["local_part"] == "admin":
            return b
    return next(iter(boxes.values()), None)


def ingest_received(item: dict, boxes: dict | None = None) -> dict:
    rid = item.get("id")
    result = {"new": 0, "known": False, "failed": 0, "ours": False}
    if not rid:
        return result
    if boxes is None:
        boxes = {r["address"]: r for r in list_mailboxes() if r["is_receiving_enabled"]}

    targets = []
    for key in ("to", "cc", "bcc"):
        for raw in as_list(item.get(key)):
            box = boxes.get(_addr(raw)[1])
            if box and box not in targets:
                targets.append(box)

    if not targets and _is_company_mail(item):
        # Resend delivers EVERY address at the domain. Mail for an address that has no mailbox (typo, retired
        # alias, random@...) must still be kept here, never left only with the provider: file it in the catch-all.
        catch_all = _catch_all_box(boxes)
        if catch_all:
            targets = [catch_all]
            result["catch_all"] = True

    result["ours"] = bool(targets)
    if not targets:
        return result

    with conn() as c:
        have = c.execute(
            "select mailbox_id, resend_email_id from email_messages where resend_email_id = %s or resend_email_id like %s",
            (rid, rid + "#%"),
        ).fetchall()
    stored = {r["mailbox_id"] for r in have}
    base_used = any(r["resend_email_id"] == rid for r in have)

    full = None
    for box in targets:
        if box["mailbox_id"] in stored:
            result["known"] = True
            continue
        if full is None:
            try:
                full = {**item, **resend_client.get_received(rid)}
            except resend_client.ResendError as exc:
                if exc.status in _CONFIG_ERROR_STATUSES:
                    raise
                log.warning("Could not download received email %s: %s", rid, exc)
                result["failed"] += 1
                return result
        name, from_addr = parseaddr(full.get("from") or "")
        text, html = full.get("text") or "", full.get("html") or ""
        when = norm_ts(full.get("created_at"))
        stored_id = rid if not base_used else f"{rid}#{box['mailbox_id']}"
        with conn() as c:
            row = c.execute(
                """insert into email_messages
                   (mailbox_id, direction, status, message_id, in_reply_to, references_header, from_address, from_name,
                    subject, snippet, text_body, html_body, is_read, folder, received_at, resend_email_id)
                   values (%s,'inbound','received',%s,%s,%s,%s,%s,%s,%s,%s,%s,false,'inbox',%s,%s)
                   on conflict do nothing returning email_message_id""",
                (box["mailbox_id"], full.get("message_id") or None, None, None,
                 from_addr or full.get("from") or "", name, full.get("subject") or "",
                 make_snippet(text, html), text, html, when, stored_id),
            ).fetchone()
            if row:
                base_used = True
                result["new"] += 1
                _insert_recipients(c, row["email_message_id"], full)
    return result


def _insert_recipients(c, message_id: int, full: dict) -> None:
    for kind in ("to", "cc", "bcc"):
        for raw in as_list(full.get(kind)):
            name, a = _addr(raw)
            if a:
                c.execute(
                    "insert into email_recipients (email_message_id, recipient_type, email_address, display_name)"
                    " values (%s, %s::mail_recipient_type, %s, %s)",
                    (message_id, kind, a, name or None),
                )


def sync_inbox(max_pages: int = 5, full_scan: bool = False) -> dict:
    """Pull received mail from Resend. Only one sync runs at a time per server instance."""
    if not _sync_lock.acquire(blocking=False):
        return {"new": 0, "failed": 0, "partial": False, "busy": True}
    try:
        ensure_mailboxes()
        boxes = {r["address"]: r for r in list_mailboxes() if r["is_receiving_enabled"]}
        deadline = time.monotonic() + config.SYNC_BUDGET_SECONDS
        new = failed = 0
        partial = False
        after = None
        for _ in range(20 if full_scan else max_pages):
            page = resend_client.list_received(limit=100, after=after)
            items = page.get("data") or []
            page_new = 0
            for item in items:
                if time.monotonic() > deadline:
                    partial = True
                    break
                r = ingest_received(item, boxes)
                new += r["new"]
                failed += r["failed"]
                page_new += r["new"] + r["failed"]
            if partial or not items or not page.get("has_more") or (page_new == 0 and not full_scan):
                break
            after = items[-1]["id"]
        return {"new": new, "failed": failed, "partial": partial}
    finally:
        _sync_lock.release()


def _like(q: str) -> str:
    return "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def counts(user) -> dict:
    ids = [r["mailbox_id"] for r in list_mailboxes(user)]
    with conn() as c:
        rows = c.execute(
            """select mb.local_part, count(*) as n from email_messages m join mailboxes mb using (mailbox_id)
               where m.folder='inbox' and not m.is_read and m.deleted_at is null and m.mailbox_id = any(%s)
               group by mb.local_part""",
            (ids,),
        ).fetchall()
    return {r["local_part"]: r["n"] for r in rows}


_SEARCH_OP_RE = re.compile(r'\b(from|to|subject|is):("[^"]*"|\S+)', re.I)
MAX_PAGE = 200
BULK_ACTIONS = ("read", "unread", "star", "unstar", "trash", "restore", "delete_forever")


def parse_search(q: str) -> tuple[str, list[tuple[str, str]]]:
    """Split a Gmail-style query into free text and operators: from:, to:, subject:, is:unread|read|starred."""
    ops = [(m.group(1).lower(), m.group(2).strip('"')) for m in _SEARCH_OP_RE.finditer(q or "")]
    return _SEARCH_OP_RE.sub(" ", q or "").strip(), ops


def list_messages(user, local: str, folder: str, q: str = "", limit: int = 50, offset: int = 0) -> list[dict]:
    """Messages of one mailbox. `folder` is inbox | sent | drafts, or the virtual views starred | trash."""
    box = _box_by_local(local, user)
    limit = max(1, min(int(limit), MAX_PAGE))
    offset = max(0, int(offset))
    # Drafts have no received/sent time (show last edit); trash orders by when it was deleted.
    if folder == "drafts":
        ts = "m.updated_at"
    elif folder == "trash":
        ts = "m.deleted_at"
    else:
        ts = "coalesce(m.received_at, m.sent_at, m.created_at)"
    sql = f"""select m.email_message_id as id, %s::text as mailbox, m.folder, coalesce(m.from_name,'') as from_name,
                    m.from_address, m.subject, coalesce(m.snippet,'') as snippet, m.is_read, m.is_starred,
                    m.status::text as status, (m.deleted_at is not null) as deleted,
                    {ts} as created_at,
                    coalesce((select jsonb_agg(r.email_address order by r.email_recipient_id) from email_recipients r
                              where r.email_message_id = m.email_message_id and r.recipient_type = 'to'), '[]'::jsonb) as to_addrs
             from email_messages m
             where m.mailbox_id = %s"""
    args: list = [local, box["mailbox_id"]]
    if folder == "trash":
        sql += " and m.deleted_at is not null"
    elif folder == "starred":
        sql += " and m.is_starred and m.deleted_at is null and m.folder in ('inbox','sent')"
    else:
        sql += " and m.folder = %s and m.deleted_at is null"
        args.append(folder)

    text, ops = parse_search(q)
    for op, value in ops:
        if op == "from":
            sql += " and (m.from_address ilike %s or m.from_name ilike %s)"
            args += [_like(value)] * 2
        elif op == "to":
            sql += " and exists (select 1 from email_recipients r where r.email_message_id = m.email_message_id and r.recipient_type = 'to' and r.email_address ilike %s)"
            args.append(_like(value))
        elif op == "subject":
            sql += " and m.subject ilike %s"
            args.append(_like(value))
        elif op == "is":
            flag = value.lower()
            if flag == "unread":
                sql += " and not m.is_read"
            elif flag == "read":
                sql += " and m.is_read"
            elif flag == "starred":
                sql += " and m.is_starred"
    if text:
        sql += """ and (m.subject ilike %s or m.from_address ilike %s or m.from_name ilike %s or m.text_body ilike %s
                   or exists (select 1 from email_recipients r where r.email_message_id = m.email_message_id
                              and r.email_address ilike %s))"""
        args += [_like(text)] * 5
    sql += f" order by {ts} desc, m.email_message_id desc limit %s offset %s"
    args += [limit, offset]
    with conn() as c:
        return c.execute(sql, args).fetchall()


def get_message(user, msg_id: int) -> dict | None:
    allowed = {r["mailbox_id"] for r in list_mailboxes(user)}
    with conn() as c:
        row = c.execute(
            """select m.email_message_id as id, mb.local_part as mailbox, m.mailbox_id, m.folder, coalesce(m.from_name,'') as from_name,
                      m.from_address, m.subject, m.text_body, m.html_body, m.is_read, m.message_id,
                      m.is_starred, m.status::text as status, (m.deleted_at is not null) as deleted,
                      coalesce(m.received_at, m.sent_at, m.created_at) as created_at
               from email_messages m join mailboxes mb using (mailbox_id)
               where m.email_message_id = %s""",
            (msg_id,),
        ).fetchone()
        if not row or row["mailbox_id"] not in allowed:
            return None
        for kind in ("to", "cc", "bcc"):
            row[kind + "_addrs"] = [r["email_address"] for r in c.execute(
                "select email_address from email_recipients where email_message_id=%s and recipient_type=%s::mail_recipient_type"
                " order by email_recipient_id",
                (msg_id, kind),
            ).fetchall()]
        if row["folder"] == "inbox" and not row["is_read"] and not row["deleted"]:
            c.execute("update email_messages set is_read=true, updated_at=now() where email_message_id=%s", (msg_id,))
            row["is_read"] = True
    row.pop("mailbox_id")
    return row


def split_addrs(value: str) -> list[str]:
    """Parse a comma/semicolon/newline separated address list; validates, de-duplicates, preserves order."""
    out: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,;\n]+", value or ""):
        part = part.strip()
        if not part:
            continue
        addr = parseaddr(part)[1]
        if not EMAIL_RE.match(addr):
            raise ValueError(f"Invalid email address: {part}")
        if addr.lower() not in seen:
            seen.add(addr.lower())
            out.append(addr)
    if len(out) > MAX_RECIPIENTS:
        raise ValueError(f"Too many recipients (maximum {MAX_RECIPIENTS} per field)")
    return out


def text_to_html(text: str) -> str:
    body = htmllib.escape(text).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
    return f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5">{body}</div>'


def send_mail(user, local: str, to: str, cc: str, bcc: str, subject: str, body: str, reply_to_id=None,
              draft_id: int | None = None) -> int:
    try:
        box = _box_by_local(local, user, sending=True)
    except LookupError:
        raise ValueError("Unknown mailbox")
    to_l, cc_l, bcc_l = split_addrs(to), split_addrs(cc), split_addrs(bcc)
    if not to_l:
        raise ValueError("Add at least one recipient")
    if not subject.strip() and not body.strip():
        raise ValueError("Write a subject or a message first")

    label = box["label"]
    # Collapse any CR/LF so a subject can never inject extra mail headers.
    subject = re.sub(r"\s+", " ", subject).strip()[:MAX_SUBJECT_LEN] or "(no subject)"
    payload = {
        "from": formataddr((label, box["address"])),
        "to": to_l,
        "subject": subject,
        "text": body,
        "html": text_to_html(body),
    }
    if cc_l:
        payload["cc"] = cc_l
    if bcc_l:
        payload["bcc"] = bcc_l

    parent = None
    if reply_to_id:
        parent = get_message(user, int(reply_to_id))
    elif draft_id:
        # A reply saved as a draft keeps its threading headers.
        in_reply_to = _draft_in_reply_to(user, draft_id)
        if in_reply_to:
            parent = {"message_id": in_reply_to}
    if parent and parent.get("message_id"):
        payload["headers"] = {
            "In-Reply-To": parent["message_id"],
            "References": parent["message_id"],
        }

    sent = resend_client.send(payload)

    try:
        mid = _store_sent(box, label, subject, body, payload["html"], sent.get("id"), parent, to_l, cc_l, bcc_l)
    except Exception as exc:
        # The email already went out; make sure that is visible in the logs and to the caller.
        # The draft is deliberately kept so the text is not lost.
        log.exception("Email %s was sent but could not be saved to Sent", sent.get("id"))
        raise SentNotSaved("Message was sent, but it could not be saved to the Sent folder") from exc

    if draft_id:
        try:
            delete_draft(user, draft_id)
        except Exception:
            log.warning("Could not remove draft %s after sending", draft_id, exc_info=True)
    return mid


def _store_sent(box, label, subject, body, html, resend_id, parent, to_l, cc_l, bcc_l) -> int:
    parent_msg_id = (parent or {}).get("message_id")
    with conn() as c:
        row = c.execute(
            """insert into email_messages
               (mailbox_id, direction, status, in_reply_to, references_header, from_address, from_name, subject, snippet,
                text_body, html_body, is_read, folder, sent_at, resend_email_id)
               values (%s,'outbound','sent',%s,%s,%s,%s,%s,%s,%s,%s,true,'sent',now(),%s)
               returning email_message_id""",
            (
                box["mailbox_id"],
                parent_msg_id,
                parent_msg_id,
                box["address"],
                label,
                subject,
                make_snippet(body, ""),
                body,
                html,
                resend_id,
            ),
        ).fetchone()
        mid = row["email_message_id"]
        for kind, addrs in (("to", to_l), ("cc", cc_l), ("bcc", bcc_l)):
            for a in addrs:
                c.execute(
                    "insert into email_recipients (email_message_id, recipient_type, email_address)"
                    " values (%s, %s::mail_recipient_type, %s)",
                    (mid, kind, a.lower()),
                )
    return mid


# --- Drafts -----------------------------------------------------------------
# Drafts live in email_messages (status 'draft', folder 'drafts'); their recipients use email_recipients.
# No schema changes: both values already exist in the schema.

def _lenient_addrs(value: str) -> list[str]:
    """Like split_addrs, but silently skips incomplete addresses so half-typed fields can still be autosaved."""
    out: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,;\n]+", value or ""):
        addr = parseaddr(part.strip())[1]
        if addr and EMAIL_RE.match(addr) and addr.lower() not in seen:
            seen.add(addr.lower())
            out.append(addr)
    return out[:MAX_RECIPIENTS]


def _accessible_ids(user) -> list[int]:
    return [r["mailbox_id"] for r in list_mailboxes(user)]


def _draft_in_reply_to(user, draft_id: int) -> str | None:
    with conn() as c:
        row = c.execute(
            "select in_reply_to from email_messages where email_message_id = %s and folder = 'drafts'"
            " and status = 'draft' and mailbox_id = any(%s)",
            (int(draft_id), _accessible_ids(user)),
        ).fetchone()
    return row["in_reply_to"] if row else None


def save_draft(user, local: str, to: str, cc: str, bcc: str, subject: str, body: str,
               draft_id: int | None = None, reply_to_id: int | None = None) -> dict:
    """Create a draft, or update `draft_id` in place. Raises LookupError for an unknown mailbox/draft."""
    box = _box_by_local(local, user)
    to_l, cc_l, bcc_l = _lenient_addrs(to), _lenient_addrs(cc), _lenient_addrs(bcc)
    subject = re.sub(r"\s+", " ", subject or "").strip()[:MAX_SUBJECT_LEN]
    body = body or ""
    if not draft_id and not (to_l or cc_l or bcc_l or subject or body.strip()):
        return {"id": None, "saved_at": None}   # never create empty drafts

    snippet = make_snippet(body, "")
    with conn() as c:
        if draft_id:
            row = c.execute(
                """update email_messages set mailbox_id = %s, from_address = %s, from_name = %s, subject = %s,
                          snippet = %s, text_body = %s, updated_at = now()
                   where email_message_id = %s and folder = 'drafts' and status = 'draft' and deleted_at is null
                     and mailbox_id = any(%s)
                   returning email_message_id, updated_at""",
                (box["mailbox_id"], box["address"], box["label"], subject, snippet, body,
                 int(draft_id), _accessible_ids(user)),
            ).fetchone()
            if not row:
                raise LookupError("Draft not found")
            c.execute("delete from email_recipients where email_message_id = %s", (row["email_message_id"],))
        else:
            parent_msg_id = None
            if reply_to_id:
                parent = get_message(user, int(reply_to_id))
                parent_msg_id = (parent or {}).get("message_id")
            row = c.execute(
                """insert into email_messages
                   (mailbox_id, direction, status, in_reply_to, references_header, from_address, from_name, subject,
                    snippet, text_body, is_read, folder)
                   values (%s,'outbound','draft',%s,%s,%s,%s,%s,%s,%s,true,'drafts')
                   returning email_message_id, updated_at""",
                (box["mailbox_id"], parent_msg_id, parent_msg_id, box["address"], box["label"], subject, snippet, body),
            ).fetchone()
        mid = row["email_message_id"]
        for kind, addrs in (("to", to_l), ("cc", cc_l), ("bcc", bcc_l)):
            for a in addrs:
                c.execute(
                    "insert into email_recipients (email_message_id, recipient_type, email_address)"
                    " values (%s, %s::mail_recipient_type, %s)",
                    (mid, kind, a.lower()),
                )
    return {"id": mid, "saved_at": row["updated_at"]}


def delete_draft(user, draft_id: int) -> bool:
    """Permanently discard a draft the user can access. Returns False if it does not exist."""
    with conn() as c:
        row = c.execute(
            "delete from email_messages where email_message_id = %s and folder = 'drafts' and status = 'draft'"
            " and mailbox_id = any(%s) returning email_message_id",
            (int(draft_id), _accessible_ids(user)),
        ).fetchone()
    return bool(row)


def draft_counts(user) -> dict:
    with conn() as c:
        rows = c.execute(
            """select mb.local_part, count(*) as n from email_messages m join mailboxes mb using (mailbox_id)
               where m.folder = 'drafts' and m.deleted_at is null and m.mailbox_id = any(%s)
               group by mb.local_part""",
            (_accessible_ids(user),),
        ).fetchall()
    return {r["local_part"]: r["n"] for r in rows}


# --- Gmail-style message actions (stars, trash, read state) --------------------------------------
# Uses only columns that already exist on email_messages: is_read, is_starred, deleted_at.

_ACTION_SQL = {
    # action: (SQL "set" clause + extra where) ; every statement is also scoped to the user's mailboxes
    "read": "update email_messages set is_read = true, updated_at = now() where folder = 'inbox' and deleted_at is null and not is_read",
    "unread": "update email_messages set is_read = false, updated_at = now() where folder = 'inbox' and deleted_at is null and is_read",
    "star": "update email_messages set is_starred = true, updated_at = now() where folder in ('inbox','sent') and not is_starred",
    "unstar": "update email_messages set is_starred = false, updated_at = now() where folder in ('inbox','sent') and is_starred",
    "trash": "update email_messages set deleted_at = now(), updated_at = now() where folder in ('inbox','sent') and deleted_at is null",
    "restore": "update email_messages set deleted_at = null, updated_at = now() where deleted_at is not null",
    "delete_forever": "delete from email_messages where deleted_at is not null",
}


def update_messages(user, ids: list[int], action: str) -> int:
    """Apply `action` to the given messages the user can access. Returns how many actually changed.

    Drafts are never touched here (use delete_draft). delete_forever only removes messages already in Trash.
    """
    if action not in _ACTION_SQL:
        raise ValueError("Unknown action")
    ids = list({int(i) for i in ids})
    if not ids:
        return 0
    if len(ids) > MAX_PAGE:
        raise ValueError(f"Too many messages selected (maximum {MAX_PAGE})")
    sql = _ACTION_SQL[action] + " and email_message_id = any(%s) and mailbox_id = any(%s) returning email_message_id"
    with conn() as c:
        return len(c.execute(sql, (ids, _accessible_ids(user))).fetchall())


def empty_trash(user, local: str) -> int:
    box = _box_by_local(local, user)
    with conn() as c:
        rows = c.execute(
            "delete from email_messages where mailbox_id = %s and deleted_at is not null returning email_message_id",
            (box["mailbox_id"],),
        ).fetchall()
    return len(rows)


# --- Delivery status (Resend webhook events for mail we sent) --------------------------------------
_DELIVERY_EVENTS = {
    "email.delivered": "delivered",
    "email.bounced": "bounced",
    "email.complained": "complained",
    "email.failed": "failed",
}


def apply_delivery_event(event_type: str, resend_email_id: str | None, event_id: str | None = None) -> int:
    """Record delivered/bounced/... on the matching sent message. Bad outcomes always win; a late
    'delivered' never overwrites a bounce. Returns the number of messages updated (0 if unknown)."""
    status = _DELIVERY_EVENTS.get(event_type)
    if not status or not resend_email_id:
        return 0
    with conn() as c:
        rows = c.execute(
            """update email_messages set status = %s::mail_message_status, resend_event_id = %s, updated_at = now()
               where resend_email_id = %s and direction = 'outbound'
                 and (status::text in ('sent','queued','delivered') or %s in ('bounced','complained','failed'))
               returning email_message_id""",
            (status, event_id, resend_email_id, status),
        ).fetchall()
    return len(rows)


# --- Mail lock: append-only archive and compliance report -------------------------------------------
_COMPLIANCE_TTL = 300
_compliance_cache: dict = {"at": 0.0, "data": None}


def archive_search(q: str = "", mailbox: str = "", limit: int = 50, offset: int = 0) -> list[dict]:
    """Admin-only. Lists the append-only archive (it still holds mail deleted from the mailboxes)."""
    sql = """select a.archive_id, a.email_message_id, a.mailbox_address, a.direction, a.from_address, a.from_name, a.subject,
                    coalesce(a.received_at, a.sent_at, a.archived_at) as ts,
                    left(coalesce(a.text_body, ''), 160) as snippet,
                    exists (select 1 from email_messages m where m.email_message_id = a.email_message_id and m.deleted_at is null) as in_mailbox
             from mail_archive a where true"""
    args: list = []
    if mailbox:
        sql += " and a.mailbox_address = %s"
        args.append(f"{mailbox.lower()}@{config.MAIL_DOMAIN}")
    q = (q or "").strip()
    if q:
        sql += """ and (a.subject ilike %s or a.from_address ilike %s or a.from_name ilike %s or a.text_body ilike %s
                   or exists (select 1 from mail_archive_recipients r where r.email_message_id = a.email_message_id and r.email_address ilike %s))"""
        args += [_like(q)] * 5
    sql += " order by ts desc, a.archive_id desc limit %s offset %s"
    args += [min(max(int(limit), 1), MAX_PAGE), max(int(offset), 0)]
    with conn() as c:
        return c.execute(sql, args).fetchall()


def archive_get(archive_id: int) -> dict | None:
    with conn() as c:
        row = c.execute("select * from mail_archive where archive_id = %s", (archive_id,)).fetchone()
        if not row:
            return None
        rec = c.execute(
            "select recipient_type, email_address, display_name from mail_archive_recipients where email_message_id = %s order by archive_recipient_id",
            (row["email_message_id"],),
        ).fetchall()
    row["to_addrs"] = [r for r in rec if r["recipient_type"] == "to"]
    row["cc_addrs"] = [r for r in rec if r["recipient_type"] == "cc"]
    row["bcc_addrs"] = [r for r in rec if r["recipient_type"] == "bcc"]
    return row


def compliance_report(force: bool = False) -> dict:
    """DNS 'mail lock' check plus what this application itself guarantees. Cached for five minutes."""
    from . import compliance

    now = time.monotonic()
    if not force and _compliance_cache["data"] and now - _compliance_cache["at"] < _COMPLIANCE_TTL:
        return _compliance_cache["data"]
    try:
        dns = compliance.fetch_dns(config.MAIL_DOMAIN)
        report = compliance.evaluate(config.MAIL_DOMAIN, dns["mx"], dns["spf"], dns["dmarc"], dns["dkim"], config.ALLOWED_MX)
    except Exception as exc:  # network/DNS problem: say so instead of pretending everything is fine
        log.warning("DNS check failed: %s", exc)
        report = {"domain": config.MAIL_DOMAIN, "status": "warning", "mx": [], "checked_at": int(time.time()),
                  "checks": [compliance._check("dns", "DNS look-up", "warning", "Could not read DNS right now. Try again in a minute.")]}

    with conn() as c:
        off = c.execute("select count(*) n from mailboxes where email_address not like %s", ("%@" + config.MAIL_DOMAIN,)).fetchone()["n"]
        arch = c.execute("select count(*) n, max(archived_at) last from mail_archive").fetchone()
        live = c.execute("select count(*) n from email_messages where status <> 'draft'").fetchone()["n"]
    app = report["checks"]
    app.append(compliance._check("mailboxes", "Mailboxes only on your domain", "ok" if off == 0 else "fail",
                                 "Every mailbox is an @" + config.MAIL_DOMAIN + " address." if off == 0 else f"{off} mailbox(es) use another domain."))
    app.append(compliance._check("catchall", "Unknown addresses", "ok",
                                 "Mail to any other @" + config.MAIL_DOMAIN + " address is kept in the admin mailbox, not lost."))
    app.append(compliance._check("archive", "Permanent archive", "ok" if arch["n"] >= live else "warning",
                                 f"{arch['n']} messages archived (append-only; survives deleting from a mailbox)."))
    app.append(compliance._check("resend", "Receiving connected", "ok" if config.RESEND_API_KEY and config.RESEND_WEBHOOK_SECRET else "warning",
                                 "Resend key and webhook secret are set." if config.RESEND_API_KEY and config.RESEND_WEBHOOK_SECRET
                                 else "RESEND_API_KEY and/or RESEND_WEBHOOK_SECRET is missing on the server."))
    order = {"fail": 2, "warning": 1, "ok": 0}
    report["status"] = max((c["status"] for c in app), key=lambda s: order[s])
    _compliance_cache.update(at=now, data=report)
    return report
