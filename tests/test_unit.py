"""Pure-function tests: no database, no network."""
from datetime import timezone

import pytest

from mailcore import auth, config, db, resend_client, service


class TestNormTs:
    def test_iso_with_z(self):
        d = service.norm_ts("2025-01-02T03:04:05Z")
        assert (d.year, d.month, d.day, d.hour) == (2025, 1, 2, 3) and d.tzinfo == timezone.utc

    def test_postgres_style_short_offset(self):
        d = service.norm_ts("2025-01-02 03:04:05.123456+00")
        assert d.tzinfo is not None and d.hour == 3

    def test_offset_is_converted_to_utc(self):
        assert service.norm_ts("2025-01-02T03:00:00+02:00").hour == 1

    def test_naive_is_treated_as_utc(self):
        assert service.norm_ts("2025-01-02T03:00:00").tzinfo == timezone.utc

    @pytest.mark.parametrize("bad", [None, "", "not a date"])
    def test_bad_input_falls_back_to_now(self, bad):
        assert service.norm_ts(bad).tzinfo == timezone.utc


class TestSnippet:
    def test_prefers_text(self):
        assert service.make_snippet("hello   world\n", "<p>ignored</p>") == "hello world"

    def test_strips_html_style_and_script(self):
        html = "<style>p{color:red}</style><script>alert(1)</script><p>Hi &amp; bye</p>"
        assert service.make_snippet("", html) == "Hi & bye"

    def test_truncates_to_160(self):
        assert len(service.make_snippet("x" * 500, "")) == 160


class TestAddresses:
    def test_split_handles_separators_and_names(self):
        assert service.split_addrs("A <a@x.com>; b@x.com,\n c@x.com") == ["a@x.com", "b@x.com", "c@x.com"]

    def test_split_dedupes_case_insensitively(self):
        assert service.split_addrs("a@x.com, A@X.com") == ["a@x.com"]

    def test_split_empty(self):
        assert service.split_addrs("  ") == []

    @pytest.mark.parametrize("bad", ["nope", "a@b", "a b@x.com"])
    def test_split_rejects_invalid(self, bad):
        with pytest.raises(ValueError):
            service.split_addrs(bad)

    def test_split_caps_recipient_count(self):
        many = ",".join(f"u{i}@x.com" for i in range(service.MAX_RECIPIENTS + 1))
        with pytest.raises(ValueError, match="Too many"):
            service.split_addrs(many)

    def test_as_list(self):
        assert service.as_list(None) == [] and service.as_list("a") == ["a"] and service.as_list(("a", "b")) == ["a", "b"]


class TestFormatting:
    def test_text_to_html_escapes_and_breaks_lines(self):
        out = service.text_to_html("<b>hi</b>\r\nline2\rline3")
        assert "&lt;b&gt;" in out and "<b>" not in out and out.count("<br>") == 2

    def test_like_escapes_wildcards(self):
        assert service._like(" 50%_off ") == "%50\\%\\_off%"


class TestConfig:
    def test_env_float_falls_back(self, monkeypatch):
        monkeypatch.setenv("X_NUM", "abc")
        assert config._env_float("X_NUM", 7.0) == 7.0
        monkeypatch.setenv("X_NUM", "-3")
        assert config._env_float("X_NUM", 7.0) == 7.0
        monkeypatch.setenv("X_NUM", "2.5")
        assert config._env_float("X_NUM", 7.0) == 2.5


class TestDatabaseUrl:
    def test_escapes_at_sign_in_password(self):
        url = db._normalize_database_url("postgresql://user:p@ss@host:5432/postgres")
        assert url == "postgresql://user:p%40ss@host:5432/postgres"

    def test_leaves_already_encoded_url_alone(self):
        url = "postgresql://user:p%40ss@host:5432/postgres"
        assert db._normalize_database_url(url) == url

    def test_ignores_non_postgres_and_empty(self):
        assert db._normalize_database_url("") == ""
        assert db._normalize_database_url("sqlite:///x.db") == "sqlite:///x.db"


class TestUser:
    def test_can_access(self):
        assert auth.User("1", "a@x", True).can_access(5)
        assert auth.User("1", "a@x", False, {5}).can_access(5)
        assert not auth.User("1", "a@x", False, {5}).can_access(6)


class TestResendClient:
    def test_requires_api_key(self, monkeypatch):
        monkeypatch.setattr(config, "RESEND_API_KEY", "")
        with pytest.raises(resend_client.ResendError) as e:
            resend_client.send({})
        assert e.value.status == 400

    def test_received_id_is_url_encoded(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(resend_client, "_call", lambda m, p, **k: seen.setdefault("path", p))
        resend_client.get_received("a/b?c")
        assert seen["path"] == "/emails/receiving/a%2Fb%3Fc"

    def test_retries_on_429_then_succeeds(self, monkeypatch):
        import httpx

        calls = []

        def fake_request(method, url, **kw):
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(429, headers={"retry-after": "0"}, json={"message": "slow down"})
            return httpx.Response(200, json={"id": "ok"})

        monkeypatch.setattr(resend_client.httpx, "request", fake_request)
        monkeypatch.setattr(resend_client.time, "sleep", lambda s: None)
        assert resend_client.send({"x": 1}) == {"id": "ok"} and len(calls) == 2

    def test_surfaces_api_error_message(self, monkeypatch):
        import httpx

        monkeypatch.setattr(resend_client.httpx, "request",
                            lambda *a, **k: httpx.Response(422, json={"message": "bad from"}))
        monkeypatch.setattr(resend_client.time, "sleep", lambda s: None)
        with pytest.raises(resend_client.ResendError, match="bad from") as e:
            resend_client.send({})
        assert e.value.status == 422


class TestRepoHygiene:
    """.env.example is committed to Git, so it must never contain real credentials."""

    SECRET_KEYS = {"RESEND_API_KEY", "RESEND_WEBHOOK_SECRET", "DATABASE_URL", "SUPABASE_ANON_KEY"}

    def test_env_example_secret_values_are_empty(self):
        text = (config.ROOT / ".env.example").read_text(encoding="utf-8")
        for line in text.splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() in self.SECRET_KEYS:
                assert value.strip() == "", f"{key.strip()} in .env.example must stay empty - put real values in .env"

    def test_env_example_has_no_secret_shaped_strings(self):
        import re

        text = (config.ROOT / ".env.example").read_text(encoding="utf-8")
        assert not re.search(r"re_[A-Za-z0-9_]{16,}|eyJ[A-Za-z0-9_-]{20,}|whsec_[A-Za-z0-9+/=]{10,}", text)

    def test_pyproject_dependencies_match_requirements_txt(self):
        """Hosts such as Vercel may install from either file; they must never drift apart."""
        import tomllib

        declared = tomllib.loads((config.ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
        required = [l.strip() for l in (config.ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
                    if l.strip() and not l.startswith("#")]
        assert sorted(declared) == sorted(required)
