# Vercel deployment and load-time checklist

## Correct Supabase project

The connected Supabase **organization ID** is `ybvsjuyznkdwvrnjnelr`. It is not a project reference. The mail database is project **RtCrackers Cart**, ref `ypmiinmkyvzdpakbkers`, API URL:

`https://ypmiinmkyvzdpakbkers.supabase.co`

Update the existing Vercel environment variable `SUPABASE_URL` to that exact URL. Keep `DATABASE_URL` and `SUPABASE_ANON_KEY` from this same project. The `DATABASE_URL` must remain server-only, use the Supabase-recommended pooler URI for Vercel, and must not be committed into Git.

No mail-table migration is required for the connected database: `business_email_purposes`, `business_email_addresses`, `mailboxes`, `mailbox_access`, `email_messages`, and `email_recipients` were all found in the live project. There are ten active `@rtcrackers.com` mailboxes already configured.

## Why `/api/health/ready` can return 404

A 404 on `https://mail.rtcrackers.com/api/health/ready` is not a database-credentials error. The route exists in `mailcore/routes.py`. A 404 means the deployment receiving that request is not serving this code version, or the custom domain is attached to a different Vercel project/deployment.

In Vercel:

1. Open the project connected to `mail.rtcrackers.com` and confirm its Git repository is `Caged-Dragon/RTC_MAIL` (or import this corrected code into the repository used by that project).
2. Confirm the Root Directory points to the folder containing `main.py`, `mailcore/`, `public/`, and `requirements.txt` (normally `.`).
3. Confirm the production branch is the branch containing the current code, then deploy the latest commit. Vercel's FastAPI runtime can detect a root-level `main.py` exporting `app`; do not add unrelated rewrites to mask a stale or mispointed deployment.
4. Check that `mail.rtcrackers.com` is assigned to that same Vercel project. If the project/domain binding is correct but the route is still 404, open the deployment details and verify its commit includes `GET /api/health/ready` from `mailcore/routes.py`.
5. After deploying and setting the variables, open `/api/health/ready` again. HTTP 200 with every check `true` means database connectivity, the existing mail schema, project-ref matching, and the configured Resend values passed. HTTP 503 means inspect the named false checks. A 404 means the request still is not reaching this app version.

## Load-time improvements in this version

- `/api/bootstrap` remains the only initial data request: it loads mailbox options, counts, drafts, and the first page of mail.
- Cold-start bootstrap reuses one Postgres connection for mailbox reconciliation, mailbox lookup, counts, and message listing instead of potentially opening three connections.
- `/api/pulse` and bootstrap now get unread counts, newest inbox message IDs, and draft counts from one grouped SQL query instead of two.
- Static frontend assets remain served from `public/`; keep their URLs stable for normal deploys and avoid routing asset requests through additional application/API fetches.

The app code can be prepared and committed from Git, but deployment still needs to be triggered by the connected Vercel project after the commit exists in the repository branch it builds.
