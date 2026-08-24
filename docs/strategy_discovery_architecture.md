# Strategy Discovery architecture

The long-term search plane is a Factor Program-centered hybrid. Factor Program is the single extensible candidate representation. Template V2.1 supplies benchmarks, regression oracles, grammar seeds, and a conservative control; Template V1/V2 remain replay compatibility only. Factor AutoResearch produces statistical factor evidence, not a second strategy generator. Phase6 remains an advanced source of ordered lifecycle, temporal, geometry, multi-timeframe, and checkpoint ideas that are progressively absorbed into Factor Program rather than shipped as a parallel discovery product.

Every candidate type enters one validation contract:

```text
Dataset fingerprint
  -> Development folds (search and ranking only)
  -> Freeze candidate/config/runtime identity
  -> Walk-forward
  -> Primary holdout
  -> Final OOT
  -> Cross-asset
  -> Robustness
  -> Approval decision
  -> Approved Strategy Registry
```

Holdout and OOT never feed candidate tuning or ranking. Each stage persists the candidate identity, configuration hash, dataset fingerprint, factor/program version, runtime version, result, and failure reason. Bounded development batches and durable checkpoints make resume deterministic without repeating completed candidates or growing worker memory without bound.

The Registry owns immutable definitions and the `REJECTED`, `APPROVED`, `ACTIVE`, and `RETIRED` lifecycle, including singleton-ACTIVE and switch audit. Candidate identity alone cannot express that lifecycle. No approved candidate is a valid outcome; the system must not weaken validation to manufacture a winner.

Canonical Paper knows nothing about whether a strategy came from Factor Program, Template V2.1, a temporal experiment, or a manual frozen candidate. It accepts only an atomic ACTIVE Registry snapshot, exact frozen runtime contract, canonical input identity, and risk contract. With no ACTIVE entry it returns `WAIT`; legacy execution is advanced/replay compatibility and never an implicit fallback. StrategyRouterV2 remains a research and suitability explanation tool and is outside the Paper execution caller graph.

`rules_blueprint.py`, `ultimate_bot.py`, old template runners, and standalone temporal discovery remain legacy/advanced until replay callers and evidence compatibility are proven replaceable. Discovery is offline and deterministic, and it never mutates Paper configuration or sends exchange orders.
