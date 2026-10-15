# Mail website environment variables

The supplied Supabase project URL below is public configuration. Keep all keys and the database connection string private.

```env
RESEND_API_KEY=YOUR_RESEND_API_KEY
RESEND_WEBHOOK_SECRET=YOUR_RESEND_WEBHOOK_SECRET
MAIL_DOMAIN=rtcrackers.com
MAILBOX_ORDER=admin,sales,support,account,noreply,billing,contact,help,info,orders
DATABASE_URL=YOUR_SUPABASE_POOLER_CONNECTION_STRING
SUPABASE_URL=https://ybvsjuyznkdwvrnjnelr.supabase.co
SUPABASE_ANON_KEY=YOUR_SUPABASE_PUBLISHABLE_OR_ANON_KEY
SYNC_BUDGET_SECONDS=45
MAIL_ADMIN_EMAIL=admin@rtcrackers.com
ORDER_FROM_EMAIL=RT Crackers <orders@rtcrackers.com>
ORDER_SITE_URL=https://YOUR-CART-DOMAIN
ADMIN_SITE_URL=https://YOUR-ADMIN-DOMAIN
```

The Supabase URL is prefilled for the project ID you provided (`ybvsjuyznkdwvrnjnelr`). A project ID/URL alone does not authorize a database connection: `DATABASE_URL` and `SUPABASE_ANON_KEY` must be obtained from that same Supabase project and configured on the server. Do not reuse connection credentials from another Supabase project. Keep the Resend key, webhook secret, and database connection string server-side only. `MAILBOX_ORDER` is only a visual sorting preference; the app discovers every active address for `MAIL_DOMAIN` from the database, including addresses not named in that list.

`MAIL_ADMIN_EMAIL` is optional (defaults to `admin@<MAIL_DOMAIN>`). `MAIL_DEV_NO_AUTH=1` disables login for local development only and is ignored on Vercel.
