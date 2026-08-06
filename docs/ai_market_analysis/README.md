# Phase AI-1: professional market-analysis contract

This directory is a design and static-validation deliverable. It does not change a production API, collector, aggregation task, strategy, order path, prompt, database, or deployment. Every value under `fixtures/ai_market_analysis` is synthetic design-test data, never a claim about the current market.

The target flow is:

`raw observations → deterministic indicators/quality → deterministic structure → staged order-flow attribution → levels/scenarios → MarketAnalysisContext → LLM wording → fact audit → UI`.

Contract version `v1` deliberately differs from the existing research-only `MarketAnalysisContextV2`: V2 is a valuable input, but it does not contain the timeline, staged attribution, scenario tree, position-plan semantics, macro provenance, report request/response, or post-generation audit required here.

Files:

- `current_system_audit.md`: evidence-backed current call chain and data inventory.
- `current_vs_target_gap.md`: capability-by-capability gap analysis.
- `contract_and_causality.md`: target modules, fields, causal and gap rules, report modes.
- `golden_evaluation.md`: golden sample, anti-patterns, automated scores.
- `implementation_plan.md`: file-level AI-2 through AI-6 plan and budgets.
- `schemas/ai_market_analysis/*.schema.json`: Draft 2020-12 contracts.
- `fixtures/ai_market_analysis/*.json`: one ETH golden context and five attribution counterexamples.
- `tests/test_ai_market_analysis_contract.py`: structural and cross-object semantic validation.

Status vocabulary in audit tables:

- `EXISTS`: directly proven in the named source.
- `PARTIAL`: useful implementation exists but does not meet the target contract.
- `NOT_IMPLEMENTED`: repository evidence proves no target implementation on this baseline.
- `UNKNOWN`: code does not prove the fact.
- `REQUIRES_RUNTIME_AUDIT`: only a live read-only coverage query can determine the answer.
