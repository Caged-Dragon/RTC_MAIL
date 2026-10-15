# Corrected mailbox receiving build

## What changed

- Removed the hard-coded local-part filter from mailbox discovery. The app now discovers all active `business_email_addresses` under `MAIL_DOMAIN`; `MAILBOX_ORDER` affects ordering only.
- Made the initial mailbox creation work even when the `mailboxes` table is empty.
- Ensured the inbound webhook attempts mailbox discovery itself, so receiving does not depend on someone opening the website or running a sync first.
- Made each inbound message's stored `resend_email_id` deterministic per mailbox. This prevents concurrent/retried deliveries from racing to claim the same unsuffixed unique ID. Old unsuffixed inbound IDs are still recognized, so historical messages will not be duplicated on retry.
- Updated the setup SQL to stop inserting example business email addresses automatically. It uses only actual rows already present in `business_email_addresses` for `rtcrackers.com`.
- Added `docs/CUSTOM_DOMAIN_MAILBOX_SETUP.md` with database checks, Resend webhook setup, and end-to-end testing steps.

## What did not change

- Existing frontend architecture and styling.
- Existing email tables and unique index; no new database table or schema migration is needed for these code changes.
- Existing Resend sending implementation.
- Existing authentication policy: normal sign-in is still restricted to `MAIL_ADMIN_EMAIL`. Separate-user access needs explicit user-to-mailbox assignments and is not enabled by this patch.
- DNS settings and Resend dashboard configuration were not changed or remotely tested.

## Deploy

1. Keep your existing production environment variables and secrets in Vercel. Do not copy `.env.local` from the original archive; it is intentionally excluded from this package. Use `.env.example` only as a name checklist.
2. In Supabase, run the address-list query in `docs/CUSTOM_DOMAIN_MAILBOX_SETUP.md` and compare results with your real addresses. The setup SQL change does not remove placeholder rows created by older runs.
3. Deploy this project without running the setup SQL solely for this code patch; the existing schema and data should be preserved.
4. In Resend, confirm receiving is enabled for `rtcrackers.com` and the MX record is verified. Confirm a webhook at `https://mail.rtcrackers.com/api/webhooks/resend` subscribes to `email.received` and that its signing secret matches `RESEND_WEBHOOK_SECRET` in the deployment environment.
5. Send an external email to each actual address and check Resend webhook delivery logs plus the correct website inbox. Browser GET to the webhook URL may return `405 Method Not Allowed` by design; use Resend's signed POST delivery logs to validate it.

## Validation performed here

- Python compilation passed for the updated backend modules and API test file.
- The full pytest suite could not be collected in this environment because Python dependencies `svix` and `psycopg` are not installed; there was no network access available to install them. No claim is made that the updated full suite passed.
- Database-backed mailbox/webhook tests and live Resend/DNS delivery were not run from this environment.

## Supabase project connection update

- Corrected the Supabase project mapping after inspecting the connected database: `ybvsjuyznkdwvrnjnelr` is the organization ID; the actual project is RtCrackers Cart, ref `ypmiinmkyvzdpakbkers`. Updated default Auth URL and readiness checks to validate that Auth/database configuration points to the right project.
- Added `/api/health/ready`, which checks a real Postgres connection, the six required mail tables, and whether Auth/Resend environment variables are present; it returns only booleans and no secrets.
- Marked `supabase/mail_exclusive_archive.sql` as historical project-specific SQL because its header identifies the old project; it must not be run against the target blindly.
- Direct connection to the live database is not verified: a project ID alone does not provide the database password/connection string or Auth key. The actual project credentials must be set in the hosting environment by the owner.

## Page-load performance and live-project alignment

- Confirmed the Supabase organization ID (`ybvsjuyznkdwvrnjnelr`) differs from the connected project ref (`ypmiinmkyvzdpakbkers`). Corrected the sample `SUPABASE_URL` to the actual project API URL. Use the matching project's `DATABASE_URL` and publishable/anon key in Vercel.
- Changed cold-start `/api/bootstrap` to reuse a single database connection for periodic mailbox reconciliation, mailbox discovery, counts, draft badges, and the first page of messages. Previously, a cold cache could open up to three connections.
- Combined unread counts, latest inbox message IDs, and draft counts into one aggregate query (instead of two queries) for bootstrap and lightweight `/api/pulse` polling.
- Added a cold-cache performance regression test asserting bootstrap uses one connection.
- Updated the setup guide to reflect that the live project's mail tables and mail-archive hardening migrations were inspected; no migration is required for the existing schema.



## Readiness check fix for transaction-pooler URLs

- Fixed `database_project_matches` to normalize `DATABASE_URL` before `urlsplit()` parses it. An unescaped `#` in a password is treated by URL parsers as a fragment marker; the database connector already normalized the URL for the actual connection, which is why `database` and `mail_schema` could be true while the project check stayed false.
- Added a regression case using a transaction-pooler URL whose password contains `#`, without connecting to a real database or exposing credentials.
- Passwords in `DATABASE_URL` should still be URL-encoded; for example, encode `#` as `%23`. If the password was exposed in chat, reset it first and use the new password only in Vercel.
