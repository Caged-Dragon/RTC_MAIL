# RT Crackers Mail (Supabase + Vercel)

A private, **Gmail / Outlook-style webmail** for RT Crackers business mailboxes on `@rtcrackers.com`. The app discovers every active address for `MAIL_DOMAIN` from `business_email_addresses`; it does not require a hard-coded list of ten local-parts. The examples and preferred display order are not an allowlist.
Mail is sent and received through **Resend**, stored in your **Supabase Postgres**, and protected by **Supabase Auth**. The server is a small **FastAPI** app; the interface is plain HTML/CSS/JavaScript with no build step.

![Main screen](docs/screenshots/01-main-layout-annotated.png)

> **Full guide:** [`docs/RTC_Mail_Feature_and_Code_Guide.pdf`](docs/RTC_Mail_Feature_and_Code_Guide.pdf) - every feature explained with real code excerpts, screenshots, architecture diagrams, API reference and test results. Screenshots (fictitious sample data) are in [`docs/screenshots/`](docs/screenshots).

**The database schema is unchanged.** `mailcore/db.py`, `supabase/*.sql` and `tests/schema.sql` are identical to the original; every feature uses columns that already existed.

## Features

**Speed**
- The first screen needs **one** request (`/api/bootstrap`: mailboxes, counts and the first page of mail) and shows a skeleton instantly; branding loads afterwards.
- Mailboxes and permissions are cached in memory; a sync checks which emails are already stored with **one** query for the whole page (it used one database connection *per email*).
- New mail appears within about 20 seconds through a tiny `/api/pulse` check; opening a message or trashing several at once no longer triggers extra count requests.
- Static files are cacheable by the browser and Vercel's CDN, so a page load does not have to wake the Python function.

**Reading and navigation**
- Three-pane layout: folders (Inbox, Starred, Sent, Drafts, Trash with counts), message list, reader.
- Switch between all configured mailboxes with unread badges; you stay in the same folder.
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

## Make incoming mail instant and the site fast (Vercel checklist)

1. **Resend webhook (instant mail).** In Resend open *Webhooks*, add `https://<your-domain>/api/webhooks/resend`, tick **email.received** plus **email.delivered, email.bounced, email.complained, email.failed**, copy the signing secret (`whsec_...`) into Vercel as `RESEND_WEBHOOK_SECRET`, then redeploy. Without it, mail only arrives when the browser polls (every minute).
2. **Function region.** In Supabase open *Project Settings > General* and note the region. In Vercel open *Settings > Functions* and set the **Function Region** to the closest one. Every database call crosses that distance, so a far-away region is the biggest remaining cost.
3. **Connection string.** Use Supabase's **pooler** connection string for `DATABASE_URL` (not the direct `db.<ref>.supabase.co` host).
4. **Set up the real mailbox addresses.** The source of truth is `public.business_email_addresses`. Do not assume the starter examples are your address list. See [`docs/CUSTOM_DOMAIN_MAILBOX_SETUP.md`](docs/CUSTOM_DOMAIN_MAILBOX_SETUP.md).
5. **Redeploy after changing any environment variable.**

## Deploy on Vercel

1. Push this folder to a GitHub repo and import it in Vercel (Framework: Other/FastAPI; no build command).
2. Add the variables from `.env.example` (see `ENVIRONMENT.md`). `SUPABASE_URL` is prefilled for project `ybvsjuyznkdwvrnjnelr`; you must still supply that project’s own `DATABASE_URL` and `SUPABASE_ANON_KEY` from its dashboard.
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
| `MAILBOX_ORDER` | Optional comma-separated display-order preference; it never filters addresses |
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

- `{"ok":true}` - the app process is running. This liveness route does not verify credentials or database access.

Then open `https://<your-domain>/api/health/ready` after configuring the environment. It checks database connectivity, the six existing mail tables, and whether Supabase Auth, Resend sending, and Resend webhook verification are configured. It returns booleans only and never shows secrets. It returns HTTP 503 until every required check passes.

- On `/api/health/ready`, `{"ok":true,"checks":{...}}` with HTTP 200 means the database and all required provider configuration checks passed; HTTP 503 means at least one check is false.
- `{"ok":false,"startup_error":"..."}` on `/api/health` - the app could not load; the message names the cause (usually a missing package or an unsupported Python version).
- A Vercel "FUNCTION_INVOCATION_FAILED" page - the platform itself failed; open the project's **Logs** in Vercel and read the last lines.

Dependencies are declared in **both** `requirements.txt` and `pyproject.toml` (a unit test keeps them identical), because hosts such as Vercel may install from either. If a deploy fails at startup with `No module named 'fastapi'`, the install step did not run - check the build log.

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
