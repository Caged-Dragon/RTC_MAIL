"""Thin wrapper around the Resend REST API (send + received-mail endpoints)."""
import logging
import threading
import time
from urllib.parse import quote

import httpx

from . import config

log = logging.getLogger(__name__)

BASE = "https://api.resend.com"
MIN_INTERVAL = 0.55   # Resend allows ~2 requests/second per team
MAX_ATTEMPTS = 4

_last_call = 0.0
_throttle = threading.Lock()


class ResendError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _wait_for_slot() -> None:
    """Serialize outgoing calls so concurrent requests cannot exceed Resend's rate limit."""
    global _last_call
    with _throttle:
        wait = MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def _retry_delay(response: httpx.Response) -> float:
    try:
        return min(max(float(response.headers.get("retry-after", "")), 0.5), 10.0)
    except ValueError:
        return 1.5


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
        msg = body.get("message") if isinstance(body, dict) else None
    except ValueError:
        msg = None
    return str(msg or response.text or f"HTTP {response.status_code}")[:500]


def _call(method: str, path: str, **kwargs) -> dict:
    if not config.RESEND_API_KEY:
        raise ResendError(400, "RESEND_API_KEY is not set. Add it to your .env file and restart.")
    headers = {"Authorization": f"Bearer {config.RESEND_API_KEY}", "User-Agent": "rtcrackers-mail/1.0"}
    headers.update(kwargs.pop("headers", None) or {})

    for attempt in range(1, MAX_ATTEMPTS + 1):
        _wait_for_slot()
        try:
            r = httpx.request(method, BASE + path, headers=headers, timeout=30, **kwargs)
        except httpx.HTTPError as exc:
            raise ResendError(502, f"Could not reach Resend: {exc}") from exc

        if r.status_code == 429 and attempt < MAX_ATTEMPTS:
            delay = _retry_delay(r)
            log.info("Resend rate limited; retrying in %.1fs (attempt %d)", delay, attempt)
            time.sleep(delay)
            continue
        if r.is_error:
            raise ResendError(r.status_code, _error_message(r))
        try:
            return r.json()
        except ValueError as exc:
            raise ResendError(502, "Resend returned an unreadable response") from exc
    raise ResendError(429, "Resend rate limit exceeded")  # pragma: no cover - loop always returns/raises


def send(payload: dict, idempotency_key: str | None = None) -> dict:
    """`idempotency_key` makes Resend ignore a repeated request, so a retry can never deliver the same mail twice."""
    return _call("POST", "/emails", json=payload, headers={"Idempotency-Key": idempotency_key} if idempotency_key else None)


def list_received(limit: int = 100, after: str | None = None) -> dict:
    params: dict = {"limit": limit}
    if after:
        params["after"] = after
    return _call("GET", "/emails/receiving", params=params)


def get_received(email_id: str) -> dict:
    return _call("GET", f"/emails/receiving/{quote(str(email_id), safe='')}")
