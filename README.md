# RT Crackers Mail (Supabase + Vercel)

A private, **Gmail / Outlook-style webmail** for the 10 RT Crackers business mailboxes: `admin`, `sales`, `support`, `account`, `noreply`, `billing`, `contact`, `help`, `info` and `orders` at `@rtcrackers.com`.
Mail is sent and received through **Resend**, stored in your **Supabase Postgres**, and protected by **Supabase Auth**. The server is a small **FastAPI** app; the interface is plain HTML/CSS/JavaScript with no build step.

![Main screen](docs/screenshots/01-main-layout-annotated.png)

> **Full guide:** [`docs/RTC_Mail_Feature_and_Code_Guide.pdf`](docs/RTC_Mail_Feature_and_Code_Guide.pdf) - every feature explained with real code excerpts, screenshots, architecture diagrams, API reference and test results. Screenshots (fictitious sample data) are in [`docs/screenshots/`](docs/screenshots).

**The database schema is unchanged.** `mailcore/db.py`, `supabase/*.sql` and `tests/schema.sql` are identical to the original; every feature uses columns that already existed.

## Features

**Reading and navigation**
- Three-pane layout: folders (Inbox, Starred, Sent, Drafts, Trash with counts), message list, reader.
- Switch between all 10 mailboxes with unread badges; you stay in the same folder.
- Gmail-style dates, unread styling, avatars, expandable message details, delivery-status chips on sent mail.
- Phone layout: one pane at a time with a Back button and floating Compose button.

**Organizing**
- Star / unstar (list, reader or `S`); Starred folder across inbox and sent mail.
- Checkboxes with select-all and bulk actions: trash, mark read/unread, star. Hover quick actions on every row.
- Trash with **Undo**, Restore, Delete forever and Empty Trash (soft delete via `deleted_at`).
- Instant, optimistic updates that revert automatically if the server rejects a change.

**Writing**
- Compose with From picker, Cc/Bcc, `Ctrl+Enter` to send.
- **Drafts** with autosave (about 1.5 s after you stop typing, when you leave the screen or hide the tab); resume, send or delete from the Drafts folder; a failed send keeps the draft.
- **Reply, Reply all, Forward** with quoted text; your own address and duplicates are removed; HTML-only mail is quoted as clean plain text.

**Finding mail**
- Search operators: `from:`, `to:`, `subject:`, `is:unread`, `is:read`, `is:starred`, combinable with free text.
- "Load more" paging (50 per page, 200 max per request).

**Delivery and sync**
- Delivery status from Resend webhooks: Delivered, Bounced, Failed, Spam report (a bounce can never be hidden by a late event).
- Incoming mail via webhook, 60-second auto-sync and *Import older mail*; one sync at a time; rate-limited Resend client with retry.

**Privacy and security**
- Remote images blocked by default (no tracking pixels); *Load images* per message.
- HTML mail rendered in a sandboxed iframe with its own Content-Security-Policy.
- Security headers on every response, `no-store` on private API responses, per-mailbox permissions enforced in SQL, input limits everywhere.

**Keyboard shortcuts** (press `?` in the app)

| Key | Action | Key | Action |
| --- | --- | --- | --- |
| `C` | Compose | `S` | Star / unstar |
| `/` | Search | `#` / `Delete` | Move to Trash |
| `J` / `K` | Next / previous | `Shift+I` / `Shift+U` | Mark read / unread |
| `Enter` / `O` | Open | `R` / `A` / `F` | Reply / Reply all / Forward |
| `X` | Select | `U` / `Esc` | Back / clear selection |
| `G` then `I S T D B` | Go to Inbox, Starred, Sent, Drafts, Trash | `?` | Help |

## Deploy on Vercel

1. Push this folder to a GitHub repo and import it in Vercel (Framework: Other/FastAPI; no build command).
2. Add the variables from `.env.example` (see `ENVIRONMENT.md`).
3. Deploy, open the site, and sign in with the Supabase Auth user `admin@rtcrackers.com`.
4. Use **Import older mail** once for the first import.
5. In Resend, point the webhook at `https://<your-domain>/api/webhooks/resend`, enable **email.received** plus the delivery events (**email.delivered, email.bounced, email.complained, email.failed**), and set `RESEND_WEBHOOK_SECRET`.

The server holds the Postgres connection; the database URL is never exposed to the browser.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Supabase Postgres (server-side only) |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | Supabase Auth |
| `RESEND_API_KEY`, `RESEND_WEBHOOK_SECRET` | Resend sending/receiving and webhook verification |
| `MAIL_DOMAIN` | Company mail domain (default `rtcrackers.com`) |
| `MAIL_ADMIN_EMAIL` | The one account allowed to sign in (default `admin@<MAIL_DOMAIN>`) |
| `SYNC_BUDGET_SECONDS` | Time limit for one sync (default 45) |
| `MAIL_DEV_NO_AUTH=1` | Local development only; ignored on Vercel |

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # fill in the values
uvicorn main:app --reload
```

For local work without Supabase Auth set `MAIL_DEV_NO_AUTH=1` in `.env`.

> **Keep secrets out of Git:** put real keys in `.env` (git-ignored), never in `.env.example`, which is committed and must stay empty. A unit test fails if a key ends up there.

## Troubleshooting

Open `https://<your-domain>/api/health`:

- `{"ok":true}` - the app is running. If something else fails, the browser's Network tab shows a readable `detail` message (for example *SUPABASE_URL and SUPABASE_ANON_KEY are not configured* or *Database temporarily unavailable*).
- `{"ok":false,"startup_error":"..."}` - the app could not load; the message names the cause (usually a missing package or an unsupported Python version).
- A Vercel "FUNCTION_INVOCATION_FAILED" page - the platform itself failed; open the project's **Logs** in Vercel and read the last lines.

After adding or changing Environment Variables in Vercel, **redeploy** - changes only apply to new deployments. On Vercel use Supabase's **pooler** connection string for `DATABASE_URL`, not the direct `db.<ref>.supabase.co` host.

## Tests

```bash
python -m pytest -q
```

Unit and API tests need nothing else. The database-backed tests use `tests/schema.sql` and are skipped automatically unless a scratch Postgres and the `psql` client are available:

```bash
createdb rtmail_test
TEST_DATABASE_URL=postgresql://user:pass@localhost/rtmail_test python -m pytest -q
```

Browser-logic checks (the real `app.js` in jsdom against a fake API: autosave, resume, send, undo, paging, shortcuts, mobile flow):

```bash
cd tests/frontend && npm install && npm test
```

Never point `TEST_DATABASE_URL` at your real Supabase database: the test schema drops and recreates tables.

## Project layout

| Path | Purpose |
| --- | --- |
| `main.py` | FastAPI app, security headers, error handlers, static front end |
| `mailcore/routes.py` | HTTP API (`/api/...`) and the Resend webhook |
| `mailcore/service.py` | Mailboxes, sync, search, send, drafts, star/trash actions, delivery status |
| `mailcore/auth.py` | Supabase login/refresh and mailbox permissions |
| `mailcore/resend_client.py` | Rate-limited Resend REST client |
| `mailcore/db.py` | Postgres connection (unchanged) |
| `public/` | Dependency-free front end (`index.html`, `style.css`, `app.js`) |
| `supabase/` | SQL for the Supabase SQL editor (unchanged) |
| `tests/` | Backend tests, `schema.sql`, and `frontend/smoke.js` |
| `docs/` | Feature and code guide (PDF) and screenshots |
