from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from dashboard.automatic_research import AutomaticResearchService
from dashboard.discovery_service import DiscoveryService
from dashboard.job_queue import JobQueue
from dashboard.research_repository import ResearchRepository
<<<<<<< HEAD
from dashboard.research_service import ResearchService
from scripts.run_auto_research_scheduler import DurableAutoResearchScheduler, run_forever


class StopAfterWaits:
    def __init__(self, waits: int): self.waits, self.calls = waits, 0
    def is_set(self): return self.calls >= self.waits
    def wait(self, _seconds): self.calls += 1; return self.is_set()


class ResidentScheduler:
    def __init__(self): self.initialized = []; self.ticks = 0
    def initialize(self, enabled): self.initialized.append(enabled); return {"enabled": enabled}
    def tick(self): self.ticks += 1; return {"triggered": False, "state": {"enabled": False}}
    def sleep_seconds(self, _state): return 60.0
=======
from scripts.run_auto_research_scheduler import DurableAutoResearchScheduler
>>>>>>> feat/auto-research-closed-loop


class Clock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def scheduler_service(tmp_path, now: datetime, interval: int = 168):
    repository = ResearchRepository(tmp_path / "research.db")
    jobs = JobQueue(tmp_path / "research.db", autostart=False)
    discovery = DiscoveryService(repository, jobs)
    automatic = AutomaticResearchService(repository, jobs, discovery)
    clock = Clock(now)
    scheduler = DurableAutoResearchScheduler(
        SimpleNamespace(automatic_research=automatic), interval, clock=clock
    )
    return repository, jobs, automatic, clock, scheduler


def test_restart_preserves_next_due(tmp_path):
    now = datetime(2026, 8, 19, 7, 20, tzinfo=timezone.utc)
    _, _, automatic, clock, scheduler = scheduler_service(tmp_path, now)
    first = scheduler.initialize(True)
    clock.value = now + timedelta(hours=3)
    restarted = DurableAutoResearchScheduler(
        SimpleNamespace(automatic_research=automatic), 168, clock=clock
    ).initialize(True)
    assert restarted["next_due_at"] == first["next_due_at"]
    assert restarted["next_due_at"] == "2026-08-26T07:20:00+00:00"


def test_expired_due_triggers_once_and_advances_from_previous_due(tmp_path):
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    repository, _, _, clock, scheduler = scheduler_service(tmp_path, now, interval=24)
    initial = scheduler.initialize(True)
    clock.value = now + timedelta(hours=25)
    result = scheduler.tick()
    assert result["triggered"] is True
    assert result["state"]["last_started_cycle_id"] == result["cycle"]["id"]
    assert result["state"]["last_scheduled_at"] == "2026-08-20T01:00:00+00:00"
    assert result["state"]["next_due_at"] == "2026-08-21T00:00:00+00:00"
    assert result["state"]["next_due_at"] != (
        clock.value + timedelta(hours=24)
    ).isoformat()
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM automatic_research_cycles").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM research_jobs").fetchone()[0] == 1
    assert initial["next_due_at"] == "2026-08-20T00:00:00+00:00"
    assert scheduler.tick()["triggered"] is False


def test_dedupe_prevents_duplicate_cycle_when_due_is_replayed(tmp_path):
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    repository, _, automatic, clock, scheduler = scheduler_service(tmp_path, now, interval=24)
    state = scheduler.initialize(True)
    clock.value = now + timedelta(hours=25)
    first = scheduler.tick()
    with repository.connect() as connection:
        connection.execute(
            "UPDATE automatic_research_scheduler_state SET next_due_at=? WHERE scheduler_name=?",
            (state["next_due_at"], state["scheduler_name"]),
        )
    replay = scheduler.tick()
    assert replay["triggered"] is True
    assert replay["cycle"]["id"] == first["cycle"]["id"]
    assert replay["cycle"]["deduplicated"] is True
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM automatic_research_cycles").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM research_jobs").fetchone()[0] == 1
    assert automatic.scheduler_state()["next_due_at"] == "2026-08-21T00:00:00+00:00"


def test_disabled_scheduler_does_not_run(tmp_path):
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    repository, _, _, clock, scheduler = scheduler_service(tmp_path, now, interval=24)
    scheduler.initialize(False)
    clock.value = now + timedelta(days=2)
    assert scheduler.tick()["triggered"] is False
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM automatic_research_cycles").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM research_jobs").fetchone()[0] == 0


<<<<<<< HEAD
def test_idle_worker_loop_stays_resident_until_stop_signal():
    scheduler, stop = ResidentScheduler(), StopAfterWaits(3)
    assert run_forever(scheduler, False, stop) == 0
    assert scheduler.initialized == [False]
    assert scheduler.ticks == 3


def test_active_worker_loop_stays_resident_until_stop_signal():
    scheduler, stop = ResidentScheduler(), StopAfterWaits(2)
    assert run_forever(scheduler, True, stop) == 0
    assert scheduler.initialized == [True]
    assert scheduler.ticks == 2


def test_passive_api_service_never_interrupts_a_worker_owned_running_job(tmp_path, monkeypatch):
    database = tmp_path / "research.db"
    owner = JobQueue(database, autostart=False)
    job = owner.enqueue("BACKTEST", {"fixture": True}, "fixture")
    with owner.connect() as connection:
        connection.execute("UPDATE research_jobs SET status='RUNNING' WHERE id=?", (job["id"],))
    monkeypatch.delenv("RESEARCH_JOB_WORKER_ENABLED", raising=False)
    passive = ResearchService(database)
    assert passive.jobs.autostart is False
    assert passive.jobs.get(job["id"])["status"] == "RUNNING"


=======
>>>>>>> feat/auto-research-closed-loop
def test_interval_and_summary_are_persisted(tmp_path):
    now = datetime(2026, 8, 19, 7, 20, tzinfo=timezone.utc)
    _, _, automatic, _, scheduler = scheduler_service(tmp_path, now, interval=168)
    state = scheduler.initialize(True)
    summary = automatic.summary()
    assert state["interval_hours"] == 168
    assert summary["scheduler_enabled"] is True
    assert summary["interval_hours"] == 168
    assert summary["next_due_at"] == "2026-08-26T07:20:00+00:00"
