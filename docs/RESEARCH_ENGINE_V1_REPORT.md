# Research Engine V1 Report

## Architecture

`research_engine/` is a thin local orchestration layer:

* `schema.py` — immutable hypothesis/cycle/candidate contracts and deterministic candidate identities.
* `features.py` — explicit AVAILABLE/UNAVAILABLE feature registry.
* `gates.py` — configurable hard gates; no composite score exists.
* `ledger.py` — append-only SQLite experiment ledger, protected holdout log and repeated-reveal block.
* `budget.py` — per-cycle/per-family budgets with durable `BUDGET_EXHAUSTED` events.
* `scheduler.py` — fail-closed Stage 0–6 progression.
* `cli.py` — `research status|report|candidate|hypothesis` semantics via `python -m research_engine.cli`.
* `scripts/run_research_engine_v1.py` — small real-cycle runner.

## Reused components

The Engine reuses `dashboard.discovery_execution.run_discovery_candidate_backtest`,
the canonical next-open engine, fixed fee/slippage execution assumptions, V2
registries, feature hashes and the existing confirmed local SQLite OHLCV data.
It does not reuse the legacy optimizer holdout route.

## Tests

Focused Engine contracts: 10 passed (`test_research_engine_contracts.py`,
`test_research_engine_ledger_budget.py`). Existing causal execution/foundation
tests also passed before integration. Contracts cover deterministic identity,
unavailable data, no composite score, parameter instability, append-only
ledger, duplicate/repeated holdout handling and budget exhaustion.

## Leakage audit

The canonical execution path remains causal: confirmed signal at close and
entry at next open. VPVA is named `VPVA_PROXY` and uses only completed bars.
The existing optimizer was excluded because it loads holdout candles before
development ranking; this is governance contamination even though it is not a
signal-timestamp lookahead. Cross-asset relative strength is not yet registered
because causal alignment has not been implemented.

## Real research cycle

Artifact directory: `.research/research_cycle_v1_final/`.

* Hypotheses: 4 — Trend, Momentum, Mean Reversion, VPVA_PROXY/Boll.
* Unique candidates: 12 — four bounded V2 candidates per first three families.
* VPVA_PROXY/Boll: registered as an integration hypothesis only; no new grid
  was generated.
* Stage 0: 3 available strategy hypotheses; VPVA_PROXY available but adapter
  intentionally not expanded.
* Stage 1–5 survivors: 0. Each real candidate stopped at the cheap development
  gate; no expensive stage was run.
* Holdout tested: 0; final survivors: 0.

The summary and append-only CSV ledger are available in the artifact directory.
The survivor rate is therefore 0/12, and no result is presented without its
search-history denominator.

## Rejections

Primary rejection path was Stage 1 hard gates (minimum trade count and/or
maximum drawdown). No gate was changed after seeing results.

## Holdout

No candidate received access. The ledger permits one formal reveal per
`research_cycle_id + candidate_id`; repeats are blocked unless an explicit
override reason is recorded as `REPEATED_HOLDOUT_ACCESS`.

## Survivors

Empty set. No shadow, paper or live action is authorized.

## Limitations

* Two-year OHLCV history limits long-horizon evidence.
* `VPVA_PROXY` is not true trade-level VPVA.
* CVD, OI, funding and basis are unavailable for formal scoring.
* Multiple-testing risk remains material; the ledger exposes rather than
  eliminates it.
* Cross-asset relative strength needs a causal alignment adapter before use.

## Next highest-value improvements

1. Migrate verified funding/basis history with timestamps and availability gates.
2. Implement causal cross-asset alignment and relative-strength adapter.
3. Add a versioned VPVA_PROXY/Boll adapter without expanding its parameter grid.
4. Add Engine artifact exporters for all empty/non-empty stage tables.
5. Obtain verified trade-level historical data before enabling true VPVA/CVD/OI.
