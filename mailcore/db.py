"""Supabase Postgres access. One short-lived connection per call (fine for serverless; use the pooler URL)."""
from urllib.parse import quote, unquote, urlsplit

import psycopg
from psycopg.rows import dict_row

from . import config


def _normalize_database_url(value: str) -> str:
    """Normalize PostgreSQL URLs whose password contains unescaped '@' characters."""
    value = (value or "").strip()
    if not value:
        return value

    try:
        parts = urlsplit(value)
        if parts.scheme not in {"postgresql", "postgres"} or not parts.netloc:
            return value

        rest = value.split("://", 1)[1]
        authority, sep, suffix = rest.partition("/")
        if not sep or "@" not in authority or ":" not in authority:
            return value

        userinfo, hostport = authority.rsplit("@", 1)
        username, password = userinfo.split(":", 1)
        password = unquote(password)
        normalized = f"{quote(username, safe='')}:{quote(password, safe='')}@{hostport}"
        return f"{parts.scheme}://{normalized}/{suffix}"
    except Exception:
        return value


def conn() -> psycopg.Connection:
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")

    database_url = _normalize_database_url(config.DATABASE_URL)
    return psycopg.connect(
        database_url,
        row_factory=dict_row,
        prepare_threshold=None,
        connect_timeout=10,
    )
