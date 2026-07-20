"""Command line entry point for anonymized persisted research evidence."""
from __future__ import annotations
import argparse
from pathlib import Path
from dashboard.research_repository import ResearchRepository
from dashboard.report_exporter import export_report

parser = argparse.ArgumentParser(description="Export an anonymized paper-research report.")
group = parser.add_mutually_exclusive_group(required=True)
group.add_argument("--optimization-run", type=int); group.add_argument("--experiment-family", type=int)
parser.add_argument("--output", type=Path, required=True); parser.add_argument("--json-output", type=Path)
parser.add_argument("--database", type=Path, default=Path("data_cache/paper_trades.db"), help="Optional local SQLite input; never included in output.")
args = parser.parse_args()
export_report(ResearchRepository(args.database), args.output, args.optimization_run, args.experiment_family, args.json_output)
