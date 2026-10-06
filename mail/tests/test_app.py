"""Needs a Postgres with tests/schema.sql loaded:  TEST_DATABASE_URL=postgresql://... python -m pytest -q
(Resend and Supabase Auth are mocked; nothing is sent.)"""
import os
import subprocess

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://rt:rt@localhost/rtmail_test")
os.environ.update(DATABASE_URL=TEST_URL, RESEND_API_KEY="re_test", RESEND_WEBHOOK_SECRET="",
                  MAIL_DOMAIN="rtcrackers.com", MAIL_DEV_NO_AUTH="1", SUPABASE_URL="https://x.supabase.co",
                  SUPABASE_ANON_KEY="anon")

import pytest
from fastapi.testclient import TestClient

from mailcore import auth, config, resend_client
from mailcore.db import conn
from main import app

RECEIVED = [
    {"id": "r4", "to": ["Info <info@rtcrackers.com>"], "from": "z@x.com", "subject": "Hello info",
     "created_at": "2026-10-05 11:00:00+00", "message_id": "<m4@x.com>"},
    {"id": "r3", "to": ["Sales <sales@rtcrackers.com>"], "cc": ["support@rtcrackers.com"], "from": "Ravi <ravi@gmail.com>",
     "subject": "Bulk order", "created_at": "2026-10-05 10:00:00.123456+00", "message_id": "<m3@gmail.com>"},
    {"id": "r2", "to": ["admin@rtcrackers.com"], "from": "bob@x.com", "subject": "Hi admin",
     "created_at": "2026-10-04 09:00:00+00", "message_id": "<m2@x.com>"},
    {"id": "r1", "to": ["someone@other.com"], "from": "bob@x.com", "subject": "Not ours",
     "created_at": "2026-10-03 09:00:00+00", "message_id": "<m1@x.com>"},
]
FULL = {"r4": {"text": "info body"}, "r3": {"text": "Need 500 boxes", "html": "<p>Need <b>500</b> boxes</p>"},
        "r2": {"text": "", "html": "<p>Hello admin</p>"}}


def reset_db():
    sql = os.path.join(os.path.dirname(__file__), "schema.sql")
    subprocess.run(["psql", TEST_URL, "-q", "-v", "ON_ERROR_STOP=1", "-f", sql], check=True, capture_output=True)


@pytest.fixture()
def client(monkeypatch):
    reset_db()
    sent, fetched = [], []
    monkeypatch.setattr(resend_client, "send", lambda p: sent.append(p) or {"id": f"out{len(sent)}"})
    monkeypatch.setattr(resend_client, "list_received", lambda limit=100, after=None: {"data": RECEIVED, "has_more": False})
    monkeypatch.setattr(resend_client, "get_received", lambda i: fetched.append(i) or FULL[i])
    with TestClient(app) as c:
        c.sent, c.fetched = sent, fetched
        yield c


def addresses(client):
    return [m["address"] for m in client.get("/api/config").json()["mailboxes"]]


def test_required_four_first_then_every_other_business_email(client):
    got = addresses(client)
    assert got[:4] == ["admin@rtcrackers.com", "sales@rtcrackers.com", "support@rtcrackers.com", "noreply@rtcrackers.com"]
    # billing had no mailbox row: it is auto-provisioned from business_email_addresses
    assert got[4:] == ["billing@rtcrackers.com", "info@rtcrackers.com", "orders@rtcrackers.com"]


def test_inactive_or_duplicate_addresses_are_not_listed(client):
    with conn() as c:
        c.execute("update business_email_addresses set is_active=false where email_address='orders@rtcrackers.com'")
    assert "orders@rtcrackers.com" not in addresses(client)
    assert len(addresses(client)) == len(set(addresses(client)))


def test_sync_routes_mail_to_every_matching_mailbox(client):
    assert client.post("/api/sync").json() == {"new": 4, "failed": 0, "partial": False}
    for box, n in [("sales", 1), ("support", 1), ("admin", 1), ("info", 1), ("noreply", 0), ("orders", 0)]:
        assert len(client.get(f"/api/messages?mailbox={box}").json()) == n, box
    assert client.fetched.count("r3") == 1            # body fetched once, stored in two mailboxes
    assert "r1" not in client.fetched                 # other domains ignored


def test_sync_is_idempotent_and_copies_survive_unique_resend_id(client):
    client.post("/api/sync")
    assert client.post("/api/sync").json()["new"] == 0
    with conn() as c:
        ids = sorted(r["resend_email_id"] for r in c.execute("select resend_email_id from email_messages").fetchall())
    assert len(ids) == 4 and len(set(ids)) == 4       # r3 stored twice with distinct stored ids
    assert sum(i.startswith("r3") for i in ids) == 2


def test_read_marks_read_and_counts(client):
    client.post("/api/sync")
    assert client.get("/api/counts").json() == {"sales": 1, "support": 1, "admin": 1, "info": 1}
    row = client.get("/api/messages?mailbox=sales").json()[0]
    assert row["to_addrs"] == ["sales@rtcrackers.com"] and row["from_address"] == "ravi@gmail.com"
    full = client.get(f"/api/messages/{row['id']}").json()
    assert full["text_body"] == "Need 500 boxes" and full["cc_addrs"] == ["support@rtcrackers.com"]
    assert "sales" not in client.get("/api/counts").json()


def test_search(client):
    client.post("/api/sync")
    assert len(client.get("/api/messages?mailbox=sales&q=BULK").json()) == 1
    assert client.get("/api/messages?mailbox=sales&q=nothing-here").json() == []
    assert client.get("/api/messages?mailbox=sales&q=%25").json() == []   # % is literal, not a wildcard


def test_send_from_a_non_default_mailbox_and_store_in_sent(client):
    r = client.post("/api/send", json={"mailbox": "info", "to": "Bob <bob@x.com>, c@y.com", "cc": "d@y.com",
                                       "subject": "Hello", "body": "Hi\nthere"})
    assert r.status_code == 200
    p = client.sent[0]
    assert p["from"] == "RT Crackers Information <info@rtcrackers.com>" and p["to"] == ["bob@x.com", "c@y.com"]
    sent = client.get("/api/messages?mailbox=info&folder=sent").json()
    assert len(sent) == 1 and sent[0]["to_addrs"] == ["bob@x.com", "c@y.com"]
    assert client.get("/api/messages?mailbox=sales&folder=sent").json() == []


def test_reply_sets_threading_headers(client):
    client.post("/api/sync")
    row = client.get("/api/messages?mailbox=admin").json()[0]
    client.post("/api/send", json={"mailbox": "admin", "to": "bob@x.com", "subject": "Re: Hi admin", "body": "ok",
                                   "reply_to_id": row["id"]})
    assert client.sent[0]["headers"]["In-Reply-To"] == "<m2@x.com>"


def test_send_validation(client):
    assert client.post("/api/send", json={"mailbox": "admin", "to": "", "subject": "s"}).status_code == 400
    assert client.post("/api/send", json={"mailbox": "admin", "to": "not-an-email", "subject": "s"}).status_code == 400
    assert client.post("/api/send", json={"mailbox": "nope", "to": "a@b.co", "subject": "s"}).status_code == 400
    assert client.sent == []


def test_disabled_sending_is_enforced(client):
    with conn() as c:
        c.execute("update mailboxes set is_sending_enabled=false where local_part='noreply'")
    cfg = {m["id"]: m["can_send"] for m in client.get("/api/config").json()["mailboxes"]}
    assert cfg["noreply"] is False and cfg["admin"] is True
    assert client.post("/api/send", json={"mailbox": "noreply", "to": "a@b.co", "subject": "s"}).status_code == 400


def test_webhook_requires_secret(client):
    assert client.post("/api/webhooks/resend", json={}).status_code == 503


# ---------- login & permissions (real auth path, Supabase mocked) ----------

@pytest.fixture()
def secured(client, monkeypatch):
    monkeypatch.setattr(config, "DEV_NO_AUTH", False)
    auth._cache.clear()
    users = {"tok-admin": {"id": "00000000-0000-0000-0000-000000000001", "email": "boss@x.com"},
             "tok-sales": {"id": "00000000-0000-0000-0000-000000000002", "email": "sam@x.com"},
             "tok-none": {"id": "00000000-0000-0000-0000-000000000003", "email": "nobody@x.com"}}
    monkeypatch.setattr(auth, "fetch_user", lambda t: users.get(t))
    with conn() as c:
        c.execute("insert into admins (status, auth_user_id) values ('active','00000000-0000-0000-0000-000000000001')")
        c.execute("insert into mailbox_access (mailbox_id, auth_user_id) select mailbox_id,'00000000-0000-0000-0000-000000000002' from mailboxes where local_part='sales'")
    return client


def H(t):
    return {"Authorization": f"Bearer {t}"}


def test_no_token_or_bad_token_is_rejected(secured):
    assert secured.get("/api/config").status_code == 401
    assert secured.get("/api/config", headers=H("bogus")).status_code == 401
    assert secured.post("/api/sync").status_code == 401
    assert secured.post("/api/send", json={"mailbox": "admin", "to": "a@b.co", "subject": "s"}).status_code == 401


def test_admin_sees_everything(secured):
    cfg = secured.get("/api/config", headers=H("tok-admin")).json()
    assert len(cfg["mailboxes"]) == 7 and cfg["user"] == "boss@x.com"


def test_limited_user_only_gets_granted_mailbox(secured):
    secured.post("/api/sync", headers=H("tok-admin"))
    cfg = secured.get("/api/config", headers=H("tok-sales")).json()
    assert [m["id"] for m in cfg["mailboxes"]] == ["sales"]
    assert len(secured.get("/api/messages?mailbox=sales", headers=H("tok-sales")).json()) == 1
    assert secured.get("/api/messages?mailbox=admin", headers=H("tok-sales")).status_code == 400
    admin_msg = secured.get("/api/messages?mailbox=admin", headers=H("tok-admin")).json()[0]["id"]
    assert secured.get(f"/api/messages/{admin_msg}", headers=H("tok-sales")).status_code == 404
    r = secured.post("/api/send", headers=H("tok-sales"), json={"mailbox": "admin", "to": "a@b.co", "subject": "s"})
    assert r.status_code == 400 and secured.sent == []


def test_user_without_any_access_is_forbidden(secured):
    assert secured.get("/api/config", headers=H("tok-none")).status_code == 403
