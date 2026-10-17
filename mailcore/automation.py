"""Automatic e-mail: ready-made templates, the send queue and event-driven mail.

Flow: something happens (order placed, status changed, sign-up, support form, mail arriving at support@)
  -> a row is queued in `mail_outbox` (by database triggers, or by `enqueue`)
  -> `process_outbox` renders the template with fresh data and sends it through Resend
  -> the sent copy is stored in the mailbox (Sent folder, archive) like any other mail.
Everything that sends mail from the company's addresses therefore lives in this one system."""
import hmac
import json
import logging
import re
import time
import uuid
from email.utils import formataddr

from . import config, resend_client, service, templating
from .db import conn

log = logging.getLogger(__name__)

RAW_BLOCKS = frozenset({"items_html", "message_html"})
MAX_ATTEMPTS = 6
BACKOFF_SECONDS = [60, 300, 1800, 7200, 21600]            # after attempt 1..5
NON_RETRYABLE = {400, 401, 403, 422}                      # a retry cannot fix these (bad key, bad address, bad content)
CATEGORIES = {"orders", "support", "account", "sales", "billing", "general"}
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{2,60}$")
NO_REPLY_LOCALS = {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster", "bounce", "bounces", "notifications", "notification", "alerts"}
AUTO_SUBJECT = re.compile(r"^\s*(auto(matic)?[ :-]*(reply|response)?|out of office|undeliverable|delivery status|mail delivery|returned mail|failure notice)", re.I)

_company_cache: dict = {"at": 0.0, "data": None}


# --- Templates ----------------------------------------------------------------------------------------
def list_templates() -> list[dict]:
    with conn() as c:
        return c.execute(
            """select template_key, name, category, mailbox_local_part, subject, is_active, is_system, updated_at,
                      (select count(*) from mail_outbox o where o.template_key = t.template_key and o.status = 'sent') as sent_count
               from mail_templates t order by category, name"""
        ).fetchall()


def get_template(key: str) -> dict | None:
    with conn() as c:
        return c.execute("select * from mail_templates where template_key = %s", (key,)).fetchone()


def _check_fields(data: dict) -> dict:
    out = {
        "name": str(data.get("name", "")).strip(),
        "category": str(data.get("category", "general")).strip(),
        "mailbox_local_part": str(data.get("mailbox_local_part", "noreply")).strip().lower(),
        "subject": str(data.get("subject", "")),
        "body_html": str(data.get("body_html", "")),
        "body_text": str(data.get("body_text", "")),
        "is_active": bool(data.get("is_active", True)),
    }
    problems = templating.validate(out["subject"], out["body_html"], out["body_text"])
    if not out["name"] or len(out["name"]) > 120:
        problems.append("Give the template a name (up to 120 characters).")
    if out["category"] not in CATEGORIES:
        problems.append("Unknown category.")
    if len(out["subject"]) > 300 or len(out["body_html"]) > 100_000 or len(out["body_text"]) > 50_000:
        problems.append("The template is too long.")
    if out["mailbox_local_part"] not in {m["local_part"] for m in service.list_mailboxes(None)}:
        problems.append("Choose one of your existing mailboxes as the sender.")
    if problems:
        raise ValueError(" ".join(problems))
    return out


def save_template(key: str, data: dict, create: bool = False) -> dict:
    if not KEY_RE.match(key or ""):
        raise ValueError("The key must be lower-case letters, digits and underscores, starting with a letter.")
    f = _check_fields(data)
    with conn() as c:
        if create:
            if c.execute("select 1 from mail_templates where template_key = %s", (key,)).fetchone():
                raise ValueError("A template with this key already exists.")
            variables = json.dumps(data.get("variables") if isinstance(data.get("variables"), list) else [])
            return c.execute(
                """insert into mail_templates (template_key, name, category, mailbox_local_part, subject, body_html, body_text, variables, is_active)
                   values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) returning *""",
                (key, f["name"], f["category"], f["mailbox_local_part"], f["subject"], f["body_html"], f["body_text"], variables, f["is_active"]),
            ).fetchone()
        row = c.execute(
            """update mail_templates set name=%s, category=%s, mailbox_local_part=%s, subject=%s, body_html=%s, body_text=%s, is_active=%s
               where template_key=%s returning *""",
            (f["name"], f["category"], f["mailbox_local_part"], f["subject"], f["body_html"], f["body_text"], f["is_active"], key),
        ).fetchone()
    if not row:
        raise LookupError("Template not found")
    return row


def delete_template(key: str) -> None:
    with conn() as c:
        row = c.execute("select is_system from mail_templates where template_key = %s", (key,)).fetchone()
        if not row:
            raise LookupError("Template not found")
        if row["is_system"]:
            raise ValueError("Built-in templates cannot be deleted. Switch them off instead.")
        if c.execute("select 1 from mail_outbox where template_key = %s limit 1", (key,)).fetchone():
            raise ValueError("This template has been used, so it is kept for the record. Switch it off instead.")
        c.execute("delete from mail_templates where template_key = %s", (key,))


# --- Rendering ----------------------------------------------------------------------------------------
def company_vars() -> dict:
    """Company name/phone/email for every mail (cached five minutes)."""
    now = time.monotonic()
    if _company_cache["data"] and now - _company_cache["at"] < 300:
        return _company_cache["data"]
    data = {"company_name": "RT Crackers", "company_phone": "", "company_email": f"support@{config.MAIL_DOMAIN}"}
    try:
        with conn() as c:
            r = c.execute("select company_name, mobile, whatsapp_number, support_email, email from company_profile where is_active limit 1").fetchone()
            ms = c.execute("select support_phone from merchant_settings limit 1").fetchone()
        if r:
            data["company_name"] = r.get("company_name") or data["company_name"]
            data["company_phone"] = (ms or {}).get("support_phone") or r.get("mobile") or r.get("whatsapp_number") or ""
            data["company_email"] = r.get("support_email") or r.get("email") or data["company_email"]
    except Exception:  # noqa: BLE001 - mail must still go out with defaults
        log.warning("Could not read company profile for e-mail footer", exc_info=True)
    data.update(site_url=config.CART_URL, shop_url=config.CART_URL, year=str(time.gmtime().tm_year))
    _company_cache.update(at=now, data=data)
    return data


def _finish_vars(variables: dict) -> dict:
    v = {**company_vars(), **{k: ("" if x is None else x) for k, x in variables.items()}}
    if "message" in v and "message_html" not in variables:
        v["message_html"] = templating.nl2br(v["message"])
    if "status" in v and "status_label" not in variables:
        v["status_label"] = templating.STATUS_LABELS.get(str(v["status"]), str(v["status"]).replace("_", " ").title())
        v.setdefault("status_message", templating.STATUS_MESSAGES.get(str(v["status"]), ""))
    return v


def compose(template: dict, variables: dict) -> tuple[str, str, str]:
    """-> (subject, full html, plain text)."""
    v = _finish_vars(variables)
    subject = templating.render_subject(template["subject"], v)
    inner = templating.render(template["body_html"], v, html_mode=True, raw_allowed=RAW_BLOCKS)
    page = templating.wrap_layout(inner, company=v["company_name"], phone=v["company_phone"], email=v["company_email"], site_url=v["site_url"])
    text = templating.render(template["body_text"] or "", v, html_mode=False).strip() or service.make_snippet("", inner)
    return subject, page, text


def sample_variables(template: dict) -> dict:
    out = {}
    for item in template.get("variables") or []:
        if isinstance(item, dict) and item.get("name"):
            out[item["name"]] = item.get("example", "")
    if "items_html" in out:
        sample = [{"name": "Flower Pots Deluxe", "quantity": 2, "line_total": 900}, {"name": "Electric Sparklers 10cm", "quantity": 5, "line_total": 3000}]
        out["items_html"], out["items_text"] = templating.items_html(sample), templating.items_text(sample)
    return out


def preview(template: dict, variables: dict | None = None) -> dict:
    v = {**sample_variables(template), **(variables or {})}
    subject, page, text = compose(template, v)
    missing = sorted(n for n in templating.used_names(template["subject"], template["body_html"], template["body_text"] or "")
                     if n not in _finish_vars(v) and n != "if")
    return {"subject": subject, "html": page, "text": text, "missing": missing}


# --- Event data ---------------------------------------------------------------------------------------
def _order_context(order_id: str) -> dict:
    with conn() as c:
        o = c.execute(
            """select o.id, o.order_number, o.status::text as status, o.final_net_payable, o.payment_method, o.guest_tracking_token, o.lr_number,
                      o.shipping_first_name, o.shipping_last_name, o.shipping_address_line_1, o.shipping_address_line_2, o.shipping_landmark,
                      o.shipping_city, o.shipping_district, o.shipping_state, o.shipping_postal_code, t.transporter_name
               from orders o left join transporters t on t.id = o.lorry_transporter_id where o.id = %s""", (order_id,)).fetchone()
        if not o:
            raise LookupError("Order not found")
        items = c.execute(
            "select coalesce(product_name_snapshot, product_name) as name, quantity, coalesce(line_total_snapshot, line_total) as line_total "
            "from order_items where order_id = %s order by id", (order_id,)).fetchall()
    address = ", ".join(str(x) for x in (o["shipping_address_line_1"], o["shipping_address_line_2"], o["shipping_landmark"], o["shipping_city"],
                                         o["shipping_district"], o["shipping_state"], o["shipping_postal_code"]) if x)
    pay = {"COD": "Cash on Delivery"}.get(str(o["payment_method"] or "").upper(), o["payment_method"] or "Cash on Delivery")
    return {
        "customer_name": o["shipping_first_name"] or "there", "order_number": o["order_number"], "status": o["status"],
        "order_total": templating.format_inr(o["final_net_payable"]), "payment_method": pay, "shipping_address": address,
        "items_html": templating.items_html(items), "items_text": templating.items_text(items),
        "lr_number": o["lr_number"] or "", "transporter_name": o["transporter_name"] or "",
        "tracking_url": f"{config.CART_URL}/track-order?n={o['order_number']}&t={o['guest_tracking_token']}" if o["guest_tracking_token"] else f"{config.CART_URL}/track-order",
        "review_url": f"{config.CART_URL}/reviews",
    }


def build_context(row: dict) -> dict:
    stored = dict(row["variables"] or {})
    ctx = {}
    if stored.get("order_id"):
        ctx.update(_order_context(stored["order_id"]))
        if stored.get("status"):                      # the status that triggered THIS mail, not the order's current one
            ctx["status"] = stored["status"]
    ctx.update({k: v for k, v in stored.items() if k not in ("order_id",) and not (k == "status" and ctx.get("status"))})
    ctx.setdefault("customer_name", row.get("to_name") or "there")
    return ctx


# --- Sending ------------------------------------------------------------------------------------------
def _send_row(row: dict) -> int:
    template = get_template(row["template_key"])
    if not template:
        raise LookupError("Template no longer exists")
    ctx = build_context(row)
    subject, page, text = compose(template, ctx)
    box = service._box_by_local(template["mailbox_local_part"], None, sending=True)
    payload = {"from": formataddr((box["label"], box["address"])), "to": [row["to_email"]], "subject": subject, "html": page, "text": text,
               "headers": {"Auto-Submitted": "auto-generated", "X-Auto-Response-Suppress": "All"}}
    reply_to = str(ctx.get("reply_to") or "").strip()
    if reply_to and service.EMAIL_RE.match(reply_to):
        payload["reply_to"] = [reply_to]
    # Same outbox row -> same key: if we crash after Resend accepted it, the retry cannot deliver a duplicate.
    sent = resend_client.send(payload, idempotency_key=f"rtc-outbox-{row['outbox_id']}")
    return service._store_sent(box, box["label"], subject, text, page, sent.get("id"), None, [row["to_email"]], [], [])


def _claim(limit: int, only_id: int | None = None) -> list[dict]:
    with conn() as c:
        if only_id is not None:
            return c.execute(
                """update mail_outbox set status='sending', locked_until=now() + interval '3 minutes', attempts=attempts+1
                   where outbox_id = %s and status in ('queued','failed') returning *""", (only_id,)).fetchall()
        return c.execute(
            """update mail_outbox set status='sending', locked_until=now() + interval '3 minutes', attempts=attempts+1
               where outbox_id in (select outbox_id from mail_outbox
                                   where (status='queued' or (status='sending' and locked_until < now()))
                                     and next_attempt_at <= now() and attempts < %s
                                   order by next_attempt_at, outbox_id limit %s for update skip locked)
               returning *""", (MAX_ATTEMPTS, limit)).fetchall()


def _finish_row(row: dict) -> str:
    """Send one claimed row and record the outcome. Returns 'sent' | 'retry' | 'failed'."""
    try:
        mid = _send_row(row)
    except resend_client.ResendError as exc:
        return _record_failure(row, f"Resend {exc.status}: {exc}", retryable=exc.status not in NON_RETRYABLE)
    except (LookupError, ValueError) as exc:
        return _record_failure(row, str(exc), retryable=False)
    except Exception as exc:  # noqa: BLE001
        log.exception("Outbox %s failed", row["outbox_id"])
        return _record_failure(row, f"{type(exc).__name__}: {exc}"[:500], retryable=True)
    with conn() as c:
        c.execute("update mail_outbox set status='sent', sent_at=now(), sent_message_id=%s, last_error=null, locked_until=null where outbox_id=%s", (mid, row["outbox_id"]))
    return "sent"


def _record_failure(row: dict, error: str, retryable: bool) -> str:
    attempts = row["attempts"]
    if retryable and attempts < MAX_ATTEMPTS:
        delay = BACKOFF_SECONDS[min(attempts, len(BACKOFF_SECONDS)) - 1]
        with conn() as c:
            c.execute("update mail_outbox set status='queued', locked_until=null, last_error=%s, next_attempt_at=now() + make_interval(secs => %s) where outbox_id=%s",
                      (error[:500], delay, row["outbox_id"]))
        return "retry"
    with conn() as c:
        c.execute("update mail_outbox set status='failed', locked_until=null, last_error=%s where outbox_id=%s", (error[:500], row["outbox_id"]))
    return "failed"


def process_outbox(limit: int = 20, budget_seconds: float = 25.0) -> dict:
    """Send everything that is due. Safe to call from many places at once (rows are claimed with SKIP LOCKED)."""
    deadline = time.monotonic() + budget_seconds
    totals = {"sent": 0, "retry": 0, "failed": 0, "remaining": 0}
    while time.monotonic() < deadline:
        rows = _claim(min(5, limit))
        if not rows:
            break
        for row in rows:
            totals[_finish_row(row)] += 1
        limit -= len(rows)
        if limit <= 0:
            break
    with conn() as c:
        totals["remaining"] = c.execute("select count(*) n from mail_outbox where status in ('queued','sending') and next_attempt_at <= now() and attempts < %s", (MAX_ATTEMPTS,)).fetchone()["n"]
    return totals


def enqueue(template_key: str, to_email: str, to_name: str | None = None, variables: dict | None = None, related_type: str | None = None,
            related_id: str | None = None, key: str | None = None, delay_seconds: int = 0) -> int | None:
    with conn() as c:
        row = c.execute("select private.enqueue_mail(%s,%s,%s,%s::jsonb,%s,%s,%s, make_interval(secs => %s)) as id",
                        (template_key, to_email, to_name, json.dumps(variables or {}), related_type, related_id, key, delay_seconds)).fetchone()
    return row["id"]


def send_now(template_key: str, to_email: str, to_name: str | None, variables: dict | None) -> dict:
    """Administrator action: send a template to anyone right now (queued for the record, then sent immediately)."""
    if not service.EMAIL_RE.match((to_email or "").strip()):
        raise ValueError("Enter a valid recipient email address.")
    if not get_template(template_key):
        raise LookupError("Template not found")
    vars_ = {k: str(v)[:4000] for k, v in (variables or {}).items() if re.match(r"^[a-z_][a-z0-9_]*$", str(k)) and k != "order_id"}
    oid = enqueue(template_key, to_email.strip(), to_name, vars_, "manual", None, f"manual:{uuid.uuid4()}")
    if not oid:
        raise ValueError("The template is switched off or the address is invalid.")
    rows = _claim(1, only_id=oid)
    outcome = _finish_row(rows[0]) if rows else "failed"
    with conn() as c:
        row = c.execute("select status, last_error, sent_message_id from mail_outbox where outbox_id=%s", (oid,)).fetchone()
    return {"outbox_id": oid, "outcome": outcome, **row}


def list_outbox(status: str = "", limit: int = 100) -> list[dict]:
    sql = "select outbox_id, template_key, to_email, status, attempts, next_attempt_at, last_error, related_type, related_id, created_at, sent_at from mail_outbox"
    args: list = []
    if status in ("queued", "sending", "sent", "failed", "skipped"):
        sql += " where status = %s"
        args.append(status)
    sql += " order by outbox_id desc limit %s"
    args.append(min(max(int(limit), 1), 200))
    with conn() as c:
        return c.execute(sql, args).fetchall()


def retry_outbox(outbox_id: int) -> None:
    with conn() as c:
        n = c.execute("update mail_outbox set status='queued', attempts=0, next_attempt_at=now(), last_error=null, locked_until=null "
                      "where outbox_id=%s and status in ('failed','skipped')", (outbox_id,)).rowcount
    if not n:
        raise LookupError("Only failed messages can be retried")


def secret_ok(provided: str | None) -> bool:
    return bool(config.OUTBOX_SECRET and provided and hmac.compare_digest(provided, config.OUTBOX_SECRET))


# --- Support auto-reply for mail that arrives from the outside ----------------------------------------
def _looks_automatic(full: dict, from_addr: str, subject: str) -> bool:
    local, _, domain = from_addr.partition("@")
    if not from_addr or domain == config.MAIL_DOMAIN or local in NO_REPLY_LOCALS or any(local.startswith(p) for p in ("noreply", "no-reply", "donotreply")):
        return True
    if AUTO_SUBJECT.match(subject or ""):
        return True
    headers = full.get("headers") or {}
    if isinstance(headers, list):
        headers = {str(h.get("name", "")).lower(): h.get("value", "") for h in headers if isinstance(h, dict)}
    headers = {str(k).lower(): str(v).lower() for k, v in headers.items()}
    if headers.get("auto-submitted", "no") != "no" or headers.get("precedence") in ("bulk", "junk", "list", "auto_reply") or "list-unsubscribe" in headers \
            or headers.get("x-auto-response-suppress") or "x-autoreply" in headers:
        return True
    return False


def maybe_auto_reply(box: dict, full: dict, from_addr: str, name: str, message_id: int) -> bool:
    """When a customer emails support@ (or another configured mailbox) send one acknowledgement per sender per day."""
    if box.get("local_part") not in config.AUTO_REPLY_BOXES:
        return False
    sender = (from_addr or "").strip().lower()
    subject = full.get("subject") or ""
    if _looks_automatic(full, sender, subject):
        return False
    day = time.strftime("%Y-%m-%d", time.gmtime())
    oid = enqueue("support_auto_reply", sender, name or sender.split("@")[0],
                  {"customer_name": name or sender.split("@")[0], "subject": subject or "(no subject)", "reference": f"MAIL-{message_id}"},
                  "inbound", str(message_id), f"inbound_auto:{sender}:{day}")
    return bool(oid)


# --- Receiving self-test ------------------------------------------------------------------------------
def receiving_test_start() -> dict:
    """Send a mail from noreply@ to admin@ through Resend. If receiving is set up correctly it comes back in seconds."""
    token = uuid.uuid4().hex[:10]
    box = service._box_by_local("noreply", None, sending=True)
    target = f"admin@{config.MAIL_DOMAIN}"
    subject = f"Receiving test {token}"
    body = "This is an automatic test of your mail receiving. You can delete it."
    payload = {"from": formataddr((box["label"], box["address"])), "to": [target], "subject": subject, "text": body, "html": service.text_to_html(body),
               "headers": {"Auto-Submitted": "auto-generated"}}
    sent = resend_client.send(payload)
    service._store_sent(box, box["label"], subject, body, payload["html"], sent.get("id"), None, [target], [], [])
    return {"token": token, "to": target}


def receiving_test_check(token: str) -> dict:
    """Has the test mail come back through Resend's inbound side?"""
    if not re.fullmatch(r"[0-9a-f]{10}", token or ""):
        raise ValueError("Bad test token")
    try:
        page = resend_client.list_received(limit=50)
    except resend_client.ResendError as exc:
        if exc.status in (401, 403):
            return {"status": "key_problem", "message": "Your Resend API key is not allowed to read received mail (it looks like a send-only key). "
                    "Create a key with Full access in Resend, then update RESEND_API_KEY in Vercel and redeploy."}
        return {"status": "error", "message": f"Resend answered {exc.status}: {exc}"}
    for item in page.get("data") or []:
        if token in (item.get("subject") or ""):
            service.ensure_mailboxes()
            boxes = {r["address"]: r for r in service.list_mailboxes() if r["is_receiving_enabled"]}
            service.ingest_received(item, boxes)
            return {"status": "received", "message": "The test mail arrived and is now in the admin inbox. Receiving works."}
    return {"status": "waiting"}
