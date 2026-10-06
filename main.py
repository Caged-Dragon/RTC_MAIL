"""RT Crackers Mail (Supabase + Vercel)."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from mailcore.routes import router

PUBLIC = Path(__file__).resolve().parent / "public"

app = FastAPI(title="RT Crackers Mail", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router)

if PUBLIC.is_dir():
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="frontend")
