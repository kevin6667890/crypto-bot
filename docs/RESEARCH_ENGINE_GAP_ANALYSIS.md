# Crypto Quant Research Engine — Gap Analysis

Date: 2026-09-29  
Scope: first local-only Research Engine. This document records an audit before
new scheduling or search code is introduced.

## Decision

Do not extend the single VPVA/Bollinger experiment or any unlimited parameter
grid. Reuse the existing causal execution, data identity, discovery,
robustness, validation, and persistence primitives behind a new small
orchestrator. The Engine's default outcome is an empty surviving set.

## Existing capabilities to reuse

| Capability | Existing component | Reuse decision |
|---|---|---|
| Confirmed, versioned OHLCV cache | `okx_history.py`, `canonical_dataset.py`, `research_repository.py` | Canonical dataset fingerprint must be required input to every run. |
| Causal next-open execution with fee/slippage | `backtest_engine.py`, `discovery_execution.py` | Single formal execution adapter; no new fill engine. |
| Deterministic candidate/evaluation identity | `discovery_identity.py`, V2/V2.1 registries | Extend identity with hypothesis and feature-definition hashes; do not create parallel ad-hoc IDs. |
| Development-only candidate discovery | `discovery_service.py`, `dataset_service.py` | Reuse as Stage 1 adapter, but put it under cycle budget and unified ledger. |
| Parameter neighbourhood and cost stress | `discovery_robustness.py`, `discovery_robustness_service.py` | Reuse for Stage 2; formalize hard pass/fail gates. |
| Walk-forward, cross-asset validation | `research_service.py`, `validation_service.py`, repository validation suites | Reuse metrics/backtest primitives; add one stage contract and immutable stage evidence. |
| Bootstrap / trade-order Monte Carlo | `robustness.py`, `validation_service.py` | Reuse only after earlier gates pass. |
| Feature ablation | `discovery_ablation.py`, `discovery_ablation_service.py` | Reuse as required evidence for multi-feature survivors. |
| Holdout governance | optimization family/run/trial tables in `research_repository.py`; `test_experiment_governance.py` | Preserve explicit reveal semantics; new Engine must be stricter and ledger every access. |
| Historical append-preserving registry | `global_research_registry.py` | Reuse canonical hashing conventions; it is an importer, not a live execution ledger. |

## Current data contract

Formal, available inputs are confirmed local OHLCV for BTC/ETH/SOL on reliable
15m, 1H, and 4H partitions. They support Trend, Momentum, Mean Reversion,
Volatility, Volume, Bollinger-derived signals, `VPVA_PROXY`, and relative
strength based on those three assets.

`VPVA_PROXY` is an OHLCV proxy: completed-bar volume is assigned to typical
price bins. It is explicitly not true VPVA and never order-flow evidence.

True order-flow VPVA, CVD, OI, funding, and basis are **UNAVAILABLE** for
formal scoring. A hypothesis requiring any of them must produce an
`UNAVAILABLE` ledger event and no candidate. Missing values must never be
zero-filled, forward-filled, or replaced by a proxy under the same feature
name.

## Material gaps

1. **No unified hypothesis schema.** Discovery templates, factor programs and
   optimization requests have separate parameter contracts. A common immutable
   definition must include economic rationale, requirements, feature hash,
   allowed assets/timeframes, parameter space, entry/exit/sizing/execution.
2. **No single staged scheduler.** Existing discovery, robustness, validation
   and Monte Carlo services can be run independently, but do not enforce a
   common Stage 0–6 progression or candidate attrition.
3. **No Engine-owned append-only experiment ledger.** Existing tables record
   runs/trials in several domains; none answers, for one surviving candidate,
   every hypothesis/candidate attempted and every protected-data access.
4. **Research budgets are fragmented.** Discovery has local maximums and
   optimization has trial limits, but there is no cycle/family budget for
   hypotheses, candidate attempts, parameter trials and holdout reveals.
5. **Gating is partially score-oriented.** Discovery calculates scores and
   Pareto ranks. The Engine must instead use configurable hard gates and a
   surviving set; metrics remain descriptive only.
6. **Validation evidence is not normalized.** Fold, cross-asset, cost,
   neighbourhood, bootstrap and ablation outputs need one artifact schema and
   one rejection vocabulary.
7. **Holdout protection is not Engine-wide.** Optimization families have
   explicit reveal controls, while other routes are not uniformly bound to a
   protected access ledger.
8. **Artifacts are heterogeneous.** SQLite is used for operational evidence;
   JSON reports already exist. The Engine needs JSON metadata plus CSV exports
   first; Parquet is optional because no repository dependency currently
   guarantees it.

## Leakage and architecture risks found

* `discovery_service.FOLDS` ends before 2025-05-01, which is intentionally
  pre-holdout in its existing policy but is not the Engine's future universal
  split contract. New cycles must store explicit boundaries, never reuse an
  implicit module constant.
* Existing scripts under `scripts/` can run direct local experiments outside
  repositories. They are historical evidence only; they must not be imported
  as Engine results unless registered with full dataset, execution and code
  identity metadata.
* The OHLCV VPVA feature implementation is causal and labelled proxy, but must
  be carried in every candidate's feature-definition hash to prevent mixing
  proxy versions.
* Cross-asset normalization is not yet standardized. Relative-strength inputs
  must align each bar to only already-confirmed bars across every asset.
* `run_execution_backtest` uses next-bar-open execution correctly. New feature
  providers must continue to use only the current confirmed candle and prior
  data; all new feature builders require causal tests before registration.

## Minimal architecture proposal

Add a focused `research_engine/` package (not a rewrite of `dashboard/`):

* `schema.py`: immutable `Hypothesis`, `ResearchCycle`, `Candidate`,
  `StageResult`, data capability and hard-gate models.
* `budget.py`: configuration-only budget checks; returns `BUDGET_EXHAUSTED`.
* `ledger.py`: append-only SQLite ledger with deterministic identities and
  explicit holdout access records.
* `gates.py`: pure hard-gate evaluation with named failures, never a composite
  ranking score.
* `scheduler.py`: Stage 0–6 state machine. It calls existing execution,
  discovery robustness, validation, and Monte Carlo adapters.
* `adapters/`: narrow bridges for V2 templates first, followed by Trend,
  Momentum, and Mean Reversion registrations.
* `cli.py`: `research run`, `status`, `report`, `candidate`, `hypothesis`.

Use a separate local Engine SQLite database under `.research/`; source market
data remains read-only. Artifacts are JSON metadata and CSV tables generated
from ledger queries. Failed/unavailable/budget-exhausted attempts are retained.

## Stage contract

| Stage | Purpose | Data permission | Default attrition |
|---|---|---|---|
| 0 | Hypothesis registration and capability check | development metadata only | unavailable -> terminal |
| 1 | Cheap development backtest | development | hard gates |
| 2 | Parameter neighbours, 1.5x/2x costs, ablation | development | hard gates |
| 3 | Walk-forward/OOS | OOS only | hard gates |
| 4 | Cross-asset/timeframe | OOS only | hard gates |
| 5 | Bootstrap/Monte Carlo | surviving trade samples only | risk gates |
| 6 | Protected holdout, then final OOT | explicit, ledgered reveal | no redesign permitted |

## Recommended first-cycle defaults (configuration, not code constants)

* 8 hypotheses/family; 32 candidates/family; 64 parameter trials/family;
  1 protected holdout reveal/cycle.
* Stage 1: at least 30 trades and no missing bars.
* Stage 2: at least 60% parameter neighbours retain non-negative median OOS
  return; 2x cost result must not be positive only because costs were omitted.
* Stage 3: at least 3 walk-forward folds, 60% profitable folds, recorded worst
  fold and maximum drawdown.
* Stage 4: record asset-specific versus general evidence; do not require every
  asset to pass.
* Stage 5: minimum trade count and configured drawdown/tail-risk bounds.

Thresholds are conservative starting policies and must be versioned per cycle;
they are not a promise of profitability.

## Implementation order

1. Implement schema, ledger, budget and hard gates with unit tests.
2. Implement a deterministic staged scheduler and CLI/report queries.
3. Register the existing `VPVA_PROXY`/Boll experiment only as an end-to-end
   integration fixture; do not search or optimize it further.
4. Register existing Trend, Momentum and Mean Reversion adapters.
5. Run a deliberately small cycle to prove ledger, budget, unavailable feature,
   OOS, holdout protection, artifacts and rejection paths.
