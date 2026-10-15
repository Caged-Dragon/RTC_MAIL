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

- Python compilation check: passed.
- Pure-function/config/compliance tests: 44 passed.
- Database-backed mailbox/webhook tests were not run because this environment has no PostgreSQL test database, no `psycopg` installation, and no network access to install dependencies. No live Resend or DNS test was possible from this environment.
