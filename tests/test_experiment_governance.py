from dashboard.report_exporter import export_report
from dashboard.research_repository import ResearchRepository
from dashboard.research_service import ResearchService


def params(score=75):
    return {"minimum_score": score, "minimum_volume_ratio": 1.0, "stop_loss_atr_multiplier": 1.0, "risk_reward_ratio": 2.0, "ema_pullback_distance": 0.0045}


def policy():
    return ResearchService.OPTIMIZATION_POLICY


def family(repository):
    return repository.create_optimization_family({"name": "test", "instrument": "BTC-USDT", "timeframe": "15m", "start_ts": 1, "development_end_ts": 9, "holdout_start_ts": 10, "holdout_end_ts": 20, "final_oot_start_ts": 21, "final_oot_end_ts": 30})


def run(repository, family_id=None, changed=False):
    request = {"instrument": "BTC-USDT", "timeframe": "15m", "start_date": "2024-01-01", "end_date": "2024-01-02", "trial_budget": 2, "base_parameters": params(80 if changed else 75)}
    return repository.create_optimization_run(request, policy(), 7, 10, family_id, contamination={"post_holdout_adjustment": changed, "base_parameters_changed": changed})


def test_family_fingerprint_is_durable_and_holdout_reveal_is_explicit(tmp_path):
    repository = ResearchRepository(tmp_path / "research.db"); item = family(repository); run_id = run(repository, item["id"])
    hidden = repository.optimization_run(run_id, include_holdout=False)
    assert item["family_fingerprint"] and hidden["holdout_revealed_at"] is None
    repository.reveal_optimization_holdout(run_id)
    assert repository.optimization_run(run_id)["holdout_revealed_at"]
    assert repository.optimization_family(item["id"])["holdout_revealed_at"]


def test_comparison_preserves_missing_failed_trials_and_excludes_holdout(tmp_path):
    repository = ResearchRepository(tmp_path / "research.db"); first, second = run(repository), run(repository)
    trial = repository.create_optimization_trial(first, 1, params(), 7, "engine")
    repository.complete_optimization_trial(trial, "FAILED", elimination_reasons=["trial_error"], error="missing candles")
    service = object.__new__(ResearchService); service.repository = repository
    result = service.optimization_comparison([first, second])
    assert len(result["runs"]) == 2 and result["runs"][0]["trials"][0]["holdout_metrics"] is None
    assert "excluded from ranking" in result["comparison"]["warnings"][0]


def test_family_locked_boundaries_are_used_and_cannot_be_mutated(tmp_path):
    repository = ResearchRepository(tmp_path / "research.db"); item = family(repository)
    class Queue:
        def find_active(self, *_): return None
        def enqueue(self, *_args, **_kwargs): return {"id": 8, "status": "QUEUED", "progress": 0}
    service = object.__new__(ResearchService); service.repository = repository; service.jobs = Queue()
    service.validate_request = lambda _payload: {"instrument": "BTC-USDT", "timeframe": "15m", "start_ts": 1, "end_ts": 20, "start_date": "x", "end_date": "y", "parameters": params()}
    response = service.start_optimization({"experiment_family_id": item["id"], "trial_budget": 2, "seed": 7})
    assert repository.optimization_run(response["id"])["holdout_start_ts"] == 10
    service.validate_request = lambda _payload: {"instrument": "BTC-USDT", "timeframe": "15m", "start_ts": 1, "end_ts": 19, "start_date": "x", "end_date": "y", "parameters": params()}
    try:
        service.start_optimization({"experiment_family_id": item["id"], "trial_budget": 2})
        assert False, "locked range mutation must be rejected"
    except ValueError as error:
        assert "locks" in str(error)


def test_validation_suite_links_exact_family_run_and_trial_and_report_is_sanitized(tmp_path):
    repository = ResearchRepository(tmp_path / "research.db"); item = family(repository); run_id = run(repository, item["id"], changed=True)
    trial_id = repository.create_optimization_trial(run_id, 1, params(), 7, "engine")
    repository.complete_optimization_trial(trial_id, "COMPLETED", validation_metrics={"total_return": 2, "profit_factor": 1.1, "maximum_drawdown": 5, "total_trades": 22, "sharpe_ratio": 1}, score=50, elimination_reasons=[])
    suite_id = repository.create_validation_suite(item["id"], run_id, trial_id, {"parameters": params()})
    repository.add_validation_result(suite_id, stage="cross_asset_transfer", instrument="ETH-USDT", timeframe="15m", start_ts=10, end_ts=20, metrics={"total_return": 1}, buy_hold_metrics={"total_return": 0}, data_quality={}, status="COMPLETED", error=None)
    output = tmp_path / "public.md"; export_report(repository, output, optimization_run=run_id)
    text = output.read_text(encoding="utf-8")
    assert "cross_asset_transfer" in text and "data_cache" not in text and "API_KEY" not in text
    assert "Primary holdout metrics are unavailable" in text


def test_validation_suite_retry_enqueue_failure_is_terminal_and_immutable(tmp_path):
    repository = ResearchRepository(tmp_path / "research.db")
    item = family(repository); run_id = run(repository, item["id"])
    trial_id = repository.create_optimization_trial(run_id, 1, params(), 7, "engine")
    repository.complete_optimization_trial(trial_id, "COMPLETED", validation_metrics={"total_return": 2, "profit_factor": 1.1, "maximum_drawdown": 5, "total_trades": 22, "sharpe_ratio": 1}, score=50, elimination_reasons=[])
    original = repository.create_validation_suite(item["id"], run_id, trial_id, {"parameters": params()})
    repository.update_validation_suite(original, status="FAILED", error="original failure", completed_at="2026-01-01T00:00:00+00:00", job_id=9)
    class Queue:
        def get(self, job_id): return {"id": job_id, "job_type": "VALIDATION_SUITE", "status": "FAILED", "request_payload": {"validation_suite_id": original}}
        def enqueue(self, *_args, **_kwargs): raise OverflowError("queue full")
    service = object.__new__(ResearchService); service.repository = repository; service.jobs = Queue()
    try:
        service.retry_validation_suite_job(9)
        assert False, "enqueue failure must be returned to the caller"
    except OverflowError:
        pass
    attempts = repository.validation_suites()
    retry = next(row for row in attempts if row["retry_of_suite_id"] == original)
    assert retry["status"] == "FAILED" and retry["job_id"] is None and retry["completed_at"]
    assert retry["attempt_number"] == 2 and "queue full" in retry["error"]
    assert repository.validation_suite(original)["status"] == "FAILED"
