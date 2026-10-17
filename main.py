"""RT Crackers Mail (Supabase + Vercel).

NOTE FOR DEPLOYMENT: Vercel looks for a literal top-level `app = FastAPI(...)` in this file. Keep it exactly like that."""
import importlib
import logging
import sys
from pathlib import Path

import psycopg
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rtmail")

PUBLIC = Path(__file__).resolve().parent / "public"

# Kept deliberately light: the app only loads its own scripts and renders untrusted mail
# inside a sandboxed iframe that carries its own policy.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": "script-src 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'",
}

app = FastAPI(title="RT Crackers Mail", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


@app.exception_handler(psycopg.Error)
async def database_error(request: Request, exc: psycopg.Error):
    """Return clean JSON (which the frontend can display) instead of a bare 500 page."""
    log.error("Database error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse({"detail": "Database temporarily unavailable. Please try again."}, status_code=503)


@app.exception_handler(RuntimeError)
async def runtime_error(request: Request, exc: RuntimeError):
    # db.conn() raises RuntimeError when DATABASE_URL is missing; anything else is a genuine bug.
    if "DATABASE_URL" in str(exc):
        log.error("Configuration error on %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse({"detail": "Server is not fully configured."}, status_code=503)
    log.exception("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse({"detail": "Internal server error", "error": type(exc).__name__}, status_code=500)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    # Names the kind of failure (never the message, which could hold private data); the full traceback is in the logs.
    log.exception("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse({"detail": "Internal server error", "error": type(exc).__name__}, status_code=500)


def _failure_router(exc: BaseException) -> APIRouter:
    """If the mail code itself cannot be imported, answer every request with the reason instead of a blank 500."""
    packages = {}
    for name in ("fastapi", "starlette", "pydantic", "psycopg", "svix", "httpx", "dotenv"):
        try:
            importlib.import_module(name)
            packages[name] = "ok"
        except Exception as e:  # noqa: BLE001 - report every kind of failure
            packages[name] = f"{type(e).__name__}: {e}"[:200]
    body = {
        "detail": "The mail server started but its code could not be loaded.",
        "error": f"{type(exc).__name__}: {exc}"[:500],
        "python": sys.version.split()[0],
        "packages": packages,
        "hint": "'No module named X' means X did not install (check requirements.txt and the Vercel build log). "
                "A Python version far from 3.12 can also cause this: keep the .python-version file containing 3.12.",
    }
    log.error("Mail code failed to import: %s: %s", type(exc).__name__, exc)
    failing = APIRouter()

    @failing.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"], include_in_schema=False)
    async def report(full_path: str):
        return JSONResponse(body, status_code=500, headers={"Cache-Control": "no-store"})

    return failing


try:
    from mailcore.routes import router
except Exception as import_error:  # noqa: BLE001
    router = _failure_router(import_error)

app.include_router(router)

if PUBLIC.is_dir():
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="frontend")
