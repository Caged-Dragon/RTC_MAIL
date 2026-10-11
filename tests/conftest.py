"""Shared test setup.

DB-backed tests need a scratch Postgres database:
    TEST_DATABASE_URL=postgresql://user:pass@localhost/rtmail_test python -m pytest -q
They are skipped automatically when the database or `psql` is unavailable.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://rt:rt@localhost/rtmail_test")

# Must be set before `mailcore.config` is imported anywhere.
os.environ.update(
    DATABASE_URL=TEST_URL,
    RESEND_API_KEY="re_test",
    MAIL_DOMAIN="rtcrackers.com",
    MAIL_DEV_NO_AUTH="1",
    SUPABASE_URL="https://x.supabase.co",
    SUPABASE_ANON_KEY="anon",
)
os.environ.pop("VERCEL", None)

SCHEMA = Path(__file__).with_name("schema.sql")
LOCALS = ["admin", "sales", "support", "account", "noreply", "billing", "contact", "help", "info", "orders"]
PURPOSE_FOR = {"admin": "admin", "sales": "sales", "support": "support", "billing": "billing", "orders": "orders"}

SEED_SQL = """
insert into business_email_purposes (purpose_code, purpose_name)
  values ('admin','Admin'),('sales','Sales'),('support','Support'),('billing','Billing'),('orders','Orders'),('general','General');
""" + "\n".join(
    f"insert into business_email_addresses (email_purpose_id, email_address, display_name) "
    f"select email_purpose_id, '{l}@rtcrackers.com', 'RT Crackers {l.title()}' "
    f"from business_email_purposes where purpose_code = '{PURPOSE_FOR.get(l, 'general')}';"
    for l in LOCALS
) + """
-- ensure_mailboxes() only adds missing mailboxes once one exists to copy business_id from.
insert into mailboxes (business_email_id, business_id, email_address, local_part, display_name, mailbox_type)
  select business_email_id, 1, email_address, 'admin', display_name, 'admin'
  from business_email_addresses where email_address = 'admin@rtcrackers.com';
"""


def _psql(sql_or_file: str, is_file: bool = False) -> None:
    args = ["psql", TEST_URL, "-q", "-v", "ON_ERROR_STOP=1"] + (["-f", sql_or_file] if is_file else ["-c", sql_or_file])
    subprocess.run(args, check=True, capture_output=True)


@pytest.fixture(scope="session")
def db_available() -> bool:
    if not shutil.which("psql"):
        return False
    try:
        _psql("select 1")
    except (subprocess.CalledProcessError, OSError):
        return False
    return True


@pytest.fixture
def db(db_available):
    """A freshly reset, seeded database for each test that asks for it."""
    if not db_available:
        pytest.skip("No test Postgres available (set TEST_DATABASE_URL)")
    _psql(str(SCHEMA), is_file=True)
    _psql(SEED_SQL)
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as c:
        yield c
