import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


RESEND_API_KEY = _env("RESEND_API_KEY")
RESEND_WEBHOOK_SECRET = _env("RESEND_WEBHOOK_SECRET")
MAIL_DOMAIN = _env("MAIL_DOMAIN", "rtcrackers.com").lower()
DATABASE_URL = _env("DATABASE_URL")
SUPABASE_URL = _env("SUPABASE_URL").rstrip("/")
SUPABASE_ANON_KEY = _env("SUPABASE_ANON_KEY")
SYNC_BUDGET_SECONDS = float(_env("SYNC_BUDGET_SECONDS", "45"))

DEV_NO_AUTH = _env("MAIL_DEV_NO_AUTH") == "1" and not os.getenv("VERCEL")

REQUIRED_LOCALS = [
    "admin", "sales", "support", "account", "noreply",
    "billing", "contact", "help", "info", "orders",
]
