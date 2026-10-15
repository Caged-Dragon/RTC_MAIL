# Custom-domain mailbox setup and verification

# Connect this website to your supplied Supabase project

The first value supplied, `ybvsjuyznkdwvrnjnelr`, is the Supabase **organization ID**, not the project reference. The connected project is **RtCrackers Cart**, project ref `ypmiinmkyvzdpakbkers`, with API URL `https://ypmiinmkyvzdpakbkers.supabase.co`. The existing production database has already been inspected: all required mail tables exist, and the ten active `@rtcrackers.com` addresses are registered below. No schema rebuild is required. Use credentials from this exact project; do not copy credentials from a different project.

The connected database currently contains these ten active mailboxes: `account@rtcrackers.com`, `admin@rtcrackers.com`, `billing@rtcrackers.com`, `contact@rtcrackers.com`, `help@rtcrackers.com`, `info@rtcrackers.com`, `noreply@rtcrackers.com`, `orders@rtcrackers.com`, `sales@rtcrackers.com`, and `support@rtcrackers.com`. First, the read-only check below can be run from this project's SQL Editor to confirm the required mail tables:

```sql
select
  to_regclass('public.business_email_purposes') as business_email_purposes,
  to_regclass('public.business_email_addresses') as business_email_addresses,
  to_regclass('public.mailboxes') as mailboxes,
  to_regclass('public.mailbox_access') as mailbox_access,
  to_regclass('public.email_messages') as email_messages,
  to_regclass('public.email_recipients') as email_recipients;
```

The connected project has been checked directly: all six required mail tables exist, ten active domain mailboxes are present, and the project migration history includes the mail archive/RLS hardening migrations. No schema rebuild is needed to connect this application. Do not rerun `supabase/mail_exclusive_archive.sql` as a setup step: it changes archive triggers and RLS policies (including `customer_addresses` policies), and repeat execution is not a substitute for a reviewed migration.

1. In **Project Settings → Database → Connection string**, select the pooler connection string recommended for your hosting platform and copy its URI into the server-side `DATABASE_URL` environment variable. It must point to this same project. Replace any password placeholder locally in your hosting dashboard; do not paste the connection string into this chat or commit it to Git.
2. In **Project Settings → API** (or the project's API keys panel), copy the publishable/anon key into `SUPABASE_ANON_KEY`. Set `SUPABASE_PROJECT_REF=ypmiinmkyvzdpakbkers` and `SUPABASE_URL=https://ypmiinmkyvzdpakbkers.supabase.co`. The app uses these for Supabase Auth; `DATABASE_URL` is the separate server-side Postgres connection.
3. In the hosting dashboard, add `MAIL_DOMAIN=rtcrackers.com`, `RESEND_API_KEY`, `RESEND_WEBHOOK_SECRET`, `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `MAIL_ADMIN_EMAIL`. Keep all keys server-side. Redeploy after saving environment variables.
4. Open `https://<your-site>/api/health/ready`. Do not proceed until it returns `ok: true` and all checks are `true`. If `database` is false, verify the database URI belongs to project `ypmiinmkyvzdpakbkers`, the password is current, and the correct pooler/region was selected. If `supabase_auth_configured` is false, check the Auth URL and key. If `database_project_matches` or `supabase_auth_project_matches` is false, update the associated URL/connection string to the `ypmiinmkyvzdpakbkers` project; `ybvsjuyznkdwvrnjnelr` is only the organization ID. If a Resend check is false, set the corresponding Resend environment variable.
5. In the target project's SQL Editor, run the read-only mailbox queries in this guide first. Only run `supabase/mail_portal_setup.sql` if its tables/types do not already exist or are incomplete. Review SQL before execution; do not reset or drop existing mail tables.

This app uses the existing Supabase tables `business_email_addresses`, `mailboxes`, `email_messages`, and `email_recipients`. The live project already contains these tables and ten active addresses. It does not use a frontend-only list to decide which addresses can receive mail.

## 1. Check the real configured addresses

Run this in the Supabase SQL Editor before changing anything:

```sql
select lower(b.email_address) as email_address,
       b.display_name,
       b.is_active,
       p.purpose_code
from public.business_email_addresses b
join public.business_email_purposes p using (email_purpose_id)
where lower(split_part(b.email_address, '@', 2)) = 'rtcrackers.com'
order by lower(b.email_address);
```

Review the result against the actual mailbox list. No addresses are inferred from the source-code examples. The app discovers every active row for the configured `MAIL_DOMAIN`. Inactive rows and addresses for another domain are not displayed or routed.

The revised setup SQL no longer inserts example addresses on a fresh setup. It does not delete placeholder rows created by an earlier run. Compare the query results with your actual list, and deactivate/remove only entries that you have verified are placeholders; do not bulk-delete without reviewing the result first.

If an actual address is missing, add it to `public.business_email_addresses` using the appropriate existing purpose. For an address with no dedicated purpose, the `general` purpose may be used. For example, after checking the purpose ID in your own database:

```sql
select email_purpose_id, purpose_code, purpose_name
from public.business_email_purposes
order by purpose_code;
```

Do not paste placeholder addresses or run a bulk insert until you have the actual list. The application creates a matching `mailboxes` row for each active address when the portal bootstraps or when an inbound webhook is first processed.

## 2. Check mailbox receiving and sending switches

```sql
select m.email_address,
       m.is_active,
       m.is_receiving_enabled,
       m.is_sending_enabled,
       m.mailbox_id
from public.mailboxes m
where lower(split_part(m.email_address, '@', 2)) = 'rtcrackers.com'
order by m.email_address;
```

Enable receiving only for addresses meant to receive mail. Keep sending enabled only for mailboxes that the app should allow as a From address. Sender-domain authorization still has to be configured in Resend; the local switch does not grant provider authorization.

## 3. Resend configuration

In Resend, verify that the domain shown is `rtcrackers.com`, that **Enable Receiving** is on, and that its MX record is verified. Use the exact DNS values displayed by Resend; never copy a truncated value from a screenshot. Check any existing mail provider before changing MX records.

Register this webhook:

`https://mail.rtcrackers.com/api/webhooks/resend`

Subscribe to `email.received`. To keep sent-message status updates, also subscribe to `email.delivered`, `email.bounced`, `email.complained`, and `email.failed`. Save the endpoint's signing secret in the deployment environment as `RESEND_WEBHOOK_SECRET`, alongside `RESEND_API_KEY`, `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `MAIL_DOMAIN`. Redeploy after changing environment variables. Never put these values in frontend JavaScript.

The webhook route is POST-only. Opening it in a browser sends GET and may show `405 Method Not Allowed`; that is expected and is not a delivery test. Check the webhook delivery log in Resend for the signed POST event and HTTP response instead.

## 4. End-to-end test for every address

1. Send an external email from Gmail or another provider to the exact address.
2. Confirm the email appears in Resend's receiving/logs view.
3. Confirm the `email.received` webhook delivered successfully to the URL above.
4. Sign in to the website and open only the intended mailbox. Confirm the sender, subject, date, and body.
5. Open the message and confirm unread count decreases.
6. Repeat for every real address, including a test message addressed to two mailboxes, if sharing by recipient is intended.
7. Send a test message through the existing compose UI from each permitted sender address.

The app stores one mailbox-specific copy per matching configured recipient. Provider IDs are stored deterministically per mailbox, and the database's existing unique index prevents duplicate copies when Resend retries a webhook. A recipient not present among active, receiving-enabled mailboxes is not routed into another inbox.

## 5. Access control

The included login flow is currently restricted to `MAIL_ADMIN_EMAIL` (defaults to `admin@<MAIL_DOMAIN>`). The live database has active admin allowlist entries for `admin@rtcrackers.com` and `developer@rtcrackers.com`, but the app authorizes only the address specified by `MAIL_ADMIN_EMAIL` as the portal administrator; mailbox-specific `mailbox_access` rows are currently empty. This retains the current administrator-only access model until you decide whether the ten mailboxes need separate users.
