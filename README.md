# RT Crackers Mail (Supabase + Vercel)

Webmail for the **10 RT Crackers addresses only**: `admin`, `sales`, `support`, `account`, `noreply`, `billing`, `contact`, `help`, `info`, and `orders` at `@rtcrackers.com`.
The portal login is restricted to `admin@rtcrackers.com`; the other addresses are business mailboxes with their own responsibility.
Sends and receives through **Resend**; stores everything in your Supabase Postgres.

## Deploy on Vercel

1. Push this folder to a GitHub repo and import it in Vercel (Framework: Other/FastAPI; no build command).
2. Add the variables from `.env.example`.
3. Deploy, open the site, and sign in with the Supabase Auth user `admin@rtcrackers.com`.
4. Use **Import older mail** for the first import.

The server uses a server-side Postgres connection and does not expose the database URL to the browser.
