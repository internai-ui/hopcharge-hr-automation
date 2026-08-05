# HopCharge HR Dashboard — AWS Hosting Guide

Host the dashboard on a single AWS EC2 server, on your own domain, with HTTPS
and a login page. Total time: **about 45 minutes**. Cost: **~US$17–20/month**
(one `t3.small` instance + a few cents for the fixed IP).

> **Why one EC2 server (and not ECS/Fargate/Lambda)?**
> The app stores its working data (candidates, tracking, settings) as files on
> disk next to the code. A plain server keeps those files safe across restarts
> and code updates with zero extra moving parts. Container platforms lose local
> files on every redeploy and would need extra infrastructure (EFS, ECR, task
> definitions) for no benefit at this scale — roughly 10–30 concurrent HR users.

**The moving parts:**

```
you (browser) ──HTTPS──▶ nginx (port 443)  ──▶ uvicorn/FastAPI (port 8000, localhost only)
                          │  on the EC2 server          │
   dashboard.hopcharge.com┘                             ├─▶ /opt/hopcharge/data  (secrets + files)
                                                        └─▶ Neon Postgres (already in the cloud)
```

Your Neon database is already hosted — nothing about it changes. The server
just needs the same `DATABASE_URL` your Mac uses.

---

## Part 0 — What you need before starting

- [ ] An AWS account with billing set up (you have this).
- [ ] Access to your domain's DNS settings (wherever hopcharge.com is managed —
      GoDaddy, Route 53, Cloudflare, etc.).
- [ ] Your Neon `DATABASE_URL` and `EMPLOYEE_FIELD_KEY` (from your local `neon.env`).
- [ ] The two Google service-account JSON files (in the project folder).
- [ ] (Optional, for forgot-password emails) A Gmail **app password**:
      myaccount.google.com → Security → 2-Step Verification → App passwords.

Decide your dashboard address now, e.g. **`dashboard.hopcharge.com`**
(a subdomain leaves your main website untouched). The guide uses this as the
example — substitute yours everywhere.

---

## Part 1 — Launch the server (AWS Console, ~10 min)

1. Sign in at **console.aws.amazon.com** and set the region (top-right) to
   **US East (N. Virginia) us-east-1** — the same region as your Neon database,
   so every query is fast.
2. Go to **EC2 → Instances → Launch instances** and fill in:
   - **Name:** `hopcharge-dashboard`
   - **AMI:** Ubuntu Server 24.04 LTS (64-bit x86)
   - **Instance type:** `t3.small` (2 GB RAM — the resume parser's NLP model
     needs it; `t3.micro`'s 1 GB will run out of memory)
   - **Key pair:** Create new → name `hopcharge-server` → type RSA / `.pem` →
     **Create**. The file `hopcharge-server.pem` downloads — keep it safe,
     it is the only key to your server.
   - **Network settings → Edit:**
     - Allow SSH (port 22) — Source: **My IP**
     - Allow HTTP (port 80) — Source: Anywhere
     - Allow HTTPS (port 443) — Source: Anywhere
   - **Storage:** 20 GiB gp3
3. **Launch instance**, wait until it shows *Running*.
4. Give it a permanent IP: **EC2 → Elastic IPs → Allocate Elastic IP address**
   → Allocate → select it → **Actions → Associate** → choose your instance.
   **Write this IP down** — it's your server's address forever (a plain
   instance IP would change on every stop/start; the Elastic IP doesn't).

## Part 2 — Point your domain at it (~5 min, can propagate in background)

At your domain provider's DNS page, add an **A record**:

| Type | Name / Host | Value | TTL |
|------|-------------|-------|-----|
| A | `dashboard` | *your Elastic IP* | 300 (or default) |

Check from your Mac (repeat until it prints your Elastic IP; usually < 15 min):

```bash
dig +short dashboard.hopcharge.com
```

## Part 3 — First connection & upload the code (~5 min)

On your Mac, in Terminal:

```bash
# One-time: lock down the key file or ssh will refuse it
chmod 600 ~/Downloads/hopcharge-server.pem

# Connect (say "yes" to the fingerprint prompt)
ssh -i ~/Downloads/hopcharge-server.pem ubuntu@YOUR_ELASTIC_IP
```

On the **server**, prepare the folders, then disconnect:

```bash
sudo mkdir -p /opt/hopcharge/app
sudo chown -R ubuntu:ubuntu /opt/hopcharge
exit
```

Back on your **Mac**, edit the two lines at the top of `deploy/upload_code.sh`
(`SERVER_IP` = your Elastic IP, `KEY_FILE` = path to your .pem), then from the
project folder:

```bash
bash deploy/upload_code.sh
```

This uploads the code (never your secrets or data) — it will say the service
isn't installed yet; that's expected. **This same command is also how you ship
any future code update.**

## Part 4 — Install everything on the server (~10 min)

```bash
ssh -i ~/Downloads/hopcharge-server.pem ubuntu@YOUR_ELASTIC_IP
sudo bash /opt/hopcharge/app/deploy/setup_server.sh dashboard.hopcharge.com
```

The script installs Python, nginx, Tesseract and certbot, builds the app's
virtual environment, installs the auto-start service, and configures nginx for
your domain. Re-running it is always safe.

## Part 5 — Secrets: create the server's neon.env (~5 min)

Still on the server:

```bash
nano /opt/hopcharge/data/neon.env
```

Paste and fill in (copy values from your local `neon.env`):

```env
# ── same as on your Mac ──
DATABASE_URL=postgresql://...your Neon pooled connection string...
EMPLOYEE_FIELD_KEY=...same key as your Mac, or encrypted fields won't decrypt...

# ── the login page (this is the whole point of hosting privately) ──
# Sign-in is Google-only, restricted to emails already in the Employee
# Database (no separate accounts/passwords). See auth.py's module docstring
# for the one-time Google Cloud Console step this needs.
DASHBOARD_AUTH=on
SESSION_DAYS=1

# ── the Google sign-in redirect is built from this ──
DASHBOARD_BASE_URL=https://dashboard.hopcharge.com
```

Save (Ctrl-O, Enter, Ctrl-X), then copy the Google service-account files up
— run this **on your Mac** from the project folder:

```bash
scp -i ~/Downloads/hopcharge-server.pem \
    "Gdrive service acc.json" "Gforms service acc.json" \
    ubuntu@YOUR_ELASTIC_IP:/opt/hopcharge/data/
```

Start the app and confirm it's alive — **on the server**:

```bash
sudo systemctl restart hopcharge
sleep 20   # first boot loads the NLP model
curl http://127.0.0.1:8000/api/health     # → {"status":"ok"}
```

If it doesn't answer: `journalctl -u hopcharge -n 50` shows the actual error.

## Part 6 — HTTPS (~3 min)

Once `dig +short dashboard.hopcharge.com` returns your Elastic IP:

```bash
sudo certbot --nginx -d dashboard.hopcharge.com --redirect
```

Enter an email for renewal notices, agree to the terms. Certbot fetches a free
Let's Encrypt certificate, rewires nginx for HTTPS, redirects all HTTP to
HTTPS, and **renews itself automatically** — this is a one-time step, forever.

## Part 7 — First login & admin access (~3 min)

1. Open **https://dashboard.hopcharge.com** → the HopCharge login page appears.
2. Click **Sign in with Google** and use your Hopcharge Google account. You
   must already exist in the Employee Database (add yourself there first if
   this is a fresh install — the dashboard is open with no login until
   `DASHBOARD_AUTH=on` is set).
3. To make yourself (or a teammate) a dashboard admin, open **Employee
   Database**, edit their record, and check **"Can sign in to this
   dashboard as an admin"**. The first admin can be set by anyone while no
   admin exists yet; after that, only an existing admin can grant it.
4. Anyone whose email isn't in the Employee Database is rejected at sign-in
   with a clear message — add them there to grant access.

---

## ✅ Verification checklist

| Check | How | Expect |
|---|---|---|
| Service healthy | on server: `curl http://127.0.0.1:8000/api/health` | `{"status":"ok"}` |
| HTTPS + login wall | open the domain in a private/incognito window | padlock + login page, no dashboard |
| API is protected | `curl https://dashboard.hopcharge.com/api/accepted` | `{"detail":"Not authenticated"}` |
| Candidate links public | `curl -I https://dashboard.hopcharge.com/status` | `200` |
| Login works | sign in with Google | dashboard loads, all pages work |
| Unregistered email rejected | sign in with a Google account not in the Employee Database | clear rejection message, no access |
| Survives reboot | `sudo reboot`, wait 2 min, reload the site | everything back by itself |

## Routine operations

| Task | Command (Mac unless noted) |
|---|---|
| **Update the code** | `bash deploy/upload_code.sh` (uploads + restarts) |
| Watch live logs | on server: `journalctl -u hopcharge -f` |
| Restart the app | on server: `sudo systemctl restart hopcharge` |
| Manage admin access | Employee Database page → edit an employee → "Can sign in to this dashboard as an admin" |
| Back up the data | `scp -i KEY -r ubuntu@IP:/opt/hopcharge/data ./server-backup/` |
| OS security updates | on server: `sudo apt update && sudo apt upgrade -y` (monthly) |

## Troubleshooting

- **Site unreachable** → is the instance running? Elastic IP still associated?
  `dig +short` returning the right IP? Security group still allows 80/443?
- **502 Bad Gateway** → the app isn't running. On server:
  `sudo systemctl status hopcharge` and `journalctl -u hopcharge -n 50`.
  Most common cause: a typo in `/opt/hopcharge/data/neon.env`.
- **Can't sign in — "not registered in the Employee Database"** → add that
  email (personal or official) to an employee record on the Employee
  Database page, then try again.
- **Google sign-in fails / redirect_uri_mismatch** → make sure
  `/api/auth/google/callback` is added as an Authorized redirect URI on the
  same OAuth client used by "Connect Google Account" (Google Cloud Console
  → Credentials → your Web application client), alongside the existing
  `/api/gmail-oauth/callback` one.
- **Google Drive/Forms/Sheets features fail** → the two service-account JSONs
  must be in `/opt/hopcharge/data/` (Part 5).
- **Upload/parse fails for big batches** → already handled (nginx allows 100 MB
  bodies, 10-minute timeouts) — if you need more, edit
  `/etc/nginx/sites-available/hopcharge` and `sudo systemctl reload nginx`.

## Security notes (already handled for you)

- The app listens only on `127.0.0.1` — the outside world can reach it solely
  through nginx on 80/443.
- Login is rate-limited (8 failures / 10 min per IP); sessions are HttpOnly
  Secure cookies; passwords are salted PBKDF2 hashes; reset links expire and
  are single-use.
- Secrets live in `/opt/hopcharge/data/`, which `upload_code.sh` never writes
  and never deletes.
- Keep `hopcharge-server.pem` private — anyone with it owns the server.
