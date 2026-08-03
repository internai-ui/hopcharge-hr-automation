#!/usr/bin/env bash
# ============================================================================
#  HopCharge HR Dashboard — one-time server setup (run ON the EC2 instance)
#
#  Usage:   sudo bash /opt/hopcharge/app/deploy/setup_server.sh dashboard.hopcharge.com
#
#  Prerequisite: the application code has already been uploaded to
#  /opt/hopcharge/app (use deploy/upload_code.sh from your Mac).
#
#  What it does (safe to re-run — every step is idempotent):
#    1. Installs system packages: Python, nginx, certbot, Tesseract (OCR).
#    2. Creates the Python virtual environment and installs requirements.txt
#       + the spaCy English model.
#    3. Creates /opt/hopcharge/data (HOPCHARGE_HOME) for secrets & data.
#    4. Installs and enables the systemd service (auto-start on reboot).
#    5. Installs the nginx site for your domain.
#  It does NOT create neon.env (your secrets) and does NOT run certbot —
#  those are the two manual steps described in deploy/DEPLOY_GUIDE.md.
# ============================================================================
set -euo pipefail

DOMAIN="${1:-}"
BASE=/opt/hopcharge
APP_DIR=$BASE/app
DATA_DIR=$BASE/data
VENV=$BASE/venv

if [ "$(id -u)" -ne 0 ]; then
    echo "Please run with sudo:  sudo bash $0 your-domain.com" >&2
    exit 1
fi
if [ ! -f "$APP_DIR/app.py" ]; then
    echo "No code found at $APP_DIR — run deploy/upload_code.sh from your Mac first." >&2
    exit 1
fi
if [ -z "$DOMAIN" ]; then
    echo "NOTE: no domain given — skipping the nginx site. Re-run with your domain"
    echo "      (sudo bash $0 dashboard.hopcharge.com) once you know it."
fi

echo "── 1/5 System packages ──────────────────────────────────────────────"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx tesseract-ocr \
                   certbot python3-certbot-nginx rsync

echo "── 2/5 Python environment ───────────────────────────────────────────"
if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip wheel
# --prefer-binary: pull prebuilt wheels so nothing needs a compiler.
"$VENV/bin/pip" install --prefer-binary -r "$APP_DIR/requirements.txt"
"$VENV/bin/python" -m spacy download en_core_web_sm

echo "── 3/5 Data folder (HOPCHARGE_HOME) ─────────────────────────────────"
mkdir -p "$DATA_DIR"
chown -R ubuntu:ubuntu "$BASE"
chmod 750 "$DATA_DIR"

echo "── 4/5 systemd service ──────────────────────────────────────────────"
cp "$APP_DIR/deploy/hopcharge.service" /etc/systemd/system/hopcharge.service
systemctl daemon-reload
systemctl enable hopcharge

echo "── 5/5 nginx site ───────────────────────────────────────────────────"
if [ -n "$DOMAIN" ]; then
    sed "s/YOUR_DOMAIN/$DOMAIN/g" "$APP_DIR/deploy/nginx-hopcharge.conf" \
        > /etc/nginx/sites-available/hopcharge
    ln -sf /etc/nginx/sites-available/hopcharge /etc/nginx/sites-enabled/hopcharge
    rm -f /etc/nginx/sites-enabled/default
    nginx -t
    systemctl reload nginx
fi

echo
echo "═════════════════════════════════════════════════════════════════════"
echo " Setup complete. Two manual steps remain (see deploy/DEPLOY_GUIDE.md):"
echo
echo " 1. Create your secrets file:   nano $DATA_DIR/neon.env"
echo "    (DATABASE_URL, EMPLOYEE_FIELD_KEY, DASHBOARD_AUTH=on, ...)"
echo "    then start the app:         sudo systemctl restart hopcharge"
echo "    and check it:               curl http://127.0.0.1:8000/api/health"
echo
if [ -n "$DOMAIN" ]; then
echo " 2. Once DNS for $DOMAIN points at this server, enable HTTPS:"
echo "    sudo certbot --nginx -d $DOMAIN --redirect"
fi
echo "═════════════════════════════════════════════════════════════════════"
