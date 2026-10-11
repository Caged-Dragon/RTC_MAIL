import json
import logging
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from . import auth, config, resend_client, service
from .db import conn

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_WEBHOOK_HEADERS = {"svix-id", "svix-timestamp", "svix-signature"}
_PUBLIC_CACHE = "public, max-age=300, stale-while-revalidate=3600"


class SendIn(BaseModel):
    mailbox: str = Field(max_length=64)
    to: str = Field(max_length=5000)
    cc: str = Field(default="", max_length=5000)
    bcc: str = Field(default="", max_length=5000)
    subject: str = Field(default="", max_length=2000)
    body: str = Field(default="", max_length=500_000)
    reply_to_id: int | None = None
    draft_id: int | None = None


class DraftIn(BaseModel):
    mailbox: str = Field(max_length=64)
    to: str = Field(default="", max_length=5000)
    cc: str = Field(default="", max_length=5000)
    bcc: str = Field(default="", max_length=5000)
    subject: str = Field(default="", max_length=2000)
    body: str = Field(default="", max_length=500_000)
    reply_to_id: int | None = None
    draft_id: int | None = None


class BulkIn(BaseModel):
    ids: list[int] = Field(max_length=service.MAX_PAGE)
    action: Literal["read", "unread", "star", "unstar", "trash", "restore", "delete_forever"]


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)


class RefreshIn(BaseModel):
    refresh_token: str = Field(max_length=4096)


def _resend_http_error(exc: resend_client.ResendError) -> HTTPException:
    return HTTPException(400 if exc.status == 400 else 502, f"Resend: {exc}")


@router.get("/health")
def health():
    """Liveness probe. Deliberately does not touch the database or any third party."""
    return {"ok": True}


@router.post("/auth/login")
def login(body: LoginIn, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return auth.password_login(body.email.strip(), body.password)


@router.post("/auth/refresh")
def refresh(body: RefreshIn, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return auth.refresh(body.refresh_token)


@router.get("/theme")
def get_theme(response: Response):
    """Public branding used by the login screen. Falls back to defaults if the theme tables are unavailable."""
    try:
        with conn() as c:
            row = c.execute(
                "select * from public.theme_page_settings where website_key='mail' and page_key='inbox' and is_active=true limit 1"
            ).fetchone()
            components = c.execute(
                "select * from public.theme_component_settings where website_key='mail' and page_key='inbox' and is_active=true order by sort_order"
            ).fetchall()
    except (psycopg.Error, RuntimeError):
        log.warning("Theme settings unavailable; using built-in defaults", exc_info=True)
        row, components = None, []
    else:
        response.headers["Cache-Control"] = _PUBLIC_CACHE
    return {**(row or {}), "components": components}


@router.get("/company")
def get_company(response: Response):
    """Public company name/logo used by the login screen."""
    try:
        with conn() as c:
            row = c.execute(
                "select company_name, logo_url from public.company_profile where is_active=true order by updated_at desc limit 1"
            ).fetchone()
    except (psycopg.Error, RuntimeError):
        log.warning("Company profile unavailable; using built-in defaults", exc_info=True)
        return {}
    response.headers["Cache-Control"] = _PUBLIC_CACHE
    return row or {}


@router.get("/config")
def get_config(response: Response, user: auth.User = Depends(auth.current_user)):
    response.headers["Cache-Control"] = "no-store"
    service.ensure_mailboxes()
    boxes = service.list_mailboxes(user)
    return {
        "domain": config.MAIL_DOMAIN,
        "user": user.email,
        "mailboxes": [
            {"id": b["local_part"], "label": b["label"], "address": b["address"], "can_send": b["is_sending_enabled"]}
            for b in boxes
        ],
        "resend_configured": bool(config.RESEND_API_KEY),
    }


@router.get("/counts")
def counts(response: Response, user: auth.User = Depends(auth.current_user)):
    response.headers["Cache-Control"] = "no-store"
    return service.counts(user)


@router.get("/counts/drafts")
def draft_counts(response: Response, user: auth.User = Depends(auth.current_user)):
    response.headers["Cache-Control"] = "no-store"
    return service.draft_counts(user)


@router.get("/messages")
def list_messages(
    response: Response,
    mailbox: str = Query(max_length=64),
    folder: str = "inbox",
    q: str = Query("", max_length=200),
    limit: int = Query(50, ge=1, le=service.MAX_PAGE),
    offset: int = Query(0, ge=0, le=100_000),
    user: auth.User = Depends(auth.current_user),
):
    response.headers["Cache-Control"] = "no-store"
    if folder not in ("inbox", "sent", "drafts", "starred", "trash"):
        raise HTTPException(400, "Unknown folder")
    try:
        return service.list_messages(user, mailbox, folder, q, limit, offset)
    except LookupError:
        raise HTTPException(400, "Unknown mailbox")


@router.post("/messages/bulk")
def bulk_update(body: BulkIn, response: Response, user: auth.User = Depends(auth.current_user)):
    """Star, trash, restore, mark read/unread or permanently delete several messages at once."""
    response.headers["Cache-Control"] = "no-store"
    return {"updated": service.update_messages(user, body.ids, body.action)}


@router.post("/trash/empty")
def empty_trash(response: Response, mailbox: str = Query(max_length=64), user: auth.User = Depends(auth.current_user)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"deleted": service.empty_trash(user, mailbox)}
    except LookupError:
        raise HTTPException(400, "Unknown mailbox")


@router.get("/messages/{msg_id}")
def get_message(msg_id: int, response: Response, user: auth.User = Depends(auth.current_user)):
    response.headers["Cache-Control"] = "no-store"
    msg = service.get_message(user, msg_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    return msg


@router.post("/send")
def send(body: SendIn, response: Response, user: auth.User = Depends(auth.current_user)):
    response.headers["Cache-Control"] = "no-store"
    try:
        msg_id = service.send_mail(
            user, body.mailbox, body.to, body.cc, body.bcc,
            body.subject, body.body, body.reply_to_id, body.draft_id
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except resend_client.ResendError as exc:
        raise _resend_http_error(exc)
    except service.SentNotSaved as exc:
        raise HTTPException(500, str(exc))
    return {"id": msg_id}


@router.post("/drafts")
def save_draft(body: DraftIn, response: Response, user: auth.User = Depends(auth.current_user)):
    """Create a draft, or update the one named by draft_id. Used by autosave and the Save draft button."""
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.save_draft(
            user, body.mailbox, body.to, body.cc, body.bcc, body.subject, body.body,
            body.draft_id, body.reply_to_id,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.delete("/drafts/{draft_id}")
def delete_draft(draft_id: int, response: Response, user: auth.User = Depends(auth.current_user)):
    response.headers["Cache-Control"] = "no-store"
    if not service.delete_draft(user, draft_id):
        raise HTTPException(404, "Draft not found")
    return {"deleted": True}


@router.post("/sync")
def sync(response: Response, full: bool = False, user: auth.User = Depends(auth.current_user)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.sync_inbox(full_scan=full)
    except resend_client.ResendError as exc:
        raise _resend_http_error(exc)


@router.post("/webhooks/resend")
async def resend_webhook(request: Request):
    if not config.RESEND_WEBHOOK_SECRET:
        raise HTTPException(503, "Webhook disabled: set RESEND_WEBHOOK_SECRET")
    raw = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() in _WEBHOOK_HEADERS}
    try:
        # Imported here, not at module level, so a problem with this library can never stop the whole app starting.
        from svix.webhooks import Webhook, WebhookVerificationError
    except ImportError:
        log.exception("The svix package could not be imported")
        raise HTTPException(503, "Webhook verification is unavailable")
    try:
        Webhook(config.RESEND_WEBHOOK_SECRET).verify(raw, headers)
    except WebhookVerificationError:
        raise HTTPException(401, "Invalid webhook signature")
    except Exception:
        # e.g. a malformed secret in the environment; never leak details to the caller.
        log.exception("Webhook verification failed unexpectedly")
        raise HTTPException(401, "Invalid webhook signature")
    try:
        event = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "Invalid JSON")
    if not isinstance(event, dict):
        return {"ignored": True}
    data = event.get("data") or {}
    if event.get("type") in service._DELIVERY_EVENTS:
        updated = await run_in_threadpool(
            service.apply_delivery_event, event["type"], data.get("email_id"), headers.get("svix-id")
        )
        return {"delivery": event["type"], "updated": updated}
    if event.get("type") != "email.received":
        return {"ignored": True}
    item = {**data, "id": data.get("email_id")}
    try:
        result = await run_in_threadpool(service.ingest_received, item)
    except resend_client.ResendError as exc:
        raise _resend_http_error(exc)
    if result.get("failed"):
        raise HTTPException(502, "Could not download the message; Resend will retry")
    return {"received": True, **result}
