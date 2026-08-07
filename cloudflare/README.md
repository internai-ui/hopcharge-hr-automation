# Form-tracking redirect on Cloudflare Workers (free tier)

Replaces app.py's `/t/<token>` route with the same logic running on
Cloudflare's infrastructure instead of your own server — no public URL of
your own needed. Free tier: 100,000 requests/day, which is far more than a
recruiting pipeline will ever use.

## What you need

- A free Cloudflare account: https://dash.cloudflare.com/sign-up
- Node.js installed locally, to run `wrangler` (Cloudflare's CLI) — or you
  can skip Node entirely and paste the code into the dashboard instead (see
  "Dashboard-only alternative" below).

## 1. Create the KV namespace (where click data lives)

```bash
cd cloudflare
npx wrangler login          # opens a browser to authorize wrangler once
npx wrangler kv namespace create TRACKING_KV
```

This prints something like:
```
{ binding = "TRACKING_KV", id = "abcd1234..." }
```

Copy that `id` into `wrangler.toml`, replacing `REPLACE_WITH_YOUR_KV_NAMESPACE_ID`.

## 2. Deploy the Worker

```bash
npx wrangler deploy
```

This prints your Worker's URL, e.g.:
```
https://hopcharge-form-tracking.<your-subdomain>.workers.dev
```
That's the URL to use as the "public base URL" in the Send Emails tracking
config — paste it there exactly as printed, no `/t/...` suffix (the app adds
that per-candidate).

## 3. Create an API token so this app can talk to Cloudflare

The dashboard app needs to (a) push each new tracking token to the Worker's
KV store the moment a campaign email goes out, and (b) periodically pull
click data back. Both need a scoped API token:

1. Go to https://dash.cloudflare.com/profile/api-tokens
2. **Create Token** → **Create Custom Token**
3. Permissions: **Account** → **Workers KV Storage** → **Edit**
4. Account Resources: your account
5. Create it, copy the token (shown once)

You'll also need your **Account ID** — it's on the right sidebar of any
Cloudflare dashboard page (or `npx wrangler whoami`), and the **KV
namespace ID** from step 1.

## 4. Add the credentials to this app

Add these three lines to `neon.env` (never commit this file, never paste
these values into a chat — it's already gitignored):

```
CLOUDFLARE_ACCOUNT_ID=your-account-id
CLOUDFLARE_KV_NAMESPACE_ID=the-namespace-id-from-step-1
CLOUDFLARE_API_TOKEN=the-token-from-step-3
```

Restart the app. `form_tracking.py` checks for these automatically — with
all three set, every new tracking link gets pushed to the Worker on issue,
and a background job pulls click data back every 5 minutes (same cadence as
the Gmail reply poller). With any of the three missing, everything falls
back to no-ops and the self-hosted `/t/<token>` route keeps working exactly
as before — this is purely additive.

## 5. Point the Send Emails config at the Worker

In the dashboard's Send Emails page, set the tracking base URL to the
Worker's URL from step 2, instead of your own server's address. That's the
only UI-side change — the link-building code already takes this as
configuration, it doesn't care what's actually running behind that URL.

## Dashboard-only alternative (no Node/wrangler)

1. In the Cloudflare dashboard: **Workers & Pages** → **Create** → **Create
   Worker** → give it a name → **Deploy** (deploys a placeholder first).
2. **Edit code** → paste in the contents of `worker.js` → **Deploy**.
3. **Settings** → **Bindings** → **Add binding** → **KV Namespace** → create
   a new namespace, bind it as `TRACKING_KV`.
4. Your Worker's URL is shown at the top of its dashboard page.
5. Continue from step 3 above (API token) the same way.

## Verifying it works

After deploying, visit `https://<your-worker>.workers.dev/t/nonexistent` —
you should get either a redirect to your configured form (if
`config:tracking` is set) or "Invalid or expired link." Either response
means the Worker is running correctly; a Cloudflare error page means
something's misconfigured (check the KV binding name is exactly
`TRACKING_KV`).
