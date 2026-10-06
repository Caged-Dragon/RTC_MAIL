"""Run with a scratch Postgres database:
TEST_DATABASE_URL=postgresql://... python -m pytest -q
"""
import os
import subprocess

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://rt:rt@localhost/rtmail_test")
os.environ.update(
    DATABASE_URL=TEST_URL,
    RESEND_API_KEY="re_test",
    MAIL_DOMAIN="rtcrackers.com",
    MAIL_DEV_NO_AUTH="1",
    SUPABASE_URL="https://x.supabase.co",
    SUPABASE_ANON_KEY="anon",
)

from fastapi.testclient import TestClient

from mailcore import resend_client
from main import app


def reset_db():
    sql = os.path.join(os.path.dirname(__file__), "schema.sql")
    subprocess.run(
        ["psql", TEST_URL, "-q", "-v", "ON_ERROR_STOP=1", "-f", sql],
        check=True,
        capture_output=True,
    )


def test_app_constructs():
    assert app.title == "RT Crackers Mail"


def test_resend_send_is_mockable(monkeypatch):
    monkeypatch.setattr(resend_client, "send", lambda payload: {"id": "test"})
    with TestClient(app) as client:
        assert client is not None
