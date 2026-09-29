# Market Data Expansion Plan

## Admission rule

No external series enters formal scoring until its source, instrument mapping,
UTC timestamp convention, history coverage, missing intervals and revision
behavior have been persisted and validated. Missing observations remain
`UNAVAILABLE`; they are never filled.

| Priority | Data | Candidate source | History / resolution risk | Integration decision |
|---:|---|---|---|---|
| 1 | Funding | OKX funding-rate history | Instrument and funding interval must be verified per swap; public endpoint history may be bounded. | Build read-only adapter plus coverage audit. |
| 2 | Basis / mark-index spread | OKX mark price + index price history | Must align confirmed bars and contract roll/instrument semantics. | Adapter after funding coverage passes. |
| 3 | OI | OKX OI; Binance OI history | Binance documented OI-history availability is limited; source continuity must be checked. | Do not use until durable coverage is demonstrated. |
| 4 | Liquidations | exchange or paid vendor | Public history may be incomplete/revised. | Evaluate provenance/cost before design. |
| 5 | Trades / true VPVA/CVD | exchange historical trade archive | Large storage and exact aggressor-side convention required. | Keep `TRUE_VPVA`/CVD unavailable pending an audited archive. |

## Quality contract

Every adapter emits source URL, retrieval time, source symbol, UTC interval,
expected/actual count, missing intervals, duplicate count, data fingerprint and
availability status. Engine features are enabled only for `COMPLETE` coverage.

## Source evidence

OKX documents historical funding, OI, mark/index, trades and order-book market
data, but endpoint existence is not proof of sufficient historical coverage.
[OKX API guide](https://app.okx.com/docs-v5/agent_en/)

Binance documents funding history and open-interest endpoints, while its OI
history documentation notes bounded availability; this reinforces the need for
an explicit coverage audit before use.
[Binance derivatives market-data documentation](https://developers.binance.com/docs/derivatives/coin-margined-futures/market-data/rest-api/Get-Funding-Info)
