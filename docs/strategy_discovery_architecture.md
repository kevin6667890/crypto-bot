# Strategy Discovery architecture

The canonical research and paper route is `dashboard/decision_engine.py` →
`dashboard/strategy_rules.py` → `dashboard/backtest_engine.py`.  Discovery uses
the same causal, next-bar-open and adverse-slippage execution convention through
its bounded template adapter; it never mutates a paper configuration.

`rules_blueprint.py` and `ultimate_bot.py` are legacy standalone rule programs.
They are not imported by the Discovery Lab and remain documented only for
backwards compatibility.

Discovery is offline, deterministic (fixed seed/configuration/data fingerprint),
and development-only.  Holdout/OOT are intentionally not loaded by discovery.
