#!/bin/bash
# Nightly, from a systemd timer: the database and .env, encrypted with restic.
set -euo pipefail
cd /opt/dakarbot
set -a; . ./backup.env; set +a

work="$(mktemp -d /var/tmp/dakarbot-backup.XXXXXX)"
trap 'rm -rf "$work"' EXIT
chmod 700 "$work"

docker exec dakarbot-postgres-1 pg_dump -U dakarbot -d dakarbot --format=custom > "$work/database.dump"
restic snapshots >/dev/null 2>&1 || restic init
restic backup --quiet --tag nightly "$work/database.dump" /opt/dakarbot/.env
restic forget --quiet --prune --keep-daily 7 --keep-weekly 4 --keep-monthly 6
echo "backup finished: $(restic snapshots --latest 1 --compact | sed -n 3p)"
