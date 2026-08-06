"""Pure deterministic price-structure analysis for AI market contexts.

Importing this package performs no I/O and mutates no global application state.
"""

from .context_adapter import build_market_analysis_context
from .timeframe_facts import build_multi_timeframe_facts, build_timeframe_facts

__all__ = ["build_market_analysis_context", "build_multi_timeframe_facts", "build_timeframe_facts"]
