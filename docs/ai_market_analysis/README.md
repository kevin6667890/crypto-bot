# Evidence-grounded AI market analysis

The AI market-analysis subsystem turns frozen deterministic evidence into structured, auditable explanations. It is an analysis and presentation layer: it does not own market facts, strategy selection, optimization, risk rules, activation, or order execution.

## Current flow

```text
confirmed public observations
  -> deterministic market facts, quality, structure, levels and scenarios
  -> immutable context and evidence registries
  -> bounded structured report request
  -> provider-generated report
  -> deterministic schema, reference, numeric and semantic audits
  -> persisted report presentation, history and deep links
```

The implementation is split across `dashboard/ai_market_analysis/`, the read-only presentation endpoints in `dashboard/paper_api.py`, and `frontend/src/AiReportPresentation.tsx`. The React product displays only an audit-eligible report body. A newer pending or failed report does not replace the last eligible historical report, and stale or superseded evidence is labeled rather than presented as current.

## Evidence and identity

Each report is bound to an instrument, report mode, language, confirmed decision time, context identity, registry snapshot, prompt/model versions, and source-version manifest. The context can include:

- multi-timeframe trend, structure, momentum, volume and data-quality facts;
- deterministic market phase, key levels, invalidations and scenario trees;
- staged order-flow observations with explicit missing or partial coverage;
- bounded paper-position context when the selected mode permits it;
- verified macro evidence only when the source and runtime policy permit it.

Report, context, registry, audit and presentation identities remain persisted so the UI can reopen report history through a `report_id` deep link and preserve provenance across restarts.

## Deterministic safety boundary

The AI layer may summarize the frozen evidence, organize supporting and conflicting observations, describe uncertainty, and explain conditional scenarios. It cannot:

- mutate a deterministic fact or signal;
- choose or search strategy parameters;
- influence optimization ranking, holdout, OOT, or transfer results;
- bypass data-quality or audit failures;
- change paper risk controls or strategy lifecycle state;
- activate a strategy or create an exchange order.

Generated text is not trusted on arrival. A deterministic audit validates its schema, evidence references, numeric grounding, direction semantics, context identity, and eligibility. Pending, failed, mismatched, or legacy-schema reports fail closed.

## Persistence and operations

Reports, requests, frozen contexts, registry snapshots, audits and lifecycle events use a separate SQLite-backed evidence store. The runtime includes bounded queues, concurrency and token/cost budgets, retry classification, retention/archive controls, health reporting, privacy checks, and a durable live-provider kill switch. Provider credentials remain server-side and are never part of a report, URL, frontend bundle, or captured portfolio asset.

The public product surface exposes read-only summaries, history and eligible report details. Administrative Shadow presentation and position-detail paths remain separately gated.

## Document map

The remaining files in this directory record the subsystem's design history, staged rollout controls, audits, and operational evidence. Earlier phase documents intentionally describe the state at their recorded commit and should not be read as the current repository status.

- `contract_and_causality.md`: causality rules and report contracts.
- `golden_evaluation.md`: golden cases, anti-patterns and evaluation criteria.
- `current_system_audit.md` and `current_vs_target_gap.md`: historical baseline and gap analysis.
- `implementation_plan.md`: staged AI-2 through AI-6 implementation plan.
- `ai6_production_readiness_checklist.md`: rollout gates and evidence requirements.
- `ai6_production_canary_runbook.md` and `ai6_rollback_runbook.md`: bounded operations and rollback controls.
- `ai6_privacy_review.md`: privacy and exposure review.

No historical document authorizes autonomous trading or changes the deterministic research and paper-execution boundary.
