from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys

import pytest

from dashboard.global_research_registry import (
    GLOBAL_RESEARCH_REGISTRY_VERSION,
    PARTIAL_METADATA_ONLY,
    SUPPORTED_PHASES,
    UNKNOWN,
    GlobalResearchRegistry,
    canonical_entity_key,
    canonical_trial_key,
    discover_research_artifacts,
)


def metadata(phase: str, run_id: str = "run-1", **updates):
    value = {
        "phase": phase,
        "run_id": run_id,
        "dataset_identity": "dataset-1",
        "snapshot_hash": "a" * 64,
        "source_scope": ["BTC-USDT"],
        "grammar_version": "grammar-v1",
        "schema_version": "schema-v1",
        "evaluation_version": "evaluation-v1",
        "instrument": "BTC-USDT",
        "timeframe": "15m",
        "horizon": "1H",
        "chronological_segment": "DISCOVERY",
        "status": "COMPLETED",
        "complete_trial_ledger": True,
    }
    value.update(updates)
    return value


def trial(
    identity: str, status: str = "EVALUATED", *,
    statistically_evaluated: bool | None = None,
    expression: dict | None = None,
    cluster: str = UNKNOWN,
):
    value = {
        "trial_id": identity,
        "factor_identity": f"source-{identity}",
        "canonical_expression": expression or {
            "op": "zscore", "source": identity},
        "parameters": {"window": 20},
        "dataset_identity": "dataset-1",
        "snapshot_hash": "a" * 64,
        "instrument": "BTC-USDT",
        "timeframe": "15m",
        "horizon": "1H",
        "segment": "DISCOVERY",
        "status": status,
        "correlation_cluster": cluster,
    }
    if statistically_evaluated is not None:
        value["statistically_evaluated"] = statistically_evaluated
    return value


@pytest.mark.parametrize("phase", SUPPORTED_PHASES)
def test_each_phase_import_is_idempotent(tmp_path: Path, phase: str) -> None:
    registry = GlobalResearchRegistry(tmp_path / "registry.db")
    source = tmp_path / f"phase{phase}.json"
    payload = metadata(phase)
    payload["trials"] = [trial("one")]
    source.write_text(json.dumps(payload), encoding="utf-8")
    first = registry.import_json(source)
    second = registry.import_json(source)
    assert first.phase == phase
    assert first.trials_imported == 1
    assert second.idempotent and second.trials_imported == 0
    assert len(registry.runs()) == len(registry.trials()) == 1


def test_same_run_is_not_duplicated_across_report_paths(tmp_path: Path) -> None:
    registry = GlobalResearchRegistry(tmp_path / "registry.db")
    payload = metadata("6A")
    for name, timestamp in (("first.json", "2026-01-01"),
                            ("copy.json", "2026-07-01")):
        value = {**payload, "metadata_timestamp": timestamp,
                 "trials": [trial("same")]}
        path = tmp_path / name
        path.write_text(json.dumps(value), encoding="utf-8")
        registry.import_json(path, "6A")
    assert len(registry.runs()) == 1
    assert len(registry.trials()) == 1


def test_failed_and_budget_truncated_trials_are_preserved(tmp_path: Path) -> None:
    registry = GlobalResearchRegistry(tmp_path / "registry.db")
    registry.import_mapping(
        "6B", metadata("6B"), source_path=tmp_path / "source",
        trials=[
            trial("failed", "FAILED", statistically_evaluated=True),
            trial("truncated", "BUDGET_TRUNCATED",
                  statistically_evaluated=False),
        ])
    rows = registry.trials()
    assert {row["trial_status"] for row in rows} == {
        "FAILED", "BUDGET_TRUNCATED"}
    assert all(row["raw_attempt"] == 1 for row in rows)


def test_duplicate_attempt_rows_are_preserved_with_one_canonical_key(
    tmp_path: Path,
) -> None:
    registry = GlobalResearchRegistry(tmp_path / "registry.db")
    duplicate_expression = {"op": "mean", "source": "funding"}
    registry.import_mapping(
        "6B", metadata("6B"), source_path=tmp_path / "source",
        trials=[
            trial("first", "EVALUATED", expression=duplicate_expression),
            trial("duplicate", "DUPLICATE", statistically_evaluated=False,
                  expression=duplicate_expression),
        ])
    rows = registry.trials()
    assert len(rows) == 2
    assert len({row["canonical_trial_key"] for row in rows}) == 1
    assert registry.accounting(include_phases=["6B"])[
        "views"]["RAW_ATTEMPT_COUNT"] == 2


def test_missing_fields_are_unknown_and_listed(tmp_path: Path) -> None:
    registry = GlobalResearchRegistry(tmp_path / "registry.db")
    registry.import_mapping(
        "6C", {"phase": "6C", "run_id": "sparse"},
        source_path=tmp_path / "sparse", trials=[])
    row = registry.runs()[0]
    assert row["dataset_identity"] == UNKNOWN
    assert "dataset_identity" in json.loads(row["missing_fields"])


def test_partial_counts_do_not_fabricate_trial_rows(tmp_path: Path) -> None:
    registry = GlobalResearchRegistry(tmp_path / "registry.db")
    registry.import_mapping(
        "6D", metadata(
            "6D", raw_trial_count=50, statistical_test_count=12,
            complete_trial_ledger=False),
        source_path=tmp_path / "summary", trials=[])
    run = registry.runs()[0]
    assert run["import_state"] == PARTIAL_METADATA_ONLY
    assert run["raw_trial_count"] == 50
    assert registry.trials() == []
    assert "50 declared attempts" in run["unrecoverable_trials"]


def test_cross_ledger_same_trial_has_same_canonical_key(tmp_path: Path) -> None:
    expression = {"right": {"source": "funding"}, "op": "add",
                  "left": {"source": "basis"}}
    first = canonical_entity_key(
        factor_identity="old-id", expression=expression,
        parameters={"window": 20})
    second = canonical_entity_key(
        factor_identity="new-id",
        expression={"left": {"source": "basis"}, "op": "add",
                    "right": {"source": "funding"}},
        parameters={"window": 20})
    assert first == second
    args = {
        "dataset_identity": "dataset", "snapshot_hash": "hash",
        "parameters": {"window": 20}, "instrument": "BTC-USDT-SWAP",
        "timeframe": "15m", "horizon": "1H",
        "chronological_segment": "DISCOVERY",
    }
    assert canonical_trial_key(canonical_entity=first, **args) == \
        canonical_trial_key(canonical_entity=second, **args)


def test_metadata_timestamp_does_not_affect_identity() -> None:
    base = {"op": "mean", "source": "funding", "created_at": "old"}
    changed = {"op": "mean", "source": "funding", "created_at": "new"}
    assert canonical_entity_key(expression=base) == \
        canonical_entity_key(expression=changed)


def test_raw_attempts_and_statistical_tests_are_separate(tmp_path: Path) -> None:
    registry = GlobalResearchRegistry(tmp_path / "registry.db")
    registry.import_mapping(
        "6E", metadata("6E"), source_path=tmp_path / "ledger",
        trials=[
            trial("ok", "EVALUATED"),
            trial("failed", "FAILED", statistically_evaluated=True),
            trial("invalid", "STRUCTURALLY_INVALID"),
            trial("cutoff", "BUDGET_TRUNCATED"),
        ])
    report = registry.accounting(include_phases=["6E"])
    assert report["views"]["RAW_ATTEMPT_COUNT"] == 4
    assert report["views"]["STATISTICALLY_EVALUATED_COUNT"] == 2


def test_structural_invalidity_is_not_a_statistical_test(tmp_path: Path) -> None:
    registry = GlobalResearchRegistry(tmp_path / "registry.db")
    registry.import_mapping(
        "6F", metadata("6F"), source_path=tmp_path / "ledger",
        trials=[trial("invalid", "STRUCTURALLY_INVALID")])
    row = registry.trials()[0]
    assert row["statistically_evaluated"] == 0


def test_historical_failures_do_not_clear_with_a_new_ledger(
    tmp_path: Path,
) -> None:
    registry = GlobalResearchRegistry(tmp_path / "registry.db")
    run = metadata("6F", run_id="durable-run")
    registry.import_mapping(
        "6F", run, source_path=tmp_path / "ledger-one",
        trials=[trial("old-failure", "FAILED")])
    registry.import_mapping(
        "6F", run, source_path=tmp_path / "ledger-two",
        trials=[trial("new-success", "EVALUATED")])
    assert len(registry.runs()) == 1
    assert {row["trial_status"] for row in registry.trials()} == {
        "FAILED", "EVALUATED"}


def test_later_complete_ledger_resolves_partial_run_without_losing_history(
    tmp_path: Path,
) -> None:
    registry = GlobalResearchRegistry(tmp_path / "registry.db")
    partial = metadata(
        "6F", run_id="backfilled", raw_trial_count=2,
        complete_trial_ledger=False)
    registry.import_mapping(
        "6F", partial, source_path=tmp_path / "summary", trials=[])
    complete = metadata(
        "6F", run_id="backfilled", raw_trial_count=2,
        complete_trial_ledger=True)
    result = registry.import_mapping(
        "6F", complete, source_path=tmp_path / "ledger",
        trials=[trial("failed", "FAILED"), trial("ok", "EVALUATED")])
    assert result.import_state == "COMPLETE_TRIAL_LEDGER"
    assert registry.runs()[0]["import_state"] == "COMPLETE_TRIAL_LEDGER"
    assert len(registry.trials()) == 2


def test_partial_metadata_risk_is_reported(tmp_path: Path) -> None:
    registry = GlobalResearchRegistry(tmp_path / "registry.db")
    registry.import_mapping(
        "6G", metadata(
            "6G", raw_trial_count=2500, complete_trial_ledger=False),
        source_path=tmp_path / "phase6g-summary", trials=[])
    report = registry.accounting()
    assert report["partial_metadata_risks"][0]["phase"] == "6G"
    assert "2500 declared attempts" in report["partial_metadata_risks"][0]["risk"]


def test_dsr_accounting_declares_all_phases(tmp_path: Path) -> None:
    report = GlobalResearchRegistry(tmp_path / "registry.db").accounting()
    assert report["included_phases"] == list(SUPPORTED_PHASES)
    assert [row["phase"] for row in report["phase_accounting"]] == \
        list(SUPPORTED_PHASES)
    assert set(report["views"]) == {
        "RAW_ATTEMPT_COUNT", "STATISTICALLY_EVALUATED_COUNT",
        "EFFECTIVE_CORRELATION_CLUSTER_COUNT"}
    assert report["registry_version"] == GLOBAL_RESEARCH_REGISTRY_VERSION


def test_import_does_not_run_research_code(tmp_path: Path) -> None:
    watched = {
        "dashboard.automatic_discovery",
        "dashboard.automatic_discovery_v2",
        "dashboard.factor_autoresearch",
        "dashboard.factor_statistical_audit",
    }
    before = watched & set(sys.modules)
    registry = GlobalResearchRegistry(tmp_path / "registry.db")
    registry.import_mapping(
        "6A", metadata("6A"), source_path=tmp_path / "report",
        trials=[trial("only-read-existing")])
    assert watched & set(sys.modules) == before


def test_discovery_does_not_access_holdout_or_oot(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    docs = repo / "docs"
    docs.mkdir(parents=True)
    safe = docs / "phase6a_report.json"
    unsafe = docs / "phase6a_holdout_report.json"
    safe.write_text("{}", encoding="utf-8")
    unsafe.write_text("{}", encoding="utf-8")
    assert discover_research_artifacts(repo) == [safe.resolve()]
    with pytest.raises(ValueError, match="holdout/OOT"):
        discover_research_artifacts(repo, [unsafe])


def test_registry_does_not_import_strategy_or_order_apis() -> None:
    source = Path("dashboard/global_research_registry.py").read_text(
        encoding="utf-8")
    assert "automatic_discovery import" not in source
    assert "factor_autoresearch import" not in source
    assert "paper_api import" not in source
    assert "order_api" not in source


def test_phase6f_sqlite_import_keeps_all_terminal_rows(tmp_path: Path) -> None:
    ledger = tmp_path / "phase6f.db"
    with sqlite3.connect(ledger) as connection:
        connection.executescript(
            """CREATE TABLE factor_runs(
                 run_id TEXT,ledger_version TEXT,dataset_identity TEXT,
                 dataset_sha256 TEXT,seed INTEGER,workers INTEGER,stage TEXT,
                 status TEXT,eligibility_snapshot_json TEXT,policy_json TEXT,
                 created_at TEXT,updated_at TEXT,report_json TEXT);
               CREATE TABLE factor_trials(
                 trial_id TEXT,run_id TEXT,sequence INTEGER,
                 factor_identity TEXT,canonical_expression TEXT,
                 expression_ast TEXT,source_versions TEXT,
                 dataset_identity TEXT,instrument TEXT,horizon TEXT,
                 chronological_segment TEXT,parameter_values TEXT,
                 feature_timestamps TEXT,evaluation_version TEXT,
                 trial_family TEXT,parent_expressions TEXT,
                 structural_status TEXT,status TEXT,rejection_reason TEXT,
                 classification TEXT,complexity INTEGER,created_at TEXT,
                 updated_at TEXT);
               CREATE TABLE factor_evaluations(
                 run_id TEXT,trial_id TEXT,segment TEXT,horizon TEXT);""")
        connection.execute(
            "INSERT INTO factor_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("run", "ledger-v1", "dataset-1", "a" * 64, 1, 1, "DONE",
             "COMPLETED", "{}", json.dumps({
                 "grammar_version": "g", "schema_version": "s",
                 "evaluation_version": "e"}), "old", "new", None))
        rows = [
            ("bad", "run", 1, "bad-factor", '{"op":"bad"}', "{}", "{}",
             "dataset-1", "BTC-USDT", "1H", "DISCOVERY", "{}", "{}",
             "e", "generated", "[]", "STRUCTURALLY_INVALID",
             "STRUCTURALLY_INVALID", "bad shape", "STRUCTURALLY_INVALID",
             1, "old", "new"),
            ("cut", "run", 2, "cut-factor", '{"op":"cut"}', "{}", "{}",
             "dataset-1", "BTC-USDT", "1H", "DISCOVERY", "{}", "{}",
             "e", "generated", "[]", "VALID", "BUDGET_TRUNCATED",
             "budget", "BUDGET_TRUNCATED", 1, "old", "new"),
        ]
        connection.executemany(
            "INSERT INTO factor_trials VALUES(" + ",".join("?" * 23) + ")",
            rows)
    registry = GlobalResearchRegistry(tmp_path / "registry.db")
    result = registry.import_sqlite(ledger)
    assert result[0].trials_imported == 2
    assert {row["trial_status"] for row in registry.trials()} == {
        "STRUCTURALLY_INVALID", "BUDGET_TRUNCATED"}
