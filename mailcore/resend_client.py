"""Thin wrapper around the Resend REST API (send + received-mail endpoints)."""
import time

import httpx

from . import config

BASE = "https://api.resend.com"
_last_call = 0.0


class ResendError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _call(method: str, path: str, **kwargs):
    global _last_call
    if not config.RESEND_API_KEY:
        raise ResendError(400, "RESEND_API_KEY is not set. Add it to your .env file and restart.")
    headers = {"Authorization": f"Bearer {config.RESEND_API_KEY}", "User-Agent": "rtcrackers-mail/1.0"}
    for attempt in range(4):
        # Resend's default limit is about 2 requests/second, so space calls out.
        wait = 0.55 - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
        try:
            r = httpx.request(method, BASE + path, headers=headers, timeout=30, **kwargs)
        except httpx.HTTPError as exc:
            raise ResendError(502, f"Could not reach Resend: {exc}") from exc
        if r.status_code == 429 and attempt < 3:
            time.sleep(1.5)
            continue
        if r.is_error:
            try:
                msg = r.json().get("message") or r.text
            except ValueError:
                msg = r.text
            raise ResendError(r.status_code, str(msg)[:500])
        return r.json()


def send(payload: dict) -> dict:
    return _call("POST", "/emails", json=payload)


def list_received(limit: int = 100, after: str | None = None) -> dict:
    params = {"limit": limit}
    if after:
        params["after"] = after
    return _call("GET", "/emails/receiving", params=params)


def get_received(email_id: str) -> dict:
    return _call("GET", f"/emails/receiving/{email_id}")
