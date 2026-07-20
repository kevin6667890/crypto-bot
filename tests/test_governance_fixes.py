import json

from dashboard.report_exporter import export_report
from dashboard.research_repository import ResearchRepository


def _family(repository):
    return repository.create_optimization_family({"name":"gap", "instrument":"BTC-USDT", "timeframe":"15m", "start_ts":1, "development_end_ts":9, "holdout_start_ts":20, "holdout_end_ts":29, "final_oot_start_ts":30, "final_oot_end_ts":39})


def _run(repository, family):
    run_id = repository.create_optimization_run({"instrument":"BTC-USDT", "timeframe":"15m", "start_date":"1970-01-01", "end_date":"1970-01-01", "trial_budget":1, "base_parameters":{}}, {"version":"v", "weights":{}}, 7, 20, family["id"])
    trial = repository.create_optimization_trial(run_id, 1, {}, 7, "engine")
    repository.complete_optimization_trial(trial, "COMPLETED", validation_metrics={"total_return":1}, holdout_metrics={"total_return":9876.54321})
    repository.update_optimization_run(run_id, status="COMPLETED", result=json.dumps({"development_end_ts":9, "unused_gap_start_ts":10, "unused_gap_end_ts":19}))
    return run_id, trial


def test_public_json_never_leaks_unrevealed_holdout(tmp_path):
    repository = ResearchRepository(tmp_path / "research.db"); family = _family(repository); run_id, _ = _run(repository, family)
    markdown, payload = tmp_path / "report.md", tmp_path / "report.json"
    export_report(repository, markdown, optimization_run=run_id, json_output=payload)
    assert "9876.54321" not in markdown.read_text()
    assert "9876.54321" not in payload.read_text()
    assert "holdout_metrics" not in payload.read_text()
    repository.reveal_optimization_holdout(run_id)
    export_report(repository, markdown, optimization_run=run_id, json_output=payload)
    assert "9876.54321" in payload.read_text()


def test_validation_retry_creates_immutable_lineage(tmp_path):
    repository = ResearchRepository(tmp_path / "research.db"); family = _family(repository); run_id, trial = _run(repository, family)
    suite_id = repository.create_validation_suite(family["id"], run_id, trial, {"parameters": {}})
    repository.add_validation_result(suite_id, stage="primary_holdout", instrument="BTC-USDT", timeframe="15m", start_ts=20, end_ts=29, metrics={"total_return":1}, buy_hold_metrics={}, data_quality={}, status="FAILED", error="fixture")
    repository.update_validation_suite(suite_id, status="FAILED", error="fixture")
    retry_id = repository.create_validation_suite_retry(suite_id)
    old, new = repository.validation_suite(suite_id), repository.validation_suite(retry_id)
    assert retry_id != suite_id and new["retry_of_suite_id"] == suite_id and new["attempt_number"] == 2
    assert old["status"] == "FAILED" and len(old["results"]) == 1
    assert new["status"] == "QUEUED" and new["results"] == []
