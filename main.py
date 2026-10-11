"""RT Crackers Mail (Supabase + Vercel)."""
import logging
from pathlib import Path

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from mailcore.routes import router

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
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


app.include_router(router)

if PUBLIC.is_dir():
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="frontend")
