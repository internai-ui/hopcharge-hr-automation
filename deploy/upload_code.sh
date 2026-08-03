#!/usr/bin/env bash
# ============================================================================
#  HopCharge HR Dashboard — upload code to the AWS server (run ON YOUR MAC)
#
#  Usage:  bash deploy/upload_code.sh
#
#  Edit the two settings below first. Uploads the application code with rsync
#  and restarts the service. Secrets and data are NEVER uploaded:
#    - neon.env, auth_users.json, auth_secret.key, Google service-account JSONs
#    - output/, input_resumes/, credentials/  (live in /opt/hopcharge/data on
#      the server, which this script never touches)
#  Safe to run again any time you change code — that IS the update procedure.
# ============================================================================
set -euo pipefail

# ── EDIT THESE TWO LINES ────────────────────────────────────────────────────
SERVER_IP="PUT.SERVER.IP.HERE"                     # your EC2 Elastic IP
KEY_FILE="$HOME/Downloads/hopcharge-server.pem"    # path to your .pem key
# ────────────────────────────────────────────────────────────────────────────

if [ "$SERVER_IP" = "PUT.SERVER.IP.HERE" ]; then
    echo "Edit deploy/upload_code.sh first: set SERVER_IP (and KEY_FILE)." >&2
    exit 1
fi
if [ ! -f "$KEY_FILE" ]; then
    echo "Key file not found: $KEY_FILE — fix KEY_FILE in this script." >&2
    exit 1
fi
chmod 600 "$KEY_FILE" 2>/dev/null || true

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE="ubuntu@$SERVER_IP"

echo "Uploading code from: $PROJECT_DIR"
echo "                 to: $REMOTE:/opt/hopcharge/app/"

rsync -avz --delete \
    -e "ssh -i $KEY_FILE" \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.DS_Store' \
    --exclude '.claude/' \
    --exclude '.dist/' \
    --exclude 'neon.env' \
    --exclude 'auth_users.json' \
    --exclude 'auth_secret.key' \
    --exclude '*service acc*.json' \
    --exclude 'output/' \
    --exclude 'input_resumes/' \
    --exclude 'credentials/' \
    --exclude 'launcher*' \
    --exclude '*.log' \
    --exclude '*.pdf' \
    --exclude '*.spec' \
    --exclude 'appicon.ico' \
    --exclude 'Install-HopCharge.bat' \
    --exclude '*.command' \
    --exclude 'README-Windows-Setup.md' \
    "$PROJECT_DIR/" "$REMOTE:/opt/hopcharge/app/"

echo
echo "Restarting the dashboard service..."
# '|| true' so the very first upload (before setup_server.sh ran) doesn't fail.
ssh -i "$KEY_FILE" "$REMOTE" \
    'sudo systemctl restart hopcharge 2>/dev/null && echo "Service restarted." \
     || echo "Service not installed yet — run setup_server.sh on the server."'

echo "Done."
