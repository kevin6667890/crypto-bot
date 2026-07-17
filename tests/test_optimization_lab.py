from dashboard.research_repository import ResearchRepository
from dashboard.research_service import ResearchService
from dashboard.job_queue import JobQueue


def parameters(score=75, volume=1.0, stop=1.0, reward=2.0, distance=0.0045):
    return {"minimum_score": score, "minimum_volume_ratio": volume, "stop_loss_atr_multiplier": stop, "risk_reward_ratio": reward, "ema_pullback_distance": distance}


def metrics(return_=5, pf=1.2, drawdown=10, trades=30, sharpe=1):
    return {"total_return": return_, "profit_factor": pf, "maximum_drawdown": drawdown, "total_trades": trades, "sharpe_ratio": sharpe}


def create_run(repository):
    return repository.create_optimization_run({"instrument": "BTC-USDT", "timeframe": "15m", "trial_budget": 12}, ResearchService.OPTIMIZATION_POLICY, 42, 1_700_000_000)


def test_holdout_metrics_never_participate_in_optimization_score():
    service = object.__new__(ResearchService)
    score, components, reasons = service._optimization_score(metrics(), 2.0)
    assert score == round(sum(components.values()), 4)
    assert "holdout" not in " ".join(components)
    assert not reasons


def test_holdout_is_limited_to_development_ranked_top_ten_completed_trials():
    trials = [{"id": index, "status": "COMPLETED" if index != 3 else "ELIMINATED"} for index in range(1, 15)]
    finalists = ResearchService._select_holdout_finalists(trials)
    assert [trial["id"] for trial in finalists] == [1, 2, 4, 5, 6, 7, 8, 9, 10, 11]
    assert all(trial["status"] == "COMPLETED" for trial in finalists)


def test_low_trade_and_high_drawdown_trials_are_preserved_as_eliminated(tmp_path):
    repository = ResearchRepository(tmp_path / "research.db")
    run_id = create_run(repository)
    trial_id = repository.create_optimization_trial(run_id, 1, parameters(), 42, "test-engine")
    service = object.__new__(ResearchService)
    score, components, reasons = service._optimization_score(metrics(trades=2, drawdown=40), 0)
    repository.complete_optimization_trial(trial_id, "ELIMINATED", validation_metrics=metrics(trades=2, drawdown=40), score=score, score_components=components, elimination_reasons=reasons)
    trial = repository.optimization_run(run_id)["trials"][0]
    assert trial["status"] == "ELIMINATED"
    assert set(trial["elimination_reasons"]) == {"minimum_validation_trades", "maximum_drawdown"}


def test_fixed_seed_sampling_is_reproducible():
    service = object.__new__(ResearchService)
    first, second = __import__("random").Random(123), __import__("random").Random(123)
    left = [service._optimization_parameters(parameters(), first, index) for index in range(1, 8)]
    right = [service._optimization_parameters(parameters(), second, index) for index in range(1, 8)]
    assert left == right


def test_neighborhood_stability_uses_parameter_distance_not_score_distance():
    near = {"id": 2, "parameters": parameters(score=76), "validation_metrics": metrics(return_=4, pf=1.1, drawdown=12), "score": 99}
    far = {"id": 3, "parameters": parameters(score=90, volume=1.6, stop=1.8, reward=3.0, distance=0.010), "validation_metrics": metrics(return_=-4, pf=0.5, drawdown=50), "score": 50}
    target = {"id": 1, "parameters": parameters(score=75), "validation_metrics": metrics(), "score": 1}
    stability, count = ResearchService._neighborhood_stability(target, [target, near, far])
    assert ResearchService._parameter_distance(target["parameters"], near["parameters"]) <= 0.25
    assert ResearchService._parameter_distance(target["parameters"], far["parameters"]) > 0.25
    assert (stability, count) == (5.0, 1)


def test_terminal_states_and_restart_interrupt_running_trials_without_deleting_history(tmp_path):
    path = tmp_path / "research.db"; repository = ResearchRepository(path)
    cancelled = create_run(repository); cancelled_trial = repository.create_optimization_trial(cancelled, 1, parameters(), 42, "test")
    service = object.__new__(ResearchService); service.repository = repository
    service._optimization_job_terminal({"status": "CANCELLED", "request_payload": {"optimization_run_id": cancelled}, "error": "requested", "completed_at": "2026-01-01T00:00:00+00:00"})
    failed = create_run(repository); failed_trial = repository.create_optimization_trial(failed, 1, parameters(), 42, "test")
    service._optimization_job_terminal({"status": "FAILED", "request_payload": {"optimization_run_id": failed}, "error": "boom", "completed_at": "2026-01-01T00:00:00+00:00"})
    interrupted = create_run(repository); repository.update_optimization_run(interrupted, status="RUNNING"); interrupted_trial = repository.create_optimization_trial(interrupted, 1, parameters(), 42, "test")
    restarted = ResearchRepository(path)
    states = {item["id"]: item for item in restarted.optimization_history()}
    assert states[cancelled]["status"] == "CANCELLED" and states[failed]["status"] == "FAILED" and states[interrupted]["status"] == "INTERRUPTED"
    details = restarted.optimization_run(interrupted)
    assert details["trials"][0]["status"] == "INTERRUPTED"
    assert restarted.optimization_run(cancelled)["trials"][0]["id"] == cancelled_trial
    assert restarted.optimization_run(failed)["trials"][0]["id"] == failed_trial
    assert details["trials"][0]["id"] == interrupted_trial


def test_queued_job_cancellation_projects_to_optimization_run(tmp_path):
    path = tmp_path / "research.db"; repository = ResearchRepository(path); run_id = create_run(repository)
    queue = JobQueue(path, autostart=False)
    queue.register_terminal_handler("OPTIMIZATION", lambda job: repository.mark_optimization_run_terminal(int(job["request_payload"]["optimization_run_id"]), job["status"], job.get("error"), job.get("completed_at")))
    job = queue.enqueue("OPTIMIZATION", {"optimization_run_id": run_id})
    repository.update_optimization_run(run_id, job_id=job["id"])
    queue.cancel(job["id"])
    assert repository.optimization_run(run_id)["status"] == "CANCELLED"


def test_repository_preserves_completed_eliminated_and_failed_trials(tmp_path):
    repository = ResearchRepository(tmp_path / "research.db"); run_id = create_run(repository)
    for index, status in enumerate(("COMPLETED", "ELIMINATED", "FAILED"), 1):
        trial_id = repository.create_optimization_trial(run_id, index, parameters(score=70 + index), 42, "test")
        repository.complete_optimization_trial(trial_id, status, validation_metrics=metrics(), elimination_reasons=[] if status == "COMPLETED" else ["evidence"])
    assert [trial["status"] for trial in repository.optimization_run(run_id)["trials"]] == ["COMPLETED", "ELIMINATED", "FAILED"]


def optimization_request():
    return {"instrument": "BTC-USDT", "timeframe": "15m", "start_date": "2024-01-01", "end_date": "2024-12-31", "parameters": {}, "trial_budget": 2, "seed": 7}


def service_for_enqueue(repository, jobs):
    service = object.__new__(ResearchService); service.repository = repository; service.jobs = jobs
    service.validate_request = lambda _: {"instrument": "BTC-USDT", "timeframe": "15m", "start_date": "2024-01-01", "end_date": "2024-12-31", "start_ts": 1_704_067_200, "end_ts": 1_735_689_599, "parameters": {}}
    return service


def test_enqueue_deduplication_returns_existing_run_without_binding_new_run(tmp_path):
    path = tmp_path / "research.db"; repository = ResearchRepository(path); queue = JobQueue(path, autostart=False)
    old_run = create_run(repository); old_job = queue.enqueue("OPTIMIZATION", {"optimization_run_id": old_run})
    repository.update_optimization_run(old_run, job_id=old_job["id"])

    class RaceQueue:
        def find_active(self, *_): return None
        def enqueue(self, *_args, **_kwargs): return {**old_job, "deduplicated": True}

    result = service_for_enqueue(repository, RaceQueue()).start_optimization(optimization_request())
    history = repository.optimization_history(); new_run = next(item for item in history if item["id"] != old_run)
    assert result == {"id": old_run, "job_id": old_job["id"], "status": old_job["status"], "progress": old_job["progress"], "deduplicated": True}
    assert new_run["status"] == "CANCELLED" and new_run["job_id"] is None
    assert new_run["error"] == "Identical active optimization request already exists"
    assert repository.optimization_run(new_run["id"])["trials"] == []
    assert repository.optimization_run(old_run)["job_id"] == old_job["id"]
    assert queue.get(old_job["id"])["request_payload"]["optimization_run_id"] == old_run


def test_queue_full_marks_new_run_failed_without_creating_job(tmp_path):
    repository = ResearchRepository(tmp_path / "research.db")

    class FullQueue:
        def find_active(self, *_): return None
        def enqueue(self, *_args, **_kwargs): raise OverflowError("Research job queue is full")

    service = service_for_enqueue(repository, FullQueue())
    try:
        service.start_optimization(optimization_request())
        assert False, "OverflowError must reach the caller"
    except OverflowError as error:
        assert str(error) == "Research job queue is full"
    run = repository.optimization_history()[0]
    assert run["status"] == "FAILED" and run["job_id"] is None
    assert run["error"] == "Research job queue is full" and run["completed_at"]
    assert repository.optimization_run(run["id"])["trials"] == []


def test_normal_enqueue_binds_exact_new_run_and_job(tmp_path):
    path = tmp_path / "research.db"; repository = ResearchRepository(path); queue = JobQueue(path, autostart=False)
    result = service_for_enqueue(repository, queue).start_optimization(optimization_request())
    run = repository.optimization_run(result["id"]); job = queue.get(result["job_id"])
    assert result["deduplicated"] is False
    assert run["job_id"] == job["id"]
    assert job["request_payload"]["optimization_run_id"] == run["id"]
