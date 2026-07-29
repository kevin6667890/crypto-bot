# Microstructure research readiness and future terminals

Version `microstructure-research-readiness-v1` evaluates each feature group and
instrument independently. It opens only an explicitly supplied SQLite file in
read-only/query-only mode. It does not default to, discover, or connect to a
production database.

The versioned policy requires 30 gap-adjusted days, a 30-day continuous usable
interval, 95% coverage in the trailing 30 days, no unresolved
`CRITICAL_LIVE_GAP`, source freshness within its native update schedule, 2,000
native CVD buckets or 1,000 native OI changes, and at least 30 deterministic
one-hour non-overlapping labels with 80% label overlap. Interaction groups use
the smaller participating native-event set. Natural span is diagnostic only.

Even a fully qualified result is only
`RESEARCH_READY_PENDING_HUMAN_APPROVAL`. The gate never creates a research job,
generates a factor, changes configuration, starts AutoResearch, or sends a
signal.

## Daily CLI

```text
python scripts/check_microstructure_research_readiness.py \
  --database C:/offline/market_microstructure.db \
  --output-json artifacts/readiness.json \
  --previous-result artifacts/readiness.previous.json \
  --human-readable --strict
```

`--instrument` and `--feature-group` are repeatable filters. Exit codes are:
`0` all selected rows pending approval, `10` collecting, `11` approaching,
`20` data/gap/staleness blocked, and `2` execution/schema/input error. A stable
local `*.notification.json` is created beside `--output-json` only when a
previous/current status differs. It is never sent.

## Scheduling examples (disabled)

Cron example:

```text
# enabled=false
# 17 1 * * * cd /opt/crypto-bot && python scripts/check_microstructure_research_readiness.py --database /srv/offline/microstructure.db --output-json /srv/artifacts/readiness.json
```

Systemd example:

```ini
# enabled=false
[Unit]
Description=Read-only microstructure research readiness check

[Service]
Type=oneshot
WorkingDirectory=/opt/crypto-bot
ExecStart=/usr/bin/python scripts/check_microstructure_research_readiness.py --database /srv/offline/microstructure.db --output-json /srv/artifacts/readiness.json

# No [Install] section: this example cannot be enabled as supplied.
```

Future terminals are declared by
`microstructure-factor-terminals-v1` and all have `enabled=false`. A future
manifest entry requires a matching pending-approval result, an explicit
`human_approved=true` record with approver/time, and the exact same dataset
identity. The declarations perform no factor calculation.
