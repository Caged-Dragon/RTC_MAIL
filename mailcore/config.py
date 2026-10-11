"""Environment-driven configuration. Values are read once at import time."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_float(name: str, default: float) -> float:
    """Parse a float env var; fall back to the default instead of crashing on bad input."""
    try:
        value = float(_env(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


RESEND_API_KEY = _env("RESEND_API_KEY")
RESEND_WEBHOOK_SECRET = _env("RESEND_WEBHOOK_SECRET")
MAIL_DOMAIN = _env("MAIL_DOMAIN", "rtcrackers.com").lower()
DATABASE_URL = _env("DATABASE_URL")
SUPABASE_URL = _env("SUPABASE_URL").rstrip("/")
SUPABASE_ANON_KEY = _env("SUPABASE_ANON_KEY")
SYNC_BUDGET_SECONDS = _env_float("SYNC_BUDGET_SECONDS", 45.0)

# Kept for backwards compatibility only. Sign-in is no longer limited to this address:
# every ACTIVE row in public.admin_access (matched by Supabase auth user id) may enter.
ADMIN_EMAIL = _env("MAIL_ADMIN_EMAIL", f"admin@{MAIL_DOMAIN}").lower()

# MX hosts that are allowed to hold company mail (regular expressions, comma separated).
# Default = Resend's inbound servers (AWS SES inbound). Anything else is reported as a leak.
ALLOWED_MX = [x.strip() for x in _env("MAIL_ALLOWED_MX", r"^inbound-smtp\.[a-z0-9-]+\.amazonaws\.com\.?$").split(",") if x.strip()]

# Local-only escape hatch; can never be enabled on Vercel.
DEV_NO_AUTH = _env("MAIL_DEV_NO_AUTH") == "1" and not os.getenv("VERCEL")

REQUIRED_LOCALS = [
    "admin", "sales", "support", "account", "noreply",
    "billing", "contact", "help", "info", "orders",
]
