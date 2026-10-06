"""Login through Supabase Auth + the current RT Crackers public.admin_access / mailbox_access tables."""
import hashlib
import time
from dataclasses import dataclass, field

import httpx
from fastapi import HTTPException, Request

from . import config
from .db import conn

_TTL = 60
_cache: dict[str, tuple[float, "User"]] = {}


@dataclass
class User:
    uid: str
    email: str
    is_admin: bool
    mailbox_ids: set[int] = field(default_factory=set)

    def can_access(self, mailbox_id: int, all_ids: set[int] | None = None) -> bool:
        return self.is_admin or mailbox_id in self.mailbox_ids


def _headers(token: str | None = None) -> dict:
    return {"apikey": config.SUPABASE_ANON_KEY, "Authorization": f"Bearer {token or config.SUPABASE_ANON_KEY}"}


def _require_supabase():
    if not (config.SUPABASE_URL and config.SUPABASE_ANON_KEY):
        raise HTTPException(503, "SUPABASE_URL and SUPABASE_ANON_KEY are not configured")


def fetch_user(token: str) -> dict | None:
    _require_supabase()
    try:
        r = httpx.get(f"{config.SUPABASE_URL}/auth/v1/user", headers=_headers(token), timeout=10)
    except httpx.HTTPError:
        raise HTTPException(502, "Could not reach Supabase Auth")
    if r.status_code in (401, 403):
        return None
    if r.is_error:
        raise HTTPException(502, "Supabase Auth error")
    return r.json()


def load_permissions(uid: str, email: str) -> tuple[bool, set[int]]:
    with conn() as c:
        admin = c.execute(
            "select 1 from public.admin_access where lower(email)=lower(%s) and is_active=true", (email,)
        ).fetchone()
        admin = admin if email.lower() == "admin@rtcrackers.com" else None
        rows = c.execute(
            "select mailbox_id from public.mailbox_access where auth_user_id=%s", (uid,)
        ).fetchall()
    return bool(admin), {r["mailbox_id"] for r in rows}


def current_user(request: Request) -> User:
    if config.DEV_NO_AUTH:
        return User("dev", "dev@local", True)
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "Please sign in")
    token = header[7:].strip()
    key = hashlib.sha256(token.encode()).hexdigest()
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _TTL:
        return hit[1]
    info = fetch_user(token)
    if not info or not info.get("id"):
        raise HTTPException(401, "Session expired, please sign in again")
    is_admin, ids = load_permissions(info["id"], info.get("email") or "")
    if not is_admin and not ids:
        raise HTTPException(403, "This account has no mailbox access")
    user = User(info["id"], info.get("email") or "", is_admin, ids)
    if len(_cache) > 500:
        _cache.clear()
    _cache[key] = (time.monotonic(), user)
    return user


def password_login(email: str, password: str) -> dict:
    _require_supabase()
    if email.strip().lower() != "admin@rtcrackers.com":
        raise HTTPException(403, "Mail portal login is restricted to admin@rtcrackers.com")
    return _token_call("password", {"email": email.strip(), "password": password})


def refresh(refresh_token: str) -> dict:
    _require_supabase()
    return _token_call("refresh_token", {"refresh_token": refresh_token})


def _token_call(grant: str, body: dict) -> dict:
    try:
        r = httpx.post(
            f"{config.SUPABASE_URL}/auth/v1/token",
            params={"grant_type": grant},
            headers=_headers(),
            json=body,
            timeout=10,
        )
    except httpx.HTTPError:
        raise HTTPException(502, "Could not reach Supabase Auth")
    if r.status_code in (400, 401, 422):
        raise HTTPException(401, "Wrong email or password" if grant == "password" else "Session expired")
    if r.is_error:
        raise HTTPException(502, "Supabase Auth error")
    d = r.json()
    return {
        "access_token": d["access_token"],
        "refresh_token": d["refresh_token"],
        "expires_in": d.get("expires_in", 3600),
        "email": (d.get("user") or {}).get("email", ""),
    }
