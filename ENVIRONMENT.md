# Mail website environment variables

```env
RESEND_API_KEY=YOUR_RESEND_API_KEY
RESEND_WEBHOOK_SECRET=YOUR_RESEND_WEBHOOK_SECRET
MAIL_DOMAIN=rtcrackers.com
DATABASE_URL=YOUR_SUPABASE_POOLER_CONNECTION_STRING
SUPABASE_URL=https://ypmiinmkyvzdpakbkers.supabase.co
SUPABASE_ANON_KEY=YOUR_SUPABASE_PUBLISHABLE_OR_ANON_KEY
SYNC_BUDGET_SECONDS=45
MAIL_ADMIN_EMAIL=admin@rtcrackers.com
ORDER_FROM_EMAIL=RT Crackers <orders@rtcrackers.com>
ORDER_SITE_URL=https://YOUR-CART-DOMAIN
ADMIN_SITE_URL=https://YOUR-ADMIN-DOMAIN
```

Keep the Resend key, webhook secret, and database connection string server-side only.

Sign-in: **every active admin ID in the `admin_access` table can sign in** (the same Supabase IDs as admin.rtcrackers.com). `MAIL_ADMIN_EMAIL` is no longer a gate and can be removed.

`MAIL_ALLOWED_MX` (optional) = comma-separated regular expressions for MX hosts allowed to hold company mail; default is Resend's inbound server (`^inbound-smtp\.[a-z0-9-]+\.amazonaws\.com\.?$`).

`MAIL_ADMIN_EMAIL` is optional legacy. `MAIL_DEV_NO_AUTH=1` disables login for local development only and is ignored on Vercel.
