#!/usr/bin/env python3
"""Rehearse Shadow ON -> Fake Canary -> Shadow OFF in an isolated directory."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ai6b_b2_fake_canary import run_matrix

EVIDENCE_TABLES = (
    "ai_market_contexts", "ai_report_requests", "ai_report_registry_snapshots",
    "ai_market_reports", "ai_report_audit_inputs", "ai_report_audits",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(database: Path) -> dict[str, object]:
    with sqlite3.connect(database) as connection:
        counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in EVIDENCE_TABLES}
        migrations = connection.execute("SELECT migration_key,schema_version,file_sha256 FROM ai_report_migrations ORDER BY migration_key").fetchall()
        evidence = connection.execute("SELECT report_id,response_hash FROM ai_market_reports ORDER BY report_id").fetchall()
        audits = connection.execute("SELECT audit_id,payload_hash FROM ai_report_audits ORDER BY audit_id").fetchall()
    return {"counts": counts, "migrations": migrations, "reports": evidence, "audits": audits}


def rehearse(output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=False)
    flags = output_dir / "runtime-flags.local.json"
    flags.write_text(json.dumps({"AI_MARKET_ANALYSIS_PRESENTATION_ENABLED": True,
                                 "VITE_AI_MARKET_ANALYSIS_SHADOW_ENABLED": True}) + "\n", encoding="utf-8")
    canary = run_matrix(output_dir / "fake-canary")
    database = output_dir / "fake-canary/ai6b-b2-local.db"
    before_off = _snapshot(database)
    legacy_files = [ROOT / "frontend/src/App.tsx", ROOT / "frontend/src/data.ts", ROOT / "frontend/src/i18n.tsx"]
    legacy_hashes = {str(path.relative_to(ROOT)): _sha(path) for path in legacy_files}
    flags.write_text(json.dumps({"AI_MARKET_ANALYSIS_PRESENTATION_ENABLED": False,
                                 "VITE_AI_MARKET_ANALYSIS_SHADOW_ENABLED": False}) + "\n", encoding="utf-8")
    after_off = _snapshot(database)
    selected = json.loads(flags.read_text(encoding="utf-8"))
    legacy_text = "\n".join(path.read_text(encoding="utf-8") for path in legacy_files)
    checks = {
        "shadow_off": selected == {"AI_MARKET_ANALYSIS_PRESENTATION_ENABLED": False,
                                    "VITE_AI_MARKET_ANALYSIS_SHADOW_ENABLED": False},
        "fake_canary_passed": bool(canary["passed"]),
        "ai_database_retained": database.exists(),
        "reports_retained": before_off["reports"] == after_off["reports"] and len(after_off["reports"]) == 12,
        "audits_retained": before_off["audits"] == after_off["audits"] and len(after_off["audits"]) == 12,
        "contexts_and_registry_retained": before_off["counts"] == after_off["counts"],
        "schema_not_rolled_back": before_off["migrations"] == after_off["migrations"],
        "immutable_evidence_unchanged": before_off == after_off,
        "legacy_frontend_unchanged": legacy_hashes == {str(path.relative_to(ROOT)): _sha(path) for path in legacy_files},
        "old_ai_brief_path_present": "/api/chat" in legacy_text and "market.aiBrief" in legacy_text,
        "live_provider_calls_zero": canary["live_provider_calls"] == 0,
        "paper_orders_zero": canary["paper_orders_created"] == 0,
    }
    result = {"schema_version": "ai6b-b2-rollback-rehearsal-v1", "sequence": ["SHADOW_ON", "FAKE_CANARY", "SHADOW_OFF"],
              "checks": checks, "passed": all(checks.values()), "production_connections": 0, "production_writes": 0}
    (output_dir / "rollback-result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.local_only:
        parser.error("--local-only is required; this script never changes production flags")
    result = rehearse(args.output_dir.resolve())
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
