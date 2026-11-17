#!/bin/bash
# Prepares the server. Safe to run again, and alongside Moderatornos: it uses its own
# user, key and directory, so it never touches /home/deploy or /opt/moderatornos.
# Usage: setup-server.sh <deploy public key>
set -euo pipefail

deploy_key="${1:?deploy public key}"
here="$(cd "$(dirname "$0")" && pwd)"
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y -qq ca-certificates curl ufw unattended-upgrades restic
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh

# The bot only makes outgoing connections, so it needs no open ports beyond SSH.
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow OpenSSH >/dev/null
ufw --force enable >/dev/null

id dakarbot >/dev/null 2>&1 || useradd --create-home --shell /bin/bash dakarbot
usermod -aG docker dakarbot
install -m 755 "$here/deploy.sh" /usr/local/bin/dakarbot-deploy
install -d -m 700 -o dakarbot -g dakarbot /home/dakarbot/.ssh
# This key can only run the deploy script: no shell, no forwarding.
echo "restrict,command=\"/usr/local/bin/dakarbot-deploy\" $deploy_key" > /home/dakarbot/.ssh/authorized_keys
chown dakarbot:dakarbot /home/dakarbot/.ssh/authorized_keys
chmod 600 /home/dakarbot/.ssh/authorized_keys

install -d -m 750 -o dakarbot -g dakarbot /opt/dakarbot
cd /opt/dakarbot
if [ ! -f .env ]; then
  umask 077
  cat > .env <<ENV
DAKARBOT_TOKEN=
DAKARBOT_OWNER_IDS=
DAKARBOT_DATABASE_PASSWORD=$(openssl rand -hex 32)
ENV
  chown dakarbot:dakarbot .env
fi
if [ ! -f backup.env ]; then
  umask 077
  cat > backup.env <<ENV
RESTIC_REPOSITORY=/var/backups/dakarbot-restic
RESTIC_PASSWORD=$(openssl rand -hex 32)
ENV
fi
chown root:root backup.env
chmod 600 backup.env

install -m 755 "$here/backup.sh" /usr/local/bin/dakarbot-backup
cat > /etc/systemd/system/dakarbot-backup.service <<'UNIT'
[Unit]
Description=DakarBot backup
[Service]
Type=oneshot
ExecStart=/usr/local/bin/dakarbot-backup
UNIT
cat > /etc/systemd/system/dakarbot-backup.timer <<'UNIT'
[Unit]
Description=Nightly DakarBot backup
[Timer]
OnCalendar=*-*-* 03:45:00
RandomizedDelaySec=15m
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now dakarbot-backup.timer >/dev/null

if grep -q '^DAKARBOT_TOKEN=.' .env; then
  echo "setup complete"
else
  echo "setup complete: now fill DAKARBOT_TOKEN and DAKARBOT_OWNER_IDS in /opt/dakarbot/.env"
fi
