# RT Crackers Mail (Supabase + Vercel)

Webmail for the **10 RT Crackers addresses only**: `admin`, `sales`, `support`, `account`, `noreply`, `billing`, `contact`, `help`, `info`, and `orders` at `@rtcrackers.com`.
The portal login is restricted to `admin@rtcrackers.com`; the other addresses are business mailboxes with their own responsibility.
Sends and receives through **Resend**; stores everything in your Supabase Postgres (no more SQLite).

## What changed from the local version

| | Local version | This version |
|---|---|---|
| Mailboxes | hard-coded | read from `business_email_addresses` (+ `mailboxes`), restricted to the 10 RT Crackers addresses |
| Storage | SQLite file | Supabase tables `email_messages`, `email_recipients` |
| Login | none | Supabase Auth (email + password) |
| Who sees what | everyone sees all | `admins` (active) see all; others only mailboxes in `mailbox_access` |
| Sync | timer in the open page | same, plus Resend webhook (optional) and "Import older mail" |

A new row in `business_email_addresses` gets its mailbox created automatically on the next page load/sync.
Set `is_active=false` there (or `mailboxes.is_sending_enabled/is_receiving_enabled=false`) to retire an address.

## Deploy on Vercel

1. Push this folder to a GitHub repo and import it in Vercel (Framework: Other/FastAPI; no build command).
2. Project Settings > Environment Variables (see `.env.example`):
   - `RESEND_API_KEY` (Full access key, so received mail can be read)
   - `MAIL_DOMAIN` = `rtcrackers.com`
   - `DATABASE_URL`: Supabase > Connect > **Transaction pooler** URI (port 6543), with your database password
   - `SUPABASE_URL` = `https://ypmiinmkyvzdpakbkers.supabase.co`
   - `SUPABASE_ANON_KEY`: the **publishable/anon** key (never the service_role key)
   - `RESEND_WEBHOOK_SECRET`: only if you add the webhook (below)
3. Deploy, open the site, and sign in only with the Supabase Auth user `admin@rtcrackers.com`.
4. First time only: click **Import older mail** to pull earlier messages from Resend.

Optional live delivery: in Resend add a webhook for `email.received` to `https://<your-domain>/api/webhooks/resend`
and put its signing secret in `RESEND_WEBHOOK_SECRET`. Without the secret the webhook endpoint stays disabled.

## Resend setup (unchanged)

1. Verify `rtcrackers.com` in Resend and create a **Full access** API key.
2. Enable receiving and add the MX record Resend shows. If the domain already receives mail somewhere else
   (Google Workspace, Zoho...), pointing MX at Resend will redirect it. Check first.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env                                  # fill it in
uvicorn main:app --reload --port 8000
```

`MAIL_DEV_NO_AUTH=1` in `.env` skips login for local use only; it is ignored on Vercel.

Tests (Resend and Supabase Auth mocked): load `tests/schema.sql` into any scratch Postgres, then
`TEST_DATABASE_URL=postgresql://user:pass@localhost/db python -m pytest -q` (needs `psql` on PATH).
**Never point tests at your real Supabase database**: the schema file drops and recreates tables.

## Notes

- Mail sent to two of your addresses is stored once per mailbox. `email_messages.resend_email_id` is globally unique,
  so extra copies are stored as `<id>#<mailbox_id>`.
- Message threads (`email_threads`) are not used yet; `thread_id` stays empty.
- The server connects with the database owner role, so Supabase RLS does not apply to it. Permissions are enforced
  in the app (`mailcore/auth.py`). Do not expose `DATABASE_URL` anywhere public.


## RT Crackers production database integration

This package is adapted to the current `RtCrackers Cart` Supabase project (`ypmiinmkyvzdpakbkers`). It reuses `public.admin_access` for administrator authorization and adds only isolated mail tables (`business_email_*`, `mailboxes`, `mailbox_access`, `email_messages`, `email_recipients`) through `supabase/mail_portal_setup.sql`. It does not modify the existing products, orders, offers, company, or customer tables.
