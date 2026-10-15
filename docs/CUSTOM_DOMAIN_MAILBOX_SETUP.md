# Custom-domain mailbox setup and verification

This app uses the existing Supabase tables `business_email_addresses`, `mailboxes`, `email_messages`, and `email_recipients`. It does not use a frontend-only list to decide which addresses can receive mail.

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

The included login flow is currently restricted to `MAIL_ADMIN_EMAIL` (defaults to `admin@<MAIL_DOMAIN>`). Database permissions support mailbox-scoped access, but separate-user login is not enabled by this patch. Keep that single-admin behavior until the intended user-to-mailbox assignments are defined and then implement and test the matching login/authorization policy explicitly.
