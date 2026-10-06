# RT Crackers Mail - Vercel deployment

This repository keeps the Mail application under `mail/`. The root `pyproject.toml` tells Vercel to use `mail.main:app`, and the root `requirements.txt` forwards dependency installation to `mail/requirements.txt`.

Recommended Vercel Root Directory: repository root (`.`).

Framework: FastAPI. No build command is required.
