"""Mail logic on Supabase Postgres: discover mailboxes, pull received mail, send mail through Resend."""
import html as htmllib
import re
import time
from datetime import datetime, timezone
from email.utils import parseaddr

from . import config, resend_client
from .db import conn

EMAIL_RE = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")


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
            full = {**item, **resend_client.get_received(rid)}
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


def list_messages(user, local: str, folder: str, q: str = "") -> list[dict]:
    box = _box_by_local(local, user)
    sql = """select m.email_message_id as id, %s::text as mailbox, m.folder, coalesce(m.from_name,'') as from_name,
                    m.from_address, m.subject, coalesce(m.snippet,'') as snippet, m.is_read,
                    coalesce(m.received_at, m.sent_at, m.created_at) as created_at,
                    coalesce((select jsonb_agg(r.email_address order by r.email_recipient_id) from email_recipients r
                              where r.email_message_id = m.email_message_id and r.recipient_type = 'to'), '[]'::jsonb) as to_addrs
             from email_messages m
             where m.mailbox_id = %s and m.folder = %s and m.deleted_at is null"""
    args = [local, box["mailbox_id"], folder]
    if q.strip():
        sql += """ and (m.subject ilike %s or m.from_address ilike %s or m.from_name ilike %s or m.text_body ilike %s
                   or exists (select 1 from email_recipients r where r.email_message_id = m.email_message_id
                              and r.email_address ilike %s))"""
        args += [_like(q)] * 5
    sql += " order by coalesce(m.received_at, m.sent_at, m.created_at) desc, m.email_message_id desc limit 200"
    with conn() as c:
        return c.execute(sql, args).fetchall()


def get_message(user, msg_id: int) -> dict | None:
    allowed = {r["mailbox_id"] for r in list_mailboxes(user)}
    with conn() as c:
        row = c.execute(
            """select m.email_message_id as id, mb.local_part as mailbox, m.mailbox_id, m.folder, coalesce(m.from_name,'') as from_name,
                      m.from_address, m.subject, m.text_body, m.html_body, m.is_read, m.message_id,
                      coalesce(m.received_at, m.sent_at, m.created_at) as created_at
               from email_messages m join mailboxes mb using (mailbox_id)
               where m.email_message_id = %s and m.deleted_at is null""",
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
        if row["folder"] == "inbox" and not row["is_read"]:
            c.execute("update email_messages set is_read=true, updated_at=now() where email_message_id=%s", (msg_id,))
    row.pop("mailbox_id")
    return row


def split_addrs(value: str) -> list[str]:
    out = []
    for part in re.split(r"[,;\n]+", value or ""):
        part = part.strip()
        if not part:
            continue
        addr = parseaddr(part)[1]
        if not EMAIL_RE.match(addr):
            raise ValueError(f"Invalid email address: {part}")
        out.append(addr)
    return out


def text_to_html(text: str) -> str:
    body = htmllib.escape(text).replace("\r\n", "\n").replace("\n", "<br>")
    return f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5">{body}</div>'


def send_mail(user, local: str, to: str, cc: str, bcc: str, subject: str, body: str, reply_to_id=None) -> int:
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
    subject = subject.strip() or "(no subject)"
    payload = {
        "from": f"{label} <{box['address']}>",
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
        if parent and parent.get("message_id"):
            payload["headers"] = {
                "In-Reply-To": parent["message_id"],
                "References": parent["message_id"],
            }

    sent = resend_client.send(payload)

    with conn() as c:
        row = c.execute(
            """insert into email_messages
               (mailbox_id, direction, status, in_reply_to, references_header, from_address, from_name, subject, snippet,
                text_body, html_body, is_read, folder, sent_at, resend_email_id)
               values (%s,'outbound','sent',%s,%s,%s,%s,%s,%s,%s,%s,true,'sent',now(),%s)
               returning email_message_id""",
            (
                box["mailbox_id"],
                (parent or {}).get("message_id"),
                (parent or {}).get("message_id"),
                box["address"],
                label,
                subject,
                make_snippet(body, ""),
                body,
                payload["html"],
                sent.get("id"),
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
