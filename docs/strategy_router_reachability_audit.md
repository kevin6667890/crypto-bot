# Strategy Router Reachability Audit V1

This Development-only audit verifies the frozen Phase 4A3 artifact, maps the
Context/State/Router/Lifecycle/Geometry contracts, executes structured positive
controls through the public Router and Lifecycle, and streams the immutable
Phase 4A3 lifecycle ledger for blocker, geometry, parameter and identity
diagnostics.

The audit is deliberately non-executing: it creates no orders, trades, ledger,
or return statistics. `DevelopmentAccessGuard` rejects Validation and locked OOT
timestamps. No strategy thresholds, gates, ordering, parameters, or V2 behavior
are changed.

Run:

```powershell
python scripts/run_strategy_router_reachability_audit.py
```

Outputs are written beneath
`.runtime/strategy-router-reachability-audit/<audit_id>/`, with a SHA-256 for
every artifact member and one aggregate content identity.

The key contract check distinguishes `MarketStateEngineV2.evaluate()`—used by
the formal Phase 4A3 replay—from `compare(previous_context, current_context)`.
Confirmed reclaim/rejection level transitions are produced only by `compare`.
The audit records that as a missing producer; it does not alter either method.
