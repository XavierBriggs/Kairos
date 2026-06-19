#!/usr/bin/env bash
# KAIROS EC2 bootstrap — run as root on a fresh Amazon Linux 2023 instance:
#   sudo KAIROS_SRC_S3=s3://YOUR_BUCKET/kairos.tgz bash bootstrap.sh
# Idempotent. Source delivery: set KAIROS_SRC_S3 (tarball of docs/kairos), OR pre-place
# the package at /opt/kairos/src. Secrets are read from SSM Parameter Store (/kairos/*).
set -euo pipefail
REGION="${AWS_REGION:-us-east-1}"
SRC_DIR="${KAIROS_SRC_DIR:-}"      # already-present source dir (e.g. a git clone) — simplest
SRC_GIT="${KAIROS_SRC_GIT:-}"      # git URL to clone (private repo: PAT in URL or a deploy key)
SRC_SUBDIR="${KAIROS_SRC_SUBDIR:-docs/kairos}"   # kairos package path within the repo
SRC_S3="${KAIROS_SRC_S3:-}"        # s3://bucket/kairos.tgz (alternative to git)

echo "[1/9] packages"
dnf install -q -y python3.11 python3.11-pip git sqlite >/dev/null

echo "[2/9] kairos user"
id kairos &>/dev/null || useradd --system --create-home --shell /usr/sbin/nologin kairos

echo "[3/9] mount /data (the separate EBS data volume)"
mkdir -p /data
if ! mountpoint -q /data; then
  if [ -n "${KAIROS_DATA_DEV:-}" ]; then
    DEV="$KAIROS_DATA_DEV"
  else
    # data disk = a whole disk that is NOT the disk holding root (avoids grabbing nvme0n1)
    ROOTDISK=$(lsblk -no PKNAME "$(findmnt -no SOURCE /)" 2>/dev/null | head -1)
    NAME=$(lsblk -drno NAME,TYPE | awk '$2=="disk"{print $1}' | grep -v "^${ROOTDISK:-__none__}$" | head -1)
    DEV="${NAME:+/dev/$NAME}"
  fi
  [ -z "$DEV" ] && { echo "FATAL: no data device. Attach an EBS volume or set KAIROS_DATA_DEV=/dev/nvmeXn1 (run: lsblk)"; exit 1; }
  echo "      data device: $DEV"
  blkid "$DEV" &>/dev/null || mkfs.ext4 -F "$DEV"
  UUID=$(blkid -s UUID -o value "$DEV")
  [ -z "$UUID" ] && { echo "FATAL: could not read a filesystem UUID for $DEV"; exit 1; }
  grep -q "$UUID" /etc/fstab || echo "UUID=$UUID /data ext4 defaults,nofail 0 2" >> /etc/fstab
  mount /data
fi
chown kairos:kairos /data

echo "[4/9] resolve source"
mkdir -p /opt/kairos
if [ -n "$SRC_GIT" ]; then                       # git clone (preferred if you made a repo)
  rm -rf /opt/kairos/repo
  git clone --depth 1 "$SRC_GIT" /opt/kairos/repo
  SRC_DIR="/opt/kairos/repo/$SRC_SUBDIR"
elif [ -n "$SRC_S3" ]; then                      # s3 tarball
  aws s3 cp "$SRC_S3" /tmp/kairos.tgz --region "$REGION"
  rm -rf /opt/kairos/src && mkdir -p /opt/kairos/src
  tar xzf /tmp/kairos.tgz -C /opt/kairos/src
  SRC_DIR=/opt/kairos/src
  [ -f "$SRC_DIR/pyproject.toml" ] || SRC_DIR=/opt/kairos/src/kairos
fi
[ -z "$SRC_DIR" ] && SRC_DIR=/opt/kairos/src     # else expect a pre-placed source dir
[ -f "$SRC_DIR/pyproject.toml" ] || { echo "FATAL: no kairos source. Set KAIROS_SRC_DIR (a clone), KAIROS_SRC_GIT, or KAIROS_SRC_S3. Looked in $SRC_DIR"; exit 1; }
echo "      source: $SRC_DIR"
ln -sfn "$SRC_DIR/deploy" /opt/kairos/deploy
chmod +x /opt/kairos/deploy/*.sh

echo "[5/9] venv + install"
python3.11 -m venv /opt/kairos/venv
/opt/kairos/venv/bin/pip -q install --upgrade pip >/dev/null
/opt/kairos/venv/bin/pip -q install "$SRC_DIR" >/dev/null
chown -R kairos:kairos /opt/kairos

echo "[6/9] secrets -> /etc/kairos/kairos.env"
mkdir -p /etc/kairos
if [ -f /etc/kairos/kairos.env ]; then
  echo "      using existing /etc/kairos/kairos.env (keys already on box; skipping SSM)"
elif aws ssm get-parameter --region "$REGION" --name /kairos/kalshi/api_key_id >/dev/null 2>&1; then
  echo "      fetching from SSM Parameter Store"
  getp() { aws ssm get-parameter --region "$REGION" --name "$1" --with-decryption --query Parameter.Value --output text; }
  umask 077
  getp /kairos/kalshi/private_key_pem > /etc/kairos/kalshi.pem
  cat > /etc/kairos/kairos.env <<EOF
KALSHI_API_KEY_ID=$(getp /kairos/kalshi/api_key_id)
KALSHI_PRIVATE_KEY_PATH=/etc/kairos/kalshi.pem
KAIROS_DB_PATH=/data/kairos.db
KAIROS_SLACK_BOT_TOKEN=$(getp /kairos/slack/bot_token)
KAIROS_SLACK_CHANNEL_DEADMAN=$(getp /kairos/slack/channel_deadman)
KAIROS_SLACK_CHANNEL_DIGEST=$(getp /kairos/slack/channel_digest)
EOF
else
  umask 077
  cat > /etc/kairos/kairos.env <<'EOF'
# FILL IN then re-run bootstrap. Put your Kalshi PEM on the box at KALSHI_PRIVATE_KEY_PATH.
KALSHI_API_KEY_ID=
KALSHI_PRIVATE_KEY_PATH=/etc/kairos/kalshi.pem
KAIROS_DB_PATH=/data/kairos.db
KAIROS_SLACK_BOT_TOKEN=
KAIROS_SLACK_CHANNEL_DEADMAN=kairos-deadman
KAIROS_SLACK_CHANNEL_DIGEST=kairos-digest
EOF
  chmod 600 /etc/kairos/kairos.env
  echo "FATAL: no secrets found. Wrote a template to /etc/kairos/kairos.env — fill it"
  echo "       (and put the PEM at KALSHI_PRIVATE_KEY_PATH), then re-run this script."
  exit 1
fi
chmod 600 /etc/kairos/kairos.env
[ -f /etc/kairos/kalshi.pem ] && chmod 600 /etc/kairos/kalshi.pem
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
