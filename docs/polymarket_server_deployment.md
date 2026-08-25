# Polymarket production deployment runbook

## Verified production baseline (2026-08-25)

The canonical production origin is `root@8.217.62.226`; the application root is
`/opt/crypto-bot`. Production is assembled as Docker Compose project
`crypto-bot` from immutable release directories below
`/opt/crypto-bot-canonical-staging/<release>/`, not from the root
`docker-compose.yml`. The public frontend is Nginx on host TCP 443, proxies
`/api/` to internal-only `paper-api:8765`, and serves the SPA. There is no DNS
hostname in Nginx (`server_name _`); the installed certificate is for localhost.
The historical port 8501 deployment is no longer active.

Production data is on local ext4 block storage. Compose named volumes are
bind-backed by directories under `/opt/crypto-bot`; container recreation does
not remove the data. Never run `docker compose down -v`, `git clean`, or remove
anything under `/opt/crypto-bot/data_cache`.

The cloud block device was expanded to 170 GiB on 2026-08-25, then partition 3
and its mounted ext4 filesystem were grown online. The verified result was
167 GiB usable with 48 GiB free (71% used), including about 59 GiB live data
and 30 GiB existing backups. No Polymarket DB, collector, backup job, cron
entry, or systemd timer existed before this deployment. The Polymarket disk
guard must remain enabled because the host still carries large unrelated
research backups.

## Final services and storage

`deploy/compose/polymarket-production.override.yml` adds:

- `paper-api`: read APIs; mounts Polymarket storage but no provider secret.
- `frontend`: unchanged Nginx SPA/proxy; receives no DB or provider secret.
- `polymarket-collector`: one-shot, non-root, read-only container with the key
  mounted as a Docker secret. It is outside PaperService and web requests.
- `polymarket-backup`: one-shot online-backup container without provider key.
- `polymarket-collector.timer`: every three hours, without missed-run catch-up.
- `polymarket-backup.timer`: daily at 02:30 server local time.

Use the bind-backed path `/opt/crypto-bot/polymarket`:

```text
/opt/crypto-bot/polymarket/
  polymarket_research.sqlite
  backups/
  manifests/
  logs/
```

Release configuration contains paths/identity, never the key value:

```text
POLYMARKET_DATA_DIR=/opt/crypto-bot/polymarket
POLYMARKET_LLM_API_KEY_FILE=/opt/crypto-bot-runtime-secrets/polymarket/provider_key
POLYMARKET_GIT_COMMIT=<full release SHA>
POLYMARKET_BUILD_TIMESTAMP=<UTC ISO-8601 timestamp>
```

Create the tree as root, assign it to container UID/GID 10001 and mode 0700.
Create the provider secret root-owned mode 0400. Do not place its value in
`integration.env`, Git, image layers, frontend arguments, or Compose environment.

## Database migration

Preserve the prospective forecasts. Never copy a live SQLite file or its WAL.

1. Locally run `python -m dashboard.polymarket --db <live-db> backup
   --directory <staging-dir>`.
2. Run `backup-verify` and maintenance/integrity checks; compute SHA-256.
3. Transfer to an isolated server staging filename, not the active filename.
4. Verify remote SHA-256 and run remote `backup-verify`/`integrity_check`.
5. Record both digests, size, schema version, release SHA, build timestamp, and
   frozen methodology identities.
6. Atomically rename the verified file to
   `/opt/crypto-bot/polymarket/polymarket_research.sqlite`, set 10001:10001 and
   mode 0600, then deploy the read API.

Do not enable timers until read API/frontend smoke and a manual collection pass.

## Scheduler installation

Install tracked files, then edit both `RELEASE_ID` values in the root-only env:

```bash
install -o root -g root -m 0755 deploy/polymarket/polymarket-job.sh /usr/local/libexec/crypto-bot-polymarket-job
install -o root -g root -m 0644 deploy/polymarket/polymarket-collector.service /etc/systemd/system/
install -o root -g root -m 0644 deploy/polymarket/polymarket-collector.timer /etc/systemd/system/
install -o root -g root -m 0644 deploy/polymarket/polymarket-backup.service /etc/systemd/system/
install -o root -g root -m 0644 deploy/polymarket/polymarket-backup.timer /etc/systemd/system/
install -o root -g root -m 0600 deploy/polymarket/polymarket-runtime.env.example /etc/crypto-bot/polymarket-runtime.env
systemctl daemon-reload
systemctl enable --now polymarket-collector.timer polymarket-backup.timer
systemctl list-timers polymarket-collector.timer polymarket-backup.timer --no-pager
```

Systemd will not overlap an active oneshot and the wrapper adds `flock`; the DB
lease is the final restart-safe guard. Collection has a 90-minute ceiling,
above the measured 20-minute run. Before installation, a deployment test must
prove SIGTERM timeout records the active collection as FAILED/TIMEOUT.

Manual collection uses the same image, secret, volume, limits, and logs:

```bash
systemctl start polymarket-collector.service
systemctl status polymarket-collector.service --no-pager
journalctl -u polymarket-collector.service -n 200 --no-pager
```

Stop future collection with `systemctl disable --now
polymarket-collector.timer`. Only when necessary, stop an active job with
`systemctl stop polymarket-collector.service`; committed immutable rows remain,
the lease must become recoverable, and the ledger must record failure/timeout.

## Backup and restore

The daily job uses SQLite online backup, SHA-256, and `integrity_check`, then
retention. On the actual 170 GiB host it keeps two daily dates plus one backup
from one ISO week (at most three full DBs). At the current 4.55 GB DB size this
caps the Polymarket backup set near 13.7 GB and leaves the collection disk guard
above its required reserve. The job first requires free space of at least the
current DB size plus 1 GiB; keep the timer enabled only while that invariant and
the collection reserve both remain satisfied.

```bash
systemctl start polymarket-backup.service
systemctl status polymarket-backup.service --no-pager
journalctl -u polymarket-backup.service -n 100 --no-pager
```

Restore only to an isolated path. Verify manifest SHA and SQLite integrity,
smoke a temporary API, and obtain operator approval before atomic cutover.
Never overwrite the active file or remove WAL/SHM while a process is connected.

## Health and acceptance

Production checks go through Nginx; host port 8765 is intentionally unpublished:

```bash
curl -kfsS https://127.0.0.1/api/health
curl -kfsS https://127.0.0.1/api/polymarket/health
docker compose --project-name crypto-bot ps
systemctl status polymarket-collector.timer polymarket-backup.timer --no-pager
journalctl -u polymarket-collector.service -n 200 --no-pager
```

Health exposes bounded/sanitized API, collection status/duration/freshness,
lease, DB/WAL/free disk, latest verified backup/age, provider status/recent
success/error, and forecast unresolved/resolved/scored counts. It must not
return secret paths/values, provider headers, prompts, or unbounded evidence.

After manual collection, verify DB -> API -> UI counts plus sample forecast and
market-at-commit probabilities and unresolved state. With zero samples,
Scoreboard metrics are null/unavailable and UI says “Awaiting resolutions”.

## Troubleshooting and secret rotation

- `SKIPPED_LOW_DISK_SPACE`: expand storage or obtain separate archival
  approval; never delete audit rows or crypto production data.
- lock busy: inspect health/systemd. Do not remove a live lock; stale lease
  recovery is performed only by normal collector rules.
- timeout/failure: inspect run ID in journald/ledger. Do not roll back committed
  immutable records; rerun normally after correction.
- backup failure: retain the previous verified backup, fix capacity/permission,
  and rerun. Retention never runs unless the new backup verifies.
- secret rotation: disable collector timer, atomically replace the root-owned
  secret, run a one-shot `llm-check`, manually collect once, then re-enable.
  API/frontend need no restart because they never receive the secret.

Methodology remains frozen: eligibility v2.2, evidence v2, prompt/forecast v2,
DeepSeek V4 Pro non-thinking policy, scoring v2, execution simulation v1.
