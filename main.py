"""RT Crackers Mail (Supabase + Vercel).

If anything stops the server from starting (a package that failed to install, a Python version mismatch,
a bad import) the site answers every request with a readable JSON explanation instead of an opaque 500,
so the cause can be read straight from the browser and the Vercel logs."""
import importlib
import json
import logging
import sys
from pathlib import Path

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


def _build_app():
    import psycopg
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles

    from mailcore.routes import router

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

    app.include_router(router)

    if PUBLIC.is_dir():
        app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="frontend")
    return app


def _startup_failure_app(exc: BaseException):
    """Plain ASGI app (needs no packages) that reports why the real app could not start."""
    packages = {}
    for name in ("fastapi", "starlette", "pydantic", "psycopg", "svix", "httpx", "dotenv"):
        try:
            importlib.import_module(name)
            packages[name] = "ok"
        except Exception as e:  # noqa: BLE001 - we want to report every kind of failure
            packages[name] = f"{type(e).__name__}: {e}"[:200]
    body = json.dumps({
        "detail": "The mail server failed to start.",
        "error": f"{type(exc).__name__}: {exc}"[:500],
        "python": sys.version.split()[0],
        "packages": packages,
        "hint": "Read 'error' and 'packages'. 'No module named X' means X did not install: check requirements.txt and the "
                "Vercel build log. A Python version far from 3.12 can also cause this: add a .python-version file containing 3.12.",
    }, indent=2).encode()
    log.error("Startup failed: %s: %s", type(exc).__name__, exc)

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        elif scope["type"] == "http":
            await send({"type": "http.response.start", "status": 500,
                        "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": body})

    return app


try:
    app = _build_app()
except Exception as exc:  # noqa: BLE001
    app = _startup_failure_app(exc)
