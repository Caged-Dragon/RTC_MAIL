import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from . import auth, config, resend_client, service
from .db import conn

router = APIRouter(prefix="/api")


class SendIn(BaseModel):
    mailbox: str
    to: str
    cc: str = ""
    bcc: str = ""
    subject: str = ""
    body: str = ""
    reply_to_id: int | None = None


class LoginIn(BaseModel):
    email: str
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


def _resend_http_error(exc: resend_client.ResendError) -> HTTPException:
    return HTTPException(400 if exc.status == 400 else 502, f"Resend: {exc}")


# ---------- auth (public) ----------

@router.post("/auth/login")
def login(body: LoginIn):
    return auth.password_login(body.email.strip(), body.password)


@router.post("/auth/refresh")
def refresh(body: RefreshIn):
    return auth.refresh(body.refresh_token)


# ---------- mail (signed-in users only) ----------

@router.get("/theme")
def get_theme():
    # Public read is safe because only visual tokens are returned. Admin writes are protected by RLS.
    with conn() as c:
        row = c.execute("select * from public.theme_page_settings where website_key='mail' and page_key='inbox' and is_active=true limit 1").fetchone()
        components = c.execute("select * from public.theme_component_settings where website_key='mail' and page_key='inbox' and is_active=true order by sort_order").fetchall()
    return {**(row or {}), 'components': components}

@router.get("/config")
def get_config(user: auth.User = Depends(auth.current_user)):
    service.ensure_mailboxes()
    boxes = service.list_mailboxes(user)
    return {
        "domain": config.MAIL_DOMAIN,
        "user": user.email,
        "mailboxes": [{"id": b["local_part"], "label": b["label"], "address": b["address"],
                       "can_send": b["is_sending_enabled"]} for b in boxes],
        "resend_configured": bool(config.RESEND_API_KEY),
    }


@router.get("/counts")
def counts(user: auth.User = Depends(auth.current_user)):
    return service.counts(user)


@router.get("/messages")
def list_messages(mailbox: str, folder: str = "inbox", q: str = "", user: auth.User = Depends(auth.current_user)):
    if folder not in ("inbox", "sent"):
        raise HTTPException(400, "Unknown folder")
    try:
        return service.list_messages(user, mailbox, folder, q)
    except LookupError:
        raise HTTPException(400, "Unknown mailbox")


@router.get("/messages/{msg_id}")
def get_message(msg_id: int, user: auth.User = Depends(auth.current_user)):
    msg = service.get_message(user, msg_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    return msg


@router.post("/send")
def send(body: SendIn, user: auth.User = Depends(auth.current_user)):
    try:
        msg_id = service.send_mail(user, body.mailbox, body.to, body.cc, body.bcc,
                                   body.subject, body.body, body.reply_to_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except resend_client.ResendError as exc:
        raise _resend_http_error(exc)
    return {"id": msg_id}


@router.post("/sync")
def sync(full: bool = False, user: auth.User = Depends(auth.current_user)):
    try:
        return service.sync_inbox(full_scan=full)
    except resend_client.ResendError as exc:
        raise _resend_http_error(exc)


@router.post("/webhooks/resend")
async def resend_webhook(request: Request):
    """Live delivery from Resend (event `email.received`). The signing secret is mandatory."""
    if not config.RESEND_WEBHOOK_SECRET:
        raise HTTPException(503, "Webhook disabled: set RESEND_WEBHOOK_SECRET")
    raw = await request.body()
    try:
        from svix.webhooks import Webhook
        wanted = {"svix-id", "svix-timestamp", "svix-signature"}
        headers = {k: v for k, v in request.headers.items() if k.lower() in wanted}
        Webhook(config.RESEND_WEBHOOK_SECRET).verify(raw, headers)
    except Exception:
        raise HTTPException(401, "Invalid webhook signature")
    try:
        event = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "Invalid JSON")
    if event.get("type") != "email.received":
        return {"ignored": True}
    data = event.get("data") or {}
    item = {**data, "id": data.get("email_id")}
    try:
        result = await run_in_threadpool(service.ingest_received, item)
    except resend_client.ResendError as exc:
        raise _resend_http_error(exc)
    if result.get("failed"):
        raise HTTPException(502, "Could not download the message; Resend will retry")
    return {"received": True, **result}
