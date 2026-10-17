"""Login through Supabase Auth + the current RT Crackers public.admin_access / mailbox_access tables."""
import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field

import httpx
from fastapi import HTTPException, Request

from . import config
from .db import conn

log = logging.getLogger(__name__)

_TTL = 60
_MAX_CACHE = 500
_cache: dict[str, tuple[float, "User"]] = {}
_cache_lock = threading.Lock()


@dataclass
class User:
    uid: str
    email: str
    is_admin: bool
    mailbox_ids: set[int] = field(default_factory=set)

    def can_access(self, mailbox_id: int) -> bool:
        return self.is_admin or mailbox_id in self.mailbox_ids


def _headers(token: str | None = None) -> dict:
    return {"apikey": config.SUPABASE_ANON_KEY, "Authorization": f"Bearer {token or config.SUPABASE_ANON_KEY}"}


def _require_supabase() -> None:
    if not (config.SUPABASE_URL and config.SUPABASE_ANON_KEY):
        raise HTTPException(503, "SUPABASE_URL and SUPABASE_ANON_KEY are not configured")


def fetch_user(token: str) -> dict | None:
    _require_supabase()
    try:
        r = httpx.get(f"{config.SUPABASE_URL}/auth/v1/user", headers=_headers(token), timeout=10)
    except httpx.HTTPError as exc:
        log.warning("Supabase Auth unreachable: %s", exc)
        raise HTTPException(502, "Could not reach Supabase Auth") from exc
    if r.status_code in (401, 403):
        return None
    if r.is_error:
        log.warning("Supabase Auth returned %s", r.status_code)
        raise HTTPException(502, "Supabase Auth error")
    return r.json()


def load_permissions(uid: str, email: str) -> tuple[bool, set[int]]:
    """Admin = an ACTIVE public.admin_access row linked to this Supabase auth user (any admin id, not one address).
    Matching on the auth user id (never on the e-mail text alone) means nobody can claim an admin address by signing up."""
    with conn() as c:
        admin_row = c.execute(
            "select 1 from public.admin_access where auth_user_id=%s and is_active=true", (uid,)
        ).fetchone()
        rows = c.execute(
            "select mailbox_id from public.mailbox_access where auth_user_id=%s", (uid,)
        ).fetchall()
    return bool(admin_row), {r["mailbox_id"] for r in rows}


def current_user(request: Request) -> User:
    if config.DEV_NO_AUTH:
        return User("dev", "dev@local", True)
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "Please sign in")
    token = header[7:].strip()
    if not token:
        raise HTTPException(401, "Please sign in")

    key = hashlib.sha256(token.encode()).hexdigest()
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]

    info = fetch_user(token)
    if not info or not info.get("id"):
        raise HTTPException(401, "Session expired, please sign in again")
    email = info.get("email") or ""
    is_admin, ids = load_permissions(info["id"], email)
    if not is_admin and not ids:
        raise HTTPException(403, "This account has no mailbox access")
    user = User(info["id"], email, is_admin, ids)

    with _cache_lock:
        if len(_cache) >= _MAX_CACHE:
            # Drop expired entries first; only wipe everything if that was not enough.
            for k in [k for k, (t, _) in _cache.items() if now - t >= _TTL]:
                _cache.pop(k, None)
            if len(_cache) >= _MAX_CACHE:
                _cache.clear()
        _cache[key] = (now, user)
    return user


LOGIN_FAILED = "Wrong email or password"


def password_login(email: str, password: str) -> dict:
    """Sign in with the same Supabase credentials as the admin site. The account must be an active admin
    (public.admin_access) or hold explicit mailbox access; every other account gets the same generic failure."""
    _require_supabase()
    tokens = _token_call("password", {"email": email.strip(), "password": password})
    is_admin, ids = load_permissions(tokens.pop("uid"), tokens.get("email", ""))
    if not is_admin and not ids:
        raise HTTPException(401, LOGIN_FAILED)
    return tokens


def refresh(refresh_token: str) -> dict:
    _require_supabase()
    out = _token_call("refresh_token", {"refresh_token": refresh_token})
    out.pop("uid", None)
    return out


def _token_call(grant: str, body: dict) -> dict:
    try:
        r = httpx.post(
            f"{config.SUPABASE_URL}/auth/v1/token",
            params={"grant_type": grant},
            headers=_headers(),
            json=body,
            timeout=10,
        )
    except httpx.HTTPError as exc:
        log.warning("Supabase Auth unreachable: %s", exc)
        raise HTTPException(502, "Could not reach Supabase Auth") from exc
    if r.status_code in (400, 401, 422):
        raise HTTPException(401, "Wrong email or password" if grant == "password" else "Session expired")
    if r.is_error:
        log.warning("Supabase Auth token call returned %s", r.status_code)
        raise HTTPException(502, "Supabase Auth error")
    d = r.json()
    return {
        "access_token": d["access_token"],
        "refresh_token": d["refresh_token"],
        "expires_in": d.get("expires_in", 3600),
        "email": (d.get("user") or {}).get("email", ""),
        "uid": (d.get("user") or {}).get("id", ""),
    }
