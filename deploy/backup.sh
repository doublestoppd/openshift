#!/usr/bin/env bash
# Nightly SQLite backup for the Open Shift Response Portal.
#
# Uses sqlite3's online .backup command, which is safe while the
# application is running (unlike copying the file directly).
#
# Install (as the shiftportal user):
#   crontab -e
#   15 2 * * * /srv/openshift-portal/app/deploy/backup.sh
set -euo pipefail

DB_PATH="${DATABASE_PATH:-/srv/openshift-portal/data/db.sqlite3}"
BACKUP_DIR="${BACKUP_DIR:-/srv/openshift-portal/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"

mkdir -p "$BACKUP_DIR"
stamp="$(date +%Y%m%d-%H%M%S)"
target="$BACKUP_DIR/db-$stamp.sqlite3"

sqlite3 "$DB_PATH" ".backup '$target'"
gzip "$target"

# Prune old backups.
find "$BACKUP_DIR" -name 'db-*.sqlite3.gz' -mtime "+$KEEP_DAYS" -delete

echo "Backup written: $target.gz"
