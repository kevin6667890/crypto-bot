"""Run Phase 6G against the exact frozen Phase 6F manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.factor_statistical_audit import (  # noqa: E402
    FactorStatisticalAudit,
    FrozenExperiment,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase6f-ledger", type=Path, required=True)
    parser.add_argument("--phase6f-snapshot", type=Path, required=True)
    parser.add_argument("--phase6f-report", type=Path, required=True)
    parser.add_argument("--audit-ledger", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    experiment = FrozenExperiment.load(
        arguments.phase6f_ledger, arguments.phase6f_snapshot,
        arguments.phase6f_report)
    result = FactorStatisticalAudit(
        experiment, arguments.audit_ledger).run(report_path=arguments.report)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
