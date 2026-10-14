"""RT Crackers Mail (Supabase + Vercel)."""
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rtmail")

PUBLIC = Path(__file__).resolve().parent / "public"

# If anything fails while the application modules load (a missing package, an incompatible Python version, ...)
# the function would otherwise die with an opaque platform error. Keep it alive and say what went wrong instead.
STARTUP_ERROR = None
try:
    import psycopg
    from mailcore.routes import router
except Exception as exc:  # noqa: BLE001 - deliberately broad, the error is reported below
    psycopg = None
    router = None
    STARTUP_ERROR = f"{type(exc).__name__}: {exc}"[:400]
    log.exception("Application failed to start")

# Kept deliberately light: the app only loads its own scripts and renders untrusted mail
# inside a sandboxed iframe that carries its own policy.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": "script-src 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'",
}


# index.html, style.css and app.js are identical for every visitor. Letting the browser and Vercel's CDN keep them
# means a page load does not have to wake the Python function (a cold start) just to fetch three static files.
STATIC_CACHE = "public, max-age=60, s-maxage=300, stale-while-revalidate=3600"


class SecurityHeadersMiddleware:
    """Adds the headers above to every HTTP response. Plain ASGI (no BaseHTTPMiddleware) so it is
    lightweight and behaves the same on any host, including Vercel."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_static = scope.get("method") in ("GET", "HEAD") and not scope.get("path", "").startswith("/api")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    if name not in headers:
                        headers[name] = value
                if is_static and message.get("status") == 200 and "cache-control" not in headers:
                    headers["Cache-Control"] = STATIC_CACHE
            await send(message)

        await self.app(scope, receive, send_with_headers)


app = FastAPI(title="RT Crackers Mail", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SecurityHeadersMiddleware)

if psycopg is not None:
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


if router is not None:
    app.include_router(router)
else:
    @app.get("/api/health")
    def health_when_broken():
        """Visible in the browser: shows exactly why the app could not start."""
        return JSONResponse({"ok": False, "startup_error": STARTUP_ERROR}, status_code=503)

if PUBLIC.is_dir():
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="frontend")
