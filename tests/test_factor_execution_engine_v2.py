from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from dashboard.factor_execution_engine_v2 import (
    ExecutionInterrupted,
    ExecutionLedger,
    FactorExecutionEngineV2,
    FrozenExecutionManifest,
    IdentityMismatch,
    MemoryBudgetExceeded,
    TaskState,
)


def _manifest(
    count: int = 6,
    *,
    run_id: str = "fixture-run",
    dataset: str = "fixture-dataset-v1",
    crash_attempts: int = 0,
    hard_crash_attempts: int = 0,
) -> FrozenExecutionManifest:
    return FrozenExecutionManifest.from_dict(
        {
            "run_id": run_id,
            "dataset_identity": dataset,
            "evaluation_version": "factor-evaluation-v1",
            "evaluation_policy": {
                "name": "frozen-fixture-policy",
                "holdout": False,
                "oot": False,
            },
            "chronological_segments": ["DISCOVERY", "SELECTION"],
            "bootstrap_seed": 7,
            "tasks": [
                {
                    "trial_id": f"trial-{index // 2}",
                    "factor_identity": f"factor-{index // 2}",
                    "instrument": "BTC-USDT-SWAP",
                    "segment": "DISCOVERY" if index % 2 == 0 else "SELECTION",
                    "horizon": "1H",
                    "family": f"family-{index % 2}",
                    "payload": {
                        "samples": [
                            index * 0.01 + offset * 0.001
                            for offset in range(8)
                        ],
                        "crash_attempts": crash_attempts,
                        "hard_crash_attempts": hard_crash_attempts,
                    },
                }
                for index in range(count)
            ],
        }
    )


def _rows(path: Path, sql: str) -> list[tuple]:
    with sqlite3.connect(path) as connection:
        return connection.execute(sql).fetchall()


def test_chunk_completion_is_immediately_persisted(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.db"
    engine = FactorExecutionEngineV2(
        _manifest(3), ledger, workers=1, chunk_size=1
    )
    engine.run(run_post_pass=False)
    assert _rows(ledger, "SELECT evaluation_count FROM execution_chunks") == [
        (1,),
        (1,),
        (1,),
    ]
    assert len(_rows(ledger, "SELECT task_id FROM execution_evaluations")) == 3


def test_wall_clock_triggers_commit(tmp_path: Path) -> None:
    class AdvancingClock:
        def __init__(self) -> None:
            self.value = 0.0

        def __call__(self) -> float:
            self.value += 31.0
            return self.value

    ledger = tmp_path / "ledger.db"
    engine = FactorExecutionEngineV2(
        _manifest(2),
        ledger,
        workers=1,
        chunk_size=50,
        checkpoint_seconds=30,
        clock=AdvancingClock(),
    )
    engine.run(run_post_pass=False)
    reasons = _rows(ledger, "SELECT reason FROM execution_chunks")
    assert ("WALL_CLOCK",) in reasons


def test_interrupt_then_resume_without_duplicate_evaluation(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.db"
    manifest = _manifest(8)
    with pytest.raises(ExecutionInterrupted):
        FactorExecutionEngineV2(
            manifest, ledger, workers=1, chunk_size=2
        ).run(interrupt_after=3, run_post_pass=False)
    before = len(_rows(ledger, "SELECT task_id FROM execution_evaluations"))
    FactorExecutionEngineV2(
        manifest, ledger, workers=1, chunk_size=2
    ).run(resume=True, run_post_pass=False)
    rows = _rows(
        ledger,
        "SELECT task_id,COUNT(*) FROM execution_evaluations GROUP BY task_id",
    )
    assert len(rows) == 8
    assert all(count == 1 for _, count in rows)
    assert before < 8


def test_half_finished_chunk_is_not_marked_complete(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.db"
    manifest = _manifest(2)
    ledger = ExecutionLedger(ledger_path)
    ledger.initialize(manifest, {"workers": 1}, resume=False)
    task = manifest.tasks[0]
    ledger.mark_claimed(manifest.run_id, task.task_id, 1)
    ledger.close()
    reopened = ExecutionLedger(ledger_path)
    reopened.initialize(manifest, {"workers": 1}, resume=True)
    state = _rows(
        ledger_path,
        f"SELECT state FROM execution_tasks WHERE task_id='{task.task_id}'",
    )[0][0]
    assert state == TaskState.RETRYABLE_FAILED.value


def _logical_results(path: Path) -> list[tuple]:
    return _rows(
        path,
        """SELECT t.task_id,e.evaluation_identity,e.result_json,
                  e.native_event_json,e.non_overlap_json,e.hac_json,
                  e.bootstrap_json,e.portfolio_json,e.errors_json
           FROM execution_tasks t
           LEFT JOIN execution_evaluations e USING(run_id,task_id)
           ORDER BY t.task_id""",
    )


def test_one_and_two_worker_results_are_identical(tmp_path: Path) -> None:
    manifest = _manifest(10)
    one = tmp_path / "one.db"
    two = tmp_path / "two.db"
    FactorExecutionEngineV2(manifest, one, workers=1, chunk_size=3).run()
    FactorExecutionEngineV2(manifest, two, workers=2, chunk_size=3).run()
    assert _logical_results(one) == _logical_results(two)
    assert _rows(
        one,
        """SELECT stage,payload_json FROM execution_post_pass
           WHERE stage <> 'COMPLETE' ORDER BY stage""",
    ) == _rows(
        two,
        """SELECT stage,payload_json FROM execution_post_pass
           WHERE stage <> 'COMPLETE' ORDER BY stage""",
    )


def test_worker_crash_is_retried_and_recovers(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.db"
    engine = FactorExecutionEngineV2(
        _manifest(2, crash_attempts=1),
        ledger,
        workers=1,
        chunk_size=1,
        max_retries=2,
    )
    engine.run(run_post_pass=False)
    assert _rows(
        ledger, "SELECT DISTINCT attempt,state FROM execution_tasks"
    ) == [(2, TaskState.POST_PASS_PENDING.value)]


def test_hard_process_crash_reopens_pool_and_recovers(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.db"
    engine = FactorExecutionEngineV2(
        _manifest(1, hard_crash_attempts=1),
        ledger,
        workers=1,
        chunk_size=1,
        max_retries=2,
    )
    engine.run(run_post_pass=False)
    assert _rows(
        ledger, "SELECT attempt,state FROM execution_tasks"
    ) == [(2, TaskState.POST_PASS_PENDING.value)]


def test_coordinator_is_only_sqlite_writer(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.db"
    FactorExecutionEngineV2(_manifest(4), ledger, workers=2).run(
        run_post_pass=False
    )
    writer_pids = {
        row[0]
        for row in _rows(
            ledger,
            """SELECT writer_pid FROM execution_runs
               UNION SELECT writer_pid FROM execution_chunks
               UNION SELECT writer_pid FROM execution_evaluations""",
        )
    }
    assert writer_pids == {os.getpid()}
    assert {
        row[0]
        for row in _rows(ledger, "SELECT DISTINCT worker_pid FROM execution_evaluations")
    }.isdisjoint(writer_pids)


def test_task_and_result_queues_are_bounded(tmp_path: Path) -> None:
    engine = FactorExecutionEngineV2(
        _manifest(), tmp_path / "ledger.db", workers=2, chunk_size=5
    )
    assert engine.task_queue_capacity == 4
    assert engine.result_queue_capacity == 5
    assert engine.task_queue_capacity < len(engine.manifest.tasks)


def test_memory_budget_rejects_oversized_task(tmp_path: Path) -> None:
    value = {
        "run_id": "large",
        "dataset_identity": "dataset",
        "evaluation_policy": {"frozen": True},
        "chronological_segments": ["DISCOVERY"],
        "tasks": [
            {
                "trial_id": "trial",
                "factor_identity": "factor",
                "instrument": "BTC",
                "segment": "DISCOVERY",
                "horizon": "1H",
                "payload": {"blob": "x" * (1024 * 1024 + 1)},
            }
        ],
    }
    engine = FactorExecutionEngineV2(
        FrozenExecutionManifest.from_dict(value),
        tmp_path / "ledger.db",
        memory_budget_mb=1,
    )
    with pytest.raises(MemoryBudgetExceeded):
        engine.validate_task_graph()


def test_checkpoint_schema_and_identity_are_recorded(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.db"
    manifest = _manifest(2)
    FactorExecutionEngineV2(manifest, ledger, workers=1).run(
        run_post_pass=False
    )
    row = _rows(
        ledger,
        """SELECT manifest_hash,dataset_identity,evaluation_version,
                  completed_tasks_json,incomplete_tasks_json,retry_tasks_json,
                  worker_config_json,bootstrap_seed,bootstrap_state_json,
                  post_pass_state_json,ledger_hash
           FROM execution_checkpoints ORDER BY committed_sequence DESC LIMIT 1""",
    )[0]
    assert row[0:3] == (
        manifest.manifest_hash,
        manifest.dataset_identity,
        manifest.evaluation_version,
    )
    assert row[7] == 7
    assert all(row[index] for index in range(3, 11))


@pytest.mark.parametrize("change", ["manifest", "dataset"])
def test_changed_identity_rejects_resume(tmp_path: Path, change: str) -> None:
    ledger = tmp_path / "ledger.db"
    original = _manifest(2)
    FactorExecutionEngineV2(original, ledger, workers=1).run(
        run_post_pass=False
    )
    changed = (
        _manifest(3)
        if change == "manifest"
        else _manifest(2, dataset="fixture-dataset-v2")
    )
    with pytest.raises(IdentityMismatch):
        FactorExecutionEngineV2(changed, ledger, workers=1).run(
            resume=True, run_post_pass=False
        )


def test_post_pass_resumes_by_independent_stage(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.db"
    manifest = _manifest(6)
    engine = FactorExecutionEngineV2(manifest, ledger, workers=1)
    engine.run(run_post_pass=False)
    with pytest.raises(ExecutionInterrupted):
        engine.run_post_pass(interrupt_after_stage="FDR")
    fdr_before = _rows(
        ledger,
        "SELECT checksum FROM execution_post_pass WHERE stage='FDR'",
    )[0][0]
    engine.run_post_pass()
    assert _rows(
        ledger,
        "SELECT checksum FROM execution_post_pass WHERE stage='FDR'",
    )[0][0] == fdr_before
    assert _rows(
        ledger, "SELECT post_pass_stage FROM execution_runs"
    ) == [("COMPLETE",)]


def test_fdr_family_does_not_change_with_parallelism(tmp_path: Path) -> None:
    manifest = _manifest(8)
    ledgers = [tmp_path / "one.db", tmp_path / "two.db"]
    for workers, ledger in enumerate(ledgers, start=1):
        FactorExecutionEngineV2(manifest, ledger, workers=workers).run()
    payloads = [
        json.loads(
            _rows(
                ledger,
                "SELECT payload_json FROM execution_post_pass WHERE stage='FDR'",
            )[0][0]
        )
        for ledger in ledgers
    ]
    assert payloads[0]["family_members"] == payloads[1]["family_members"]
    assert payloads[0]["values"] == payloads[1]["values"]


def test_dsr_uses_complete_attempt_accounting(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.db"
    FactorExecutionEngineV2(_manifest(7), ledger, workers=1).run()
    payload = json.loads(
        _rows(
            ledger,
            """SELECT payload_json FROM execution_post_pass
               WHERE stage='PSR_DSR_PBO'""",
        )[0][0]
    )
    assert payload["attempt_count"] == 7
    assert payload["complete_evaluation_count"] == 7


def _engine_source() -> str:
    return Path("dashboard/factor_execution_engine_v2.py").read_text(
        encoding="utf-8"
    )


def test_engine_does_not_call_generator() -> None:
    source = _engine_source()
    assert "deterministic_generate" not in source
    assert "factor_generator" not in source


def test_engine_does_not_access_holdout_or_oot() -> None:
    source = _engine_source()
    assert "holdout_loader" not in source
    assert "oot_loader" not in source


def test_engine_does_not_call_strategy_or_order_apis() -> None:
    source = _engine_source()
    forbidden = (
        "create_order",
        "place_order",
        "submit_order",
        "strategy_api",
    )
    assert not any(name in source for name in forbidden)


def test_dry_run_does_not_create_ledger(tmp_path: Path) -> None:
    manifest = _manifest(2)
    manifest_path = tmp_path / "manifest.json"
    # This file is a synthetic fixture, not a formal factor-library artifact.
    value = {
        "run_id": manifest.run_id,
        "dataset_identity": manifest.dataset_identity,
        "evaluation_version": manifest.evaluation_version,
        "evaluation_policy": dict(manifest.evaluation_policy),
        "chronological_segments": list(manifest.chronological_segments),
        "bootstrap_seed": manifest.bootstrap_seed,
        "tasks": [
            {
                "trial_id": task.trial_id,
                "factor_identity": task.factor_identity,
                "instrument": task.instrument,
                "segment": task.segment,
                "horizon": task.horizon,
                "family": task.family,
                "sequence": task.sequence,
                "payload": dict(task.payload),
            }
            for task in manifest.tasks
        ],
    }
    manifest_path.write_text(json.dumps(value), encoding="utf-8")
    ledger = tmp_path / "dry-run.db"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_factor_execution_engine_v2.py",
            "--manifest",
            str(manifest_path),
            "--ledger",
            str(ledger),
            "--dry-run",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["dry_run"] is True
    assert not ledger.exists()
