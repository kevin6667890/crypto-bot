# Phase 6C official OKX public-source audit

Audit date: 2026-07-24. Only the official [OKX API guide](https://www.okx.com/docs-v5/en/)
and official OKX API changelog were used.

| Lane | Official source/channel | Type | Native frequency | Instruments | Earliest recoverable / limits | Rate limit | Repository support | Status |
|---|---|---|---|---|---|---|---|---|
| Public trades | `GET /api/v5/market/history-trades`; WS `trades-all` on `/ws/v5/business` | REST + WS | Per trade; WS pushes each trade | Spot, margin, swaps, futures and supported live instruments | REST states last 3 months, 100 rows/page, `after` pagination. `side` is the genuine taker/aggressor direction. | REST 20 requests/2 seconds/IP | Existing spot WS flow; Phase 6C adds isolated swap history + WS with contract-value notional | LIMITED_HISTORY |
| Open interest | `GET /api/v5/public/open-interest`; WS `open-interest` | REST + WS | Current snapshot / event push | SWAP, FUTURES, OPTION | No official instrument-level historical endpoint. Rubik currency-level statistics are not substituted for instrument OI. Earliest recovery is the first genuine local observation. | Public endpoint limits are IP-based; collector polls conservatively | Existing genuine REST snapshots migrate; Phase 6C continues swap collection | FORWARD_ONLY |
| Settled funding | `GET /api/v5/public/funding-rate-history` | REST | Settlement schedule, normally 8h but exchange can vary | SWAP | Official paginated settlement history; maximum 400/page | 10 requests/2 seconds/IP | New | LIMITED_HISTORY |
| Current/predicted funding | `GET /api/v5/public/funding-rate` | REST | Current funding period | SWAP | Current/provisional only. `fundingRate` and next funding time are stored apart from settlement history. | 20 requests/2 seconds/IP | New | FORWARD_ONLY |
| Mark price | `GET /api/v5/public/mark-price`; `GET /api/v5/market/history-mark-price-candles` | REST | Snapshot + 1m historical candles | SWAP, FUTURES, OPTION where applicable | 100 candles/page; actual returned boundary is recorded | 10 requests/2 seconds/IP for history | New | LIMITED_HISTORY |
| Index price | `GET /api/v5/market/index-tickers`; `GET /api/v5/market/history-index-candles` | REST | Snapshot + 1m historical candles | Underlying indices such as BTC-USDT | 100 candles/page; actual returned boundary is recorded | 20 requests/2 seconds/IP for history | New | LIMITED_HISTORY |
| Basis | Causal mark/index as-of join | Derived only from the two official sources | 1m and durable rollups | Supported linear USDT swaps | Exists only when a confirmed mark and an index at or before it both exist | Source endpoint limits apply | New | SUPPORTED |
| Liquidations | WS `liquidation-orders` on `/ws/v5/public` | WS | At most one update/second | MARGIN, SWAP, FUTURES | Official REST history was retired at the end of April 2023. OKX documents coverage limits, so the stream is not treated as a complete ledger. | WS connection/subscription limits | New, genuine events only | FORWARD_ONLY |

Implementation constraints:

- No candle direction is used to infer CVD.
- Swap trade notional uses official instrument `ctVal`: `price × contracts × ctVal`.
- OI is never inferred from volume or candles.
- Funding settlement and current-period estimates use separate tables.
- Missing intervals are not inserted.
- Liquidation history is not reconstructed.
- Every bounded backfill persists its cursor, returned range, pages, retries and honest completeness status.

Official references:

- [OKX API guide](https://www.okx.com/docs-v5/en/)
- [OKX API changelog: historical liquidation REST retirement](https://www.okx.com/docs-v5/log_en/)

