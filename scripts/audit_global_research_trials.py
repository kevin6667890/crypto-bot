"""Import existing Phase 6 evidence and report global DSR trial accounting."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.global_research_registry import (  # noqa: E402
    DEFAULT_REGISTRY_PATH,
    GlobalResearchRegistry,
    PHASE_IMPORTERS,
    SUPPORTED_PHASES,
    discover_research_artifacts,
)


def _phase_path(value: str) -> tuple[str | None, Path]:
    if "=" not in value:
        return None, Path(value)
    phase, path = value.split("=", 1)
    normalized = phase.upper().removeprefix("PHASE")
    if normalized not in SUPPORTED_PHASES:
        raise argparse.ArgumentTypeError("Artifact phase must be 6A through 6G")
    return normalized, Path(path)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Read existing reports/ledgers into the global research registry; "
            "this command never runs research."))
    result.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    result.add_argument("--repo", type=Path, default=ROOT)
    result.add_argument(
        "--artifact", action="append", default=[], metavar="[PHASE=]PATH",
        help="Additional report or ledger; repeat as needed.")
    result.add_argument(
        "--no-auto-discover", action="store_true",
        help="Only import explicitly supplied artifacts.")
    result.add_argument(
        "--include-phase", action="append", choices=SUPPORTED_PHASES,
        help="Accounting phase; default is all Phase 6A-6G.")
    result.add_argument(
        "--exclude-phase", action="append", default=[],
        choices=SUPPORTED_PHASES)
    result.add_argument(
        "--exclude-reason", action="append", default=[], metavar="PHASE=REASON")
    result.add_argument("--output", type=Path, help="Also write JSON output.")
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    registry = GlobalResearchRegistry(arguments.registry)
    registry.migrate()
    explicit = [_phase_path(value) for value in arguments.artifact]
    supplied = [path for _, path in explicit]
    paths = (
        discover_research_artifacts(arguments.repo, supplied)
        if not arguments.no_auto_discover else
        sorted({path.resolve() for path in supplied}))
    phase_hints = {path.resolve(): phase for phase, path in explicit if phase}
    imported = []
    skipped = []
    for path in paths:
        try:
            phase = phase_hints.get(path.resolve())
            if phase:
                results = PHASE_IMPORTERS[phase](registry, path)
            else:
                results = registry.import_path(path)
            imported.extend(result.__dict__ for result in results)
        except (ValueError, json.JSONDecodeError) as error:
            skipped.append({"path": str(path), "reason": str(error)})
    reasons = {}
    for item in arguments.exclude_reason:
        if "=" not in item:
            raise SystemExit("--exclude-reason must be PHASE=REASON")
        phase, reason = item.split("=", 1)
        reasons[phase.upper().removeprefix("PHASE")] = reason
    accounting = registry.accounting(
        include_phases=arguments.include_phase or SUPPORTED_PHASES,
        exclude_phases=arguments.exclude_phase,
        exclusion_reasons=reasons)
    payload = {
        "registry_path": str(arguments.registry.resolve()),
        "imports": imported,
        "skipped_artifacts": skipped,
        **accounting,
    }
    rendered = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
