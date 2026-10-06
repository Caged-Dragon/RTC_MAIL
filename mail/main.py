"""RT Crackers Mail (Supabase + Vercel).  Local:  uvicorn main:app --reload --port 8000"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from mailcore.routes import router

PUBLIC = Path(__file__).resolve().parent / "public"

app = FastAPI(title="RT Crackers Mail", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router)
# On Vercel, files in /public are served by the CDN; this mount is for local runs. Mounted last so /api wins.
if PUBLIC.is_dir():
    app.mount("/", StaticFiles(directory=PUBLIC, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
