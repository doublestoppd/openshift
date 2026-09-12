#!/usr/bin/env bash
# One-command production update for the layout in README section 5:
# pull the latest code, install dependencies, migrate, collect static
# files, run Django's deployment checks, then restart the service.
#
#   sudo /srv/openshift-portal/app/deploy/update.sh
#
# Every step must succeed before the service is restarted, so a failed
# update leaves the previous version running.
set -euo pipefail

APP_DIR=/srv/openshift-portal/app
VENV=/srv/openshift-portal/venv
ENV_FILE=/etc/openshift-portal/env
SERVICE=openshift-portal
APP_USER=shiftportal

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

sudo -u "$APP_USER" -H env APP_DIR="$APP_DIR" VENV="$VENV" ENV_FILE="$ENV_FILE" \
  bash -euo pipefail <<'STEPS'
cd "$APP_DIR"
echo "==> Pulling latest code"
git pull --ff-only
echo "==> Installing dependencies"
"$VENV/bin/pip" install --quiet -r requirements.txt
set -a; . "$ENV_FILE"; set +a
echo "==> Applying database migrations"
"$VENV/bin/python" manage.py migrate --noinput
echo "==> Collecting static files"
"$VENV/bin/python" manage.py collectstatic --noinput
echo "==> Running deployment checks"
"$VENV/bin/python" manage.py check --deploy --fail-level ERROR
STEPS

echo "==> Restarting $SERVICE"
systemctl restart "$SERVICE"
systemctl --no-pager --lines=3 status "$SERVICE"
echo "==> Update complete"
