# KAIROS — EC2 deploy kit

Self-contained deploy for the 24/7 read-only collector. Design + rationale:
`docs/research/2026-06-19-kairos-ec2-deployment.md`. **Recommendation:** `t4g.small`,
us-east-1, AL2023, 30 GB root + 100 GB gp3 data volume, zero-inbound (SSM Session Manager),
secrets in SSM Parameter Store. Read-only everywhere (GET/WS-subscribe; no order methods).

## What runs on the box
| unit | what |
|---|---|
| `kairos-collect.service` | 60s REST poll: Kalshi snapshot + tape + cross-venue funding (OKX/Bitget/Gate/Hyperliquid) |
| `kairos-stream.service` | Kalshi margin WS: tick price/book for KXBTC/ETH/SOL/XRP PERP |
| `kairos-deadman.timer` (5m) | alerts **#kairos-deadman** on stale rows / failed unit / disk >85% |
| `kairos-digest.timer` (2h) | posts **#kairos-digest**: row counts, latest BTC funding, forward-edge progress, disk |
| `kairos-prune.timer` (nightly) | ws_tick→1m rollup (kept forever); disk-pressure ws_book prune (keep max raw that fits); vacuum |

## Prereqs (one-time)
1. **Provision the instance** — see the AWS-CLI block in the research doc §inline (IAM role
   `kairos-ec2` with `ssm:GetParameter /kairos/*` + `kms:Decrypt`, no-inbound SG, IMDSv2 required,
   30 GB root + 100 GB `/dev/sdf` gp3 encrypted, `DeleteOnTermination=false` on the data volume).
2. **Secrets → SSM Parameter Store** (SecureString):
   ```
   /kairos/kalshi/api_key_id        (SecureString)
   /kairos/kalshi/private_key_pem   (SecureString; --value file://.../kalshi_key.pem)
   /kairos/slack/bot_token          (SecureString; your FORTUNA bot xoxb-… token)
   /kairos/slack/channel_deadman    (String = kairos-deadman)
   /kairos/slack/channel_digest     (String = kairos-digest)
   ```
3. **Invite the Slack bot** to `#kairos-deadman` and `#kairos-digest` (chat.postMessage needs membership).
   Bot scope: `chat:write`.
4. **Ship the source** to S3 (the repo is private; this avoids git creds on the box). From the repo root:
   ```bash
   tar czf /tmp/kairos.tgz -C docs --exclude='kairos/.venv' --exclude='*/__pycache__' \
     --exclude='kairos/*.egg-info' --exclude='kairos/.pytest_cache' --exclude='kairos/.ruff_cache' kairos
   aws s3 cp /tmp/kairos.tgz s3://YOUR_BUCKET/kairos.tgz
   ```
   Add `s3:GetObject` on `arn:aws:s3:::YOUR_BUCKET/kairos.tgz` to the `kairos-ec2` role.

## Deploy
```bash
aws ssm start-session --target i-XXXXXXXX        # zero-inbound shell
sudo dnf install -y awscli-2 >/dev/null 2>&1 || true
# pull just the bootstrap to run it (or scp the whole deploy/ dir):
aws s3 cp s3://YOUR_BUCKET/kairos.tgz /tmp/k.tgz && tar xzf /tmp/k.tgz -C /tmp
sudo KAIROS_SRC_S3=s3://YOUR_BUCKET/kairos.tgz bash /tmp/kairos/deploy/bootstrap.sh
```
Bootstrap is idempotent — re-run it to redeploy after pushing a new tarball (it reinstalls the
venv and restarts units). It runs the **venue smoke test** before starting: Kalshi + Hyperliquid
must be 200; if OKX/Bitget/Gate return 403/503 (AWS datacenter-IP block) the core still collects —
re-roll the Elastic IP or run those three on a clean-ASN VPS (see the research doc).

## Verify
```bash
journalctl -u kairos-collect -u kairos-stream -f          # live logs
sudo -u kairos /opt/kairos/venv/bin/kairos db-status      # row counts / coverage
systemctl list-timers 'kairos-*'                          # next deadman/digest/prune fires
```
A #kairos-digest post should arrive within ~10 min of first start; #kairos-deadman stays quiet unless something breaks.

## Backups (irreplaceable data)
Create an AWS Data Lifecycle Manager policy targeting tag `Backup=kairos`, daily snapshot of the
data volume, 14–30 day retention (DLM is free; pay only incremental snapshot storage). **Restore
drill:** snapshot → create volume → attach to a new instance as `/dev/sdf` → `bootstrap.sh` mounts
and resumes. Practice once.

## Knobs (env, overridable in `/etc/kairos/kairos.env` or SSM)
- `KAIROS_STREAM_SYMBOLS` — WS symbols (default the 4 liquid perps).
- `KAIROS_DISK_HIGH` (default 75) — % at which nightly prune starts dropping oldest ws_book.
- `KAIROS_RAW_TICK_DAYS` (default 30) — raw ws_tick retention (already rolled up to ws_tick_1m).
- `KAIROS_DB_PATH` (default /data/kairos.db).

## Cost
~$2.40/mo through 2026 (t4g.small free-trial) → ~$15/mo on-demand after (30 GB) / ~$28/mo at 100 GB.
