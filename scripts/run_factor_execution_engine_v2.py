"""Run the Factor Execution Engine v2 against a frozen manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.factor_execution_engine_v2 import (  # noqa: E402
    ExecutionInterrupted,
    FactorExecutionEngineV2,
    FrozenExecutionManifest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2, choices=range(1, 5))
    parser.add_argument("--chunk-size", type=int, default=50)
    parser.add_argument("--checkpoint-seconds", type=float, default=30.0)
    parser.add_argument("--memory-budget-mb", type=int, default=512)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--interrupt-after", type=int)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    manifest = FrozenExecutionManifest.load(arguments.manifest)
    engine = FactorExecutionEngineV2(
        manifest,
        arguments.ledger,
        workers=arguments.workers,
        chunk_size=arguments.chunk_size,
        checkpoint_seconds=arguments.checkpoint_seconds,
        memory_budget_mb=arguments.memory_budget_mb,
    )
    if arguments.dry_run:
        # Deliberately does not initialize or write the ledger.
        result = engine.validate_task_graph()
    else:
        try:
            result = engine.run(
                resume=arguments.resume,
                interrupt_after=arguments.interrupt_after,
            )
        except ExecutionInterrupted as error:
            result = {
                "run_id": manifest.run_id,
                "status": "INTERRUPTED",
                "message": str(error),
            }
            print(json.dumps(result, indent=2, sort_keys=True))
            return 130
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
