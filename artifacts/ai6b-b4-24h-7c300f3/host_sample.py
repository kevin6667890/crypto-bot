from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen


def inspect(name: str) -> dict[str, object]:
    value = json.loads(subprocess.check_output(["docker", "inspect", name]))[0]
    return {
        "status": value["State"]["Status"],
        "health": (value["State"].get("Health") or {}).get("Status"),
        "restart": value["RestartCount"],
        "image": value["Image"],
    }


def main() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    sample = {
        "utc": now,
        "paper": inspect("crypto-bot-paper-api-1"),
        "report_worker": inspect("crypto-bot-report-worker-1"),
        "audit_worker": inspect("crypto-bot-audit-worker-1"),
        "collector": inspect("crypto-bot-microstructure-collector-1"),
        "frontend": inspect("crypto-bot-frontend-1"),
        "disk": shutil.disk_usage("/var/lib/docker/volumes/crypto-bot_ai-report-data/_data")._asdict(),
    }
    try:
        sample["public_http"] = urlopen("https://bitcoinbot.uk/", timeout=10).status
    except Exception:
        sample["public_http"] = 0
    db = "/var/lib/docker/volumes/crypto-bot_ai-report-data/_data/ai_market_reports.db"
    connection = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
    sample["ai"] = {
        "requests": connection.execute("SELECT COUNT(*) FROM ai_report_requests").fetchone()[0],
        "attempts": connection.execute("SELECT COUNT(*) FROM ai_report_attempts").fetchone()[0],
        "reports": connection.execute("SELECT COUNT(*) FROM ai_market_reports").fetchone()[0],
        "audits": connection.execute("SELECT COUNT(*) FROM ai_report_audits").fetchone()[0],
        "queued": connection.execute(
            "SELECT COUNT(*) FROM ai_report_requests r WHERE "
            "(SELECT event_type FROM ai_report_request_events e WHERE e.request_id=r.request_id "
            "ORDER BY event_id DESC LIMIT 1) IN ('QUEUED','RUNNING','RETRY_SCHEDULED','INTERRUPTED')"
        ).fetchone()[0],
    }
    paper = "/var/lib/docker/volumes/crypto-bot_paper-data/_data/paper_trades.db"
    sample["paper_trades"] = sqlite3.connect("file:" + paper + "?mode=ro", uri=True).execute(
        "SELECT COUNT(*) FROM paper_trades"
    ).fetchone()[0]
    print(json.dumps(sample, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()

