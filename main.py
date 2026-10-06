"""RT Crackers Mail (Supabase + Vercel). Local: uvicorn mail.main:app --reload"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from mail.mailcore.routes import router

PUBLIC = Path(__file__).resolve().parent / "public"

app = FastAPI(title="RT Crackers Mail", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router)

# Keep the frontend in the same FastAPI function for the Vercel deployment.
# API routes are registered before the static mount, so /api/* remains dynamic.
if PUBLIC.is_dir():
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("mail.main:app", host="127.0.0.1", port=8000, reload=True)
