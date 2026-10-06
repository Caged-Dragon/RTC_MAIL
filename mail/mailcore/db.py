"""Supabase Postgres access. One short-lived connection per call (fine for serverless; use the pooler URL)."""
import psycopg
from psycopg.rows import dict_row

from . import config


def conn() -> psycopg.Connection:
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    # prepare_threshold=None: required by Supabase's transaction pooler (no server-side prepared statements)
    return psycopg.connect(config.DATABASE_URL, row_factory=dict_row, prepare_threshold=None, connect_timeout=10)
