AI_MARKET_FACTS_VERSION = "ai-market-facts-v1"
AI_TIMEFRAME_STRUCTURE_VERSION = "ai-timeframe-structure-v1"
AI_SWING_STRUCTURE_VERSION = "ai-swing-structure-v1"
AI_RANGE_COMPRESSION_VERSION = "ai-range-compression-v1"
AI_MARKET_TIMELINE_VERSION = "ai-market-timeline-v1"
AI_CONTEXT_ADAPTER_VERSION = "ai-market-context-adapter-v1"
AI_CONTEXT_SCHEMA_VERSION = "market-analysis-context-v1"
AI_CANONICAL_IDENTITY_VERSION = "ai-market-canonical-identity-v1"
AI_ORDERFLOW_WINDOW_VERSION = "ai-orderflow-window-v1"
AI_ORDERFLOW_METRICS_VERSION = "ai-orderflow-metrics-v1"
AI_ORDERFLOW_ATTRIBUTION_VERSION = "ai-orderflow-attribution-v1"
AI_KEY_LEVEL_ENGINE_VERSION = "ai-key-level-engine-v1"
AI_KEY_LEVEL_ZONE_VERSION = "ai-key-level-zone-v1"
AI_SCENARIO_TREE_VERSION = "ai-scenario-tree-v1"
AI_CONTEXT_ORDERFLOW_ADAPTER_VERSION = "ai-context-orderflow-adapter-v1"

SUPPORTED_INSTRUMENTS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP")
ORDERFLOW_RESOLUTIONS = ("1m", "5m", "15m", "1H", "4H", "1D")

SUPPORTED_TIMEFRAMES = ("15m", "1H", "4H", "1D", "1W")
TIMEFRAME_SECONDS = {"15m": 900, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
MAX_BARS = {"15m": 512, "1H": 512, "4H": 512, "1D": 1500, "1W": 214}
