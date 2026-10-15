import json
import logging
from typing import Literal
from urllib.parse import urlsplit

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from . import auth, config, resend_client, service
from .db import conn

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_WEBHOOK_HEADERS = {"svix-id", "svix-timestamp", "svix-signature"}
_PUBLIC_CACHE = "public, max-age=300, s-maxage=600, stale-while-revalidate=3600"
FOLDERS = ("inbox", "sent", "drafts", "starred", "trash")


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


@router.get("/health/ready")
def readiness(response: Response):
    """Readiness probe for deployment setup. Reports booleans only, never secret values."""
    # The reference is the Supabase *project* ref, not the organization ID. These
    # checks catch a common misconfiguration where Auth is pointed at an organization
    # ID or the DB pooler credentials belong to a different project.
    expected_ref = config.SUPABASE_PROJECT_REF.lower()
    try:
        auth_host = (urlsplit(config.SUPABASE_URL).hostname or "").lower()
        db_url = urlsplit(config.DATABASE_URL)
        db_host = (db_url.hostname or "").lower()
        db_user = (db_url.username or "").lower()
    except ValueError:
        # A malformed URL should cause readiness to fail, never a 500 with details.
        auth_host = db_host = db_user = ""
    database_project_matches = (
        db_host == f"db.{expected_ref}.supabase.co"
        or db_user == f"postgres.{expected_ref}"
    )
    checks = {
        "database": False,
        "database_project_matches": database_project_matches,
        "mail_schema": False,
        "supabase_auth_configured": bool(config.SUPABASE_URL and config.SUPABASE_ANON_KEY),
        "supabase_auth_project_matches": auth_host == f"{expected_ref}.supabase.co",
        "resend_sending_configured": bool(config.RESEND_API_KEY),
        "resend_webhook_configured": bool(config.RESEND_WEBHOOK_SECRET),
    }
    try:
        with conn() as database:
            database.execute("select 1").fetchone()
            checks["database"] = True
            schema = database.execute("""
                select
                  to_regclass('public.business_email_purposes') is not null as business_email_purposes,
                  to_regclass('public.business_email_addresses') is not null as business_email_addresses,
                  to_regclass('public.mailboxes') is not null as mailboxes,
                  to_regclass('public.mailbox_access') is not null as mailbox_access,
                  to_regclass('public.email_messages') is not null as email_messages,
                  to_regclass('public.email_recipients') is not null as email_recipients
            """).fetchone()
            checks["mail_schema"] = bool(schema) and all(bool(value) for value in schema.values())
    except (psycopg.Error, RuntimeError):
        # Keep provider/host/password details out of the public HTTP response.
        log.warning("Readiness check could not connect to the configured database or inspect the mail schema")

    ready = all(checks.values())
    response.status_code = 200 if ready else 503
    response.headers["Cache-Control"] = "no-store"
    return {"ok": ready, "checks": checks}


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


@router.get("/branding")
def get_branding(response: Response):
    """Theme and company in one request (one DB connection). Falls back to defaults if the tables are unavailable."""
    empty = {"theme": {"components": []}, "company": {}}
    try:
        with conn() as c:
            try:
                row = c.execute(
                    "select * from public.theme_page_settings where website_key='mail' and page_key='inbox' and is_active=true limit 1"
                ).fetchone()
                components = c.execute(
                    "select * from public.theme_component_settings where website_key='mail' and page_key='inbox' and is_active=true order by sort_order"
                ).fetchall()
            except psycopg.Error:
                c.rollback()
                log.warning("Theme settings unavailable; using defaults", exc_info=True)
                row, components = None, []
            try:
                company = c.execute(
                    "select company_name, logo_url from public.company_profile where is_active=true order by updated_at desc limit 1"
                ).fetchone()
            except psycopg.Error:
                c.rollback()
                log.warning("Company profile unavailable; using defaults", exc_info=True)
                company = None
    except (psycopg.Error, RuntimeError):
        log.warning("Branding unavailable; using built-in defaults", exc_info=True)
        return empty
    response.headers["Cache-Control"] = _PUBLIC_CACHE
    return {"theme": {**(row or {}), "components": components}, "company": company or {}}


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
        "webhook_configured": bool(config.RESEND_WEBHOOK_SECRET),
    }


@router.get("/bootstrap")
def bootstrap(
    response: Response,
    mailbox: str = Query("", max_length=64),
    folder: str = "inbox",
    user: auth.User = Depends(auth.current_user),
):
    """Everything the first screen needs in ONE request: mailboxes, unread/draft counts, first page of a folder."""
    response.headers["Cache-Control"] = "no-store"
    if folder not in FOLDERS:
        raise HTTPException(400, "Unknown folder")
    data = service.bootstrap(user, mailbox or None, folder, limit=50)
    return {
        "domain": config.MAIL_DOMAIN,
        "user": user.email,
        "mailboxes": [
            {"id": b["local_part"], "label": b["label"], "address": b["address"], "can_send": b["is_sending_enabled"]}
            for b in data["boxes"]
        ],
        "resend_configured": bool(config.RESEND_API_KEY),
        "webhook_configured": bool(config.RESEND_WEBHOOK_SECRET),
        "mailbox": data["mailbox"],
        "folder": folder,
        "counts": data["counts"],
        "drafts": data["drafts"],
        "latest": data["latest"],
        "items": data["items"],
    }


@router.get("/pulse")
def pulse(response: Response, user: auth.User = Depends(auth.current_user)):
    """Cheap 'anything new?' check: unread counts, draft counts and the newest inbox id per mailbox (one query)."""
    response.headers["Cache-Control"] = "no-store"
    return service.pulse(user)


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
    if folder not in FOLDERS:
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
