#!/usr/bin/env bash
# KAIROS EC2 bootstrap — run as root on a fresh Amazon Linux 2023 instance:
#   sudo KAIROS_SRC_S3=s3://YOUR_BUCKET/kairos.tgz bash bootstrap.sh
# Idempotent. Source delivery: set KAIROS_SRC_S3 (tarball of docs/kairos), OR pre-place
# the package at /opt/kairos/src. Secrets are read from SSM Parameter Store (/kairos/*).
set -euo pipefail
REGION="${AWS_REGION:-us-east-1}"
SRC_S3="${KAIROS_SRC_S3:-}"

echo "[1/9] packages"
dnf install -q -y python3.11 python3.11-pip git sqlite >/dev/null

echo "[2/9] kairos user"
id kairos &>/dev/null || useradd --system --create-home --shell /usr/sbin/nologin kairos

echo "[3/9] mount /data (the separate EBS data volume)"
mkdir -p /data
if ! mountpoint -q /data; then
  DEV="${KAIROS_DATA_DEV:-$(lsblk -rno NAME,TYPE,MOUNTPOINT,FSTYPE \
        | awk '$2=="disk" && $3=="" && $4=="" {print "/dev/"$1}' | head -1)}"
  [ -z "$DEV" ] && { echo "FATAL: no unformatted data device; set KAIROS_DATA_DEV"; exit 1; }
  blkid "$DEV" &>/dev/null || mkfs.ext4 -F "$DEV"
  UUID=$(blkid -s UUID -o value "$DEV")
  grep -q "$UUID" /etc/fstab || echo "UUID=$UUID /data ext4 defaults,nofail 0 2" >> /etc/fstab
  mount /data
fi
chown kairos:kairos /data

echo "[4/9] source -> /opt/kairos/src"
mkdir -p /opt/kairos
if [ -n "$SRC_S3" ]; then
  aws s3 cp "$SRC_S3" /tmp/kairos.tgz --region "$REGION"
  rm -rf /opt/kairos/src && mkdir -p /opt/kairos/src
  tar xzf /tmp/kairos.tgz -C /opt/kairos/src
fi
SRCDIR=/opt/kairos/src
[ -f "$SRCDIR/pyproject.toml" ] || SRCDIR=/opt/kairos/src/kairos     # tar of docs/kairos nests one level
[ -f "$SRCDIR/pyproject.toml" ] || { echo "FATAL: kairos source not at /opt/kairos/src"; exit 1; }
ln -sfn "$SRCDIR/deploy" /opt/kairos/deploy
chmod +x /opt/kairos/deploy/*.sh

echo "[5/9] venv + install"
python3.11 -m venv /opt/kairos/venv
/opt/kairos/venv/bin/pip -q install --upgrade pip >/dev/null
/opt/kairos/venv/bin/pip -q install "$SRCDIR" >/dev/null
chown -R kairos:kairos /opt/kairos

echo "[6/9] secrets from SSM -> /etc/kairos"
mkdir -p /etc/kairos
getp() { aws ssm get-parameter --region "$REGION" --name "$1" --with-decryption --query Parameter.Value --output text; }
getp /kairos/kalshi/private_key_pem > /etc/kairos/kalshi.pem
umask 077
cat > /etc/kairos/kairos.env <<EOF
KALSHI_API_KEY_ID=$(getp /kairos/kalshi/api_key_id)
KALSHI_PRIVATE_KEY_PATH=/etc/kairos/kalshi.pem
KAIROS_DB_PATH=/data/kairos.db
KAIROS_SLACK_BOT_TOKEN=$(getp /kairos/slack/bot_token)
KAIROS_SLACK_CHANNEL_DEADMAN=$(getp /kairos/slack/channel_deadman)
KAIROS_SLACK_CHANNEL_DIGEST=$(getp /kairos/slack/channel_digest)
EOF
chmod 600 /etc/kairos/kairos.env /etc/kairos/kalshi.pem
chown -R kairos:kairos /etc/kairos

echo "[7/9] systemd units"
cp /opt/kairos/deploy/systemd/*.service /opt/kairos/deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload

echo "[8/9] venue reachability smoke test"
sudo -u kairos bash /opt/kairos/deploy/smoke-test.sh || true

echo "[9/9] enable + start"
systemctl enable --now kairos-collect.service kairos-stream.service \
  kairos-deadman.timer kairos-digest.timer kairos-prune.timer
sleep 3
systemctl --no-pager --lines=0 status kairos-collect kairos-stream | grep -E "Active:|●" || true
echo "DONE.  logs: journalctl -u kairos-collect -u kairos-stream -f   |   status: kairos db-status"
