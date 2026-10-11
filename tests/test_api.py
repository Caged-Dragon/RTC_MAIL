"""HTTP-level tests: routing, validation, auth, webhook signature handling."""
import base64
import json
import time
from datetime import datetime

import httpx
import pytest
from svix.webhooks import Webhook

from mailcore import auth, config, resend_client, service

SECRET = "whsec_" + base64.b64encode(b"super-secret-key-for-tests").decode()


def signed_headers(payload: bytes, secret: str = SECRET) -> dict:
    ts = str(int(time.time()))
    sig = Webhook(secret).sign("msg_1", datetime.fromtimestamp(int(ts)), payload.decode())
    return {"svix-id": "msg_1", "svix-timestamp": ts, "svix-signature": sig, "content-type": "application/json"}


def test_app_constructs_and_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_security_headers_present_on_api_and_static(client):
    for path in ("/api/health", "/"):
        h = client.get(path).headers
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in h["content-security-policy"]


def test_static_frontend_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "RT Crackers Mail" in r.text


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
def test_openapi_and_docs_are_disabled(client, path):
    assert client.get(path).status_code == 404


class TestAuthRequired:
    @pytest.fixture(autouse=True)
    def _real_auth(self, monkeypatch):
        monkeypatch.setattr(config, "DEV_NO_AUTH", False)

    @pytest.mark.parametrize("method,path", [("get", "/api/config"), ("get", "/api/counts"),
                                             ("get", "/api/messages?mailbox=admin"), ("get", "/api/messages/1"),
                                             ("post", "/api/sync")])
    def test_endpoints_reject_anonymous(self, client, method, path):
        assert getattr(client, method)(path).status_code == 401

    def test_send_rejects_anonymous(self, client):
        assert client.post("/api/send", json={"mailbox": "admin", "to": "a@b.com"}).status_code == 401

    def test_empty_bearer_token_rejected(self, client):
        assert client.get("/api/counts", headers={"authorization": "Bearer "}).status_code == 401

    def test_login_restricted_to_admin_email(self, client):
        r = client.post("/api/auth/login", json={"email": "someone@else.com", "password": "x"})
        assert r.status_code == 403 and config.ADMIN_EMAIL in r.json()["detail"]

    def test_login_success_returns_tokens(self, client, monkeypatch):
        monkeypatch.setattr(auth.httpx, "post", lambda *a, **k: httpx.Response(
            200, json={"access_token": "a", "refresh_token": "r", "user": {"email": config.ADMIN_EMAIL}}))
        r = client.post("/api/auth/login", json={"email": f" {config.ADMIN_EMAIL} ", "password": "pw"})
        assert r.status_code == 200 and r.json()["access_token"] == "a"
        assert r.headers["cache-control"] == "no-store"

    def test_wrong_password_is_401(self, client, monkeypatch):
        monkeypatch.setattr(auth.httpx, "post", lambda *a, **k: httpx.Response(400, json={}))
        r = client.post("/api/auth/login", json={"email": config.ADMIN_EMAIL, "password": "bad"})
        assert r.status_code == 401

    def test_valid_token_grants_access_and_is_cached(self, client, monkeypatch):
        auth._cache.clear()
        calls = {"user": 0}

        def fake_get(*a, **k):
            calls["user"] += 1
            return httpx.Response(200, json={"id": "uid-1", "email": config.ADMIN_EMAIL})

        monkeypatch.setattr(auth.httpx, "get", fake_get)
        monkeypatch.setattr(auth, "load_permissions", lambda uid, email: (True, set()))
        monkeypatch.setattr(service, "counts", lambda user: {"sales": 2})
        for _ in range(3):
            r = client.get("/api/counts", headers={"authorization": "Bearer tok"})
            assert r.status_code == 200 and r.json() == {"sales": 2}
        assert calls["user"] == 1  # permissions lookup cached between requests

    def test_user_without_any_access_is_403(self, client, monkeypatch):
        auth._cache.clear()
        monkeypatch.setattr(auth.httpx, "get", lambda *a, **k: httpx.Response(200, json={"id": "u", "email": "x@y.com"}))
        monkeypatch.setattr(auth, "load_permissions", lambda uid, email: (False, set()))
        assert client.get("/api/counts", headers={"authorization": "Bearer t2"}).status_code == 403

    def test_expired_token_is_401(self, client, monkeypatch):
        auth._cache.clear()
        monkeypatch.setattr(auth.httpx, "get", lambda *a, **k: httpx.Response(401, json={}))
        assert client.get("/api/counts", headers={"authorization": "Bearer old"}).status_code == 401


class TestValidation:
    def test_unknown_folder(self, client):
        assert client.get("/api/messages?mailbox=admin&folder=bogus").status_code == 400

    def test_overlong_search_rejected(self, client):
        assert client.get("/api/messages?mailbox=admin&q=" + "x" * 201).status_code == 422

    def test_send_requires_fields(self, client):
        assert client.post("/api/send", json={"mailbox": "admin"}).status_code == 422

    def test_send_maps_value_error_to_400(self, client, monkeypatch):
        monkeypatch.setattr(service, "send_mail", lambda *a, **k: (_ for _ in ()).throw(ValueError("nope")))
        r = client.post("/api/send", json={"mailbox": "admin", "to": "a@b.com"})
        assert r.status_code == 400 and r.json()["detail"] == "nope"

    def test_send_maps_resend_failure_to_502(self, client, monkeypatch):
        monkeypatch.setattr(service, "send_mail",
                            lambda *a, **k: (_ for _ in ()).throw(resend_client.ResendError(500, "boom")))
        assert client.post("/api/send", json={"mailbox": "admin", "to": "a@b.com"}).status_code == 502

    def test_send_reports_sent_but_not_saved(self, client, monkeypatch):
        monkeypatch.setattr(service, "send_mail",
                            lambda *a, **k: (_ for _ in ()).throw(service.SentNotSaved("sent, not saved")))
        r = client.post("/api/send", json={"mailbox": "admin", "to": "a@b.com"})
        assert r.status_code == 500 and "sent" in r.json()["detail"]


class TestWebhook:
    EVENT = json.dumps({"type": "email.received", "data": {"email_id": "e1", "to": ["sales@rtcrackers.com"]}}).encode()

    @pytest.fixture(autouse=True)
    def _secret(self, monkeypatch):
        monkeypatch.setattr(config, "RESEND_WEBHOOK_SECRET", SECRET)

    def test_disabled_without_secret(self, client, monkeypatch):
        monkeypatch.setattr(config, "RESEND_WEBHOOK_SECRET", "")
        assert client.post("/api/webhooks/resend", content=self.EVENT).status_code == 503

    def test_rejects_missing_or_bad_signature(self, client):
        assert client.post("/api/webhooks/resend", content=self.EVENT).status_code == 401
        bad = signed_headers(self.EVENT, "whsec_" + base64.b64encode(b"other-secret-key").decode())
        assert client.post("/api/webhooks/resend", content=self.EVENT, headers=bad).status_code == 401

    def test_valid_event_is_ingested(self, client, monkeypatch):
        seen = []
        monkeypatch.setattr(service, "ingest_received", lambda item: seen.append(item) or {"new": 1, "failed": 0})
        r = client.post("/api/webhooks/resend", content=self.EVENT, headers=signed_headers(self.EVENT))
        assert r.status_code == 200 and r.json()["received"] is True
        assert seen[0]["id"] == "e1"

    def test_other_event_types_are_ignored(self, client, monkeypatch):
        body = json.dumps({"type": "email.sent", "data": {}}).encode()
        monkeypatch.setattr(service, "ingest_received", lambda item: pytest.fail("should not ingest"))
        r = client.post("/api/webhooks/resend", content=body, headers=signed_headers(body))
        assert r.json() == {"ignored": True}

    def test_failed_download_returns_502_so_resend_retries(self, client, monkeypatch):
        monkeypatch.setattr(service, "ingest_received", lambda item: {"new": 0, "failed": 1})
        r = client.post("/api/webhooks/resend", content=self.EVENT, headers=signed_headers(self.EVENT))
        assert r.status_code == 502

    def test_non_object_json_is_ignored_not_crashed(self, client):
        body = b"[1,2,3]"
        r = client.post("/api/webhooks/resend", content=body, headers=signed_headers(body))
        assert r.status_code == 200 and r.json() == {"ignored": True}


class TestDatabaseBackedPublicRoutes:
    def test_theme_and_company_degrade_gracefully_without_tables(self, client, db):
        # The scratch schema has no theme/company tables, like a fresh project would not either.
        theme, company = client.get("/api/theme"), client.get("/api/company")
        assert theme.status_code == 200 and theme.json() == {"components": []}
        assert company.status_code == 200 and company.json() == {}

    def test_config_lists_mailboxes(self, client, db):
        body = client.get("/api/config").json()
        assert [m["id"] for m in body["mailboxes"]][:3] == ["admin", "sales", "support"]
        assert body["resend_configured"] is True

    def test_end_to_end_list_open_counts(self, client, db, monkeypatch):
        monkeypatch.setattr(resend_client, "get_received", lambda i: {"from": "j@example.com", "subject": "S", "text": "T"})
        client.get("/api/config")
        service.ingest_received({"id": "z1", "to": ["info@rtcrackers.com"]})
        assert client.get("/api/counts").json() == {"info": 1}
        [row] = client.get("/api/messages?mailbox=info").json()
        msg = client.get(f"/api/messages/{row['id']}").json()
        assert msg["is_read"] is True and client.get("/api/counts").json() == {}
        assert client.get("/api/messages/999999").status_code == 404
        assert client.get("/api/messages?mailbox=bogus").status_code == 400

    def test_database_outage_returns_clean_json(self, client, monkeypatch):
        monkeypatch.setattr(config, "DATABASE_URL", "")
        r = client.get("/api/config")
        assert r.status_code == 503 and "detail" in r.json()


class TestDraftsApi:
    def test_draft_lifecycle(self, client, db):
        client.get("/api/config")
        r = client.post("/api/drafts", json={"mailbox": "sales", "to": "a@example.com", "subject": "Hi", "body": "x"})
        assert r.status_code == 200 and r.json()["id"]
        did = r.json()["id"]
        upd = client.post("/api/drafts", json={"mailbox": "sales", "to": "a@example.com", "subject": "Hi 2", "body": "y", "draft_id": did})
        assert upd.json()["id"] == did

        assert client.get("/api/counts/drafts").json() == {"sales": 1}
        [row] = client.get("/api/messages?mailbox=sales&folder=drafts").json()
        assert row["subject"] == "Hi 2" and row["folder"] == "drafts"
        msg = client.get(f"/api/messages/{did}").json()
        assert msg["text_body"] == "y" and msg["to_addrs"] == ["a@example.com"] and msg["mailbox"] == "sales"

        assert client.delete(f"/api/drafts/{did}").json() == {"deleted": True}
        assert client.delete(f"/api/drafts/{did}").status_code == 404
        assert client.get("/api/counts/drafts").json() == {}

    def test_empty_draft_returns_null_id(self, client, db):
        client.get("/api/config")
        assert client.post("/api/drafts", json={"mailbox": "sales"}).json() == {"id": None, "saved_at": None}

    def test_updating_missing_draft_is_404(self, client, db):
        client.get("/api/config")
        assert client.post("/api/drafts", json={"mailbox": "sales", "subject": "x", "draft_id": 99999}).status_code == 404

    def test_unknown_mailbox_is_404_and_bad_payload_422(self, client, db):
        client.get("/api/config")
        assert client.post("/api/drafts", json={"mailbox": "nope", "subject": "x"}).status_code == 404
        assert client.post("/api/drafts", json={"subject": "x"}).status_code == 422

    def test_send_with_draft_id_consumes_the_draft(self, client, db, monkeypatch):
        monkeypatch.setattr(resend_client, "send", lambda p: {"id": "o1"})
        client.get("/api/config")
        did = client.post("/api/drafts", json={"mailbox": "sales", "to": "a@example.com", "subject": "S", "body": "b"}).json()["id"]
        r = client.post("/api/send", json={"mailbox": "sales", "to": "a@example.com", "subject": "S", "body": "b", "draft_id": did})
        assert r.status_code == 200
        assert client.get("/api/counts/drafts").json() == {}
        assert len(client.get("/api/messages?mailbox=sales&folder=sent").json()) == 1

    def test_draft_endpoints_require_auth(self, client, monkeypatch):
        monkeypatch.setattr(config, "DEV_NO_AUTH", False)
        assert client.post("/api/drafts", json={"mailbox": "sales"}).status_code == 401
        assert client.delete("/api/drafts/1").status_code == 401
        assert client.get("/api/counts/drafts").status_code == 401


class TestGmailStyleApi:
    @pytest.fixture
    def seeded(self, client, db, monkeypatch):
        monkeypatch.setattr(resend_client, "get_received", lambda i: {"from": "j@example.com", "subject": f"S{i}", "text": "T"})
        client.get("/api/config")
        for i in ("a", "b", "c"):
            service.ingest_received({"id": i, "to": ["info@rtcrackers.com"]})
        return [m["id"] for m in client.get("/api/messages?mailbox=info").json()]

    def test_bulk_star_trash_restore_flow(self, client, seeded):
        r = client.post("/api/messages/bulk", json={"ids": seeded[:2], "action": "star"})
        assert r.json() == {"updated": 2}
        assert len(client.get("/api/messages?mailbox=info&folder=starred").json()) == 2
        client.post("/api/messages/bulk", json={"ids": seeded[:1], "action": "trash"})
        assert [m["deleted"] for m in client.get("/api/messages?mailbox=info&folder=trash").json()] == [True]
        assert client.post("/api/messages/bulk", json={"ids": seeded[:1], "action": "restore"}).json() == {"updated": 1}

    def test_bulk_validation(self, client, seeded):
        assert client.post("/api/messages/bulk", json={"ids": [1], "action": "explode"}).status_code == 422
        assert client.post("/api/messages/bulk", json={"ids": "x", "action": "star"}).status_code == 422
        assert client.post("/api/messages/bulk", json={"ids": list(range(201)), "action": "star"}).status_code == 422

    def test_pagination_params(self, client, seeded):
        assert len(client.get("/api/messages?mailbox=info&limit=2").json()) == 2
        assert len(client.get("/api/messages?mailbox=info&limit=2&offset=2").json()) == 1
        assert client.get("/api/messages?mailbox=info&limit=0").status_code == 422
        assert client.get("/api/messages?mailbox=info&limit=201").status_code == 422
        assert client.get("/api/messages?mailbox=info&offset=-1").status_code == 422

    def test_empty_trash(self, client, seeded):
        client.post("/api/messages/bulk", json={"ids": seeded, "action": "trash"})
        assert client.post("/api/trash/empty?mailbox=info").json() == {"deleted": 3}
        assert client.post("/api/trash/empty?mailbox=nope").status_code == 400

    def test_new_endpoints_require_auth(self, client, monkeypatch):
        monkeypatch.setattr(config, "DEV_NO_AUTH", False)
        assert client.post("/api/messages/bulk", json={"ids": [1], "action": "star"}).status_code == 401
        assert client.post("/api/trash/empty?mailbox=info").status_code == 401

    def test_delivery_webhook_updates_status(self, client, db, monkeypatch):
        monkeypatch.setattr(config, "RESEND_WEBHOOK_SECRET", SECRET)
        monkeypatch.setattr(resend_client, "send", lambda p: {"id": "out-9"})
        client.get("/api/config")
        client.post("/api/send", json={"mailbox": "sales", "to": "a@example.com", "subject": "S", "body": "b"})
        body = json.dumps({"type": "email.bounced", "data": {"email_id": "out-9"}}).encode()
        r = client.post("/api/webhooks/resend", content=body, headers=signed_headers(body))
        assert r.status_code == 200 and r.json() == {"delivery": "email.bounced", "updated": 1}
        assert client.get("/api/messages?mailbox=sales&folder=sent").json()[0]["status"] == "bounced"


class TestStartupResilience:
    def test_import_failure_is_reported_on_health_instead_of_crashing(self, monkeypatch):
        """If the application modules cannot load, /api/health must say why (not an opaque platform 500)."""
        import sys

        from fastapi.testclient import TestClient

        monkeypatch.setitem(sys.modules, "mailcore.routes", None)   # makes `from mailcore.routes import router` fail
        sys.modules.pop("main", None)
        try:
            import main

            r = TestClient(main.app).get("/api/health")
            assert r.status_code == 503
            body = r.json()
            assert body["ok"] is False
            assert "ImportError" in body["startup_error"] or "ModuleNotFoundError" in body["startup_error"]
            assert r.headers["x-content-type-options"] == "nosniff"      # security headers still applied
        finally:
            sys.modules.pop("main", None)                                # next tests re-import the healthy app

    def test_svix_is_not_needed_at_startup(self):
        """A broken svix install may only affect the webhook, never the whole app."""
        import subprocess, sys, textwrap

        code = textwrap.dedent("""
            import sys
            sys.modules["svix"] = None; sys.modules["svix.webhooks"] = None
            import main
            assert main.STARTUP_ERROR is None, main.STARTUP_ERROR
            print("started without svix")
        """)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(config.ROOT))
        assert "started without svix" in out.stdout, out.stderr[-500:]
