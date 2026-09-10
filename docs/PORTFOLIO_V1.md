# Portfolio v1 handoff

**Project state:** `PORTFOLIO_V1_COMPLETE` is pending source reconciliation.

Crypto-Bot is an evidence-driven crypto research system. It turns a bounded
market hypothesis into a reproducible historical test, preserves that evidence,
then compares it with current confirmed evidence. It does not execute live
orders or present statistical evidence as a prediction.

## Boundaries

- Deterministic code owns feature evaluation, event membership, statistics,
  data identities, and tracked-thesis state.
- AI is optional: it may map language into a validated draft or explain an
  audited fact set. It cannot invent, alter, or calculate research statistics.
- Historical evidence is immutable and identity-checked. Current evidence uses
  confirmed candles. Missing data is explicit and never silently becomes false.
- Persistent SQLite stores are operational state, not source artifacts.

## Production shape

Docker Compose runs the React frontend, Python API/background workers, and
persistent SQLite stores behind the public edge. Health/readiness gates expose
bounded operational status. The collector uses a forced WAL checkpoint guard:
a busy writer queue may defer small checkpoints but may not defer them
indefinitely once the WAL passes its guard threshold.

## Maintenance policy

After `v1.0.0`, feature work is frozen. Allowed changes are production bug
fixes, security/dependency updates, data-integrity fixes, reliability fixes,
and portfolio/documentation corrections. New indicators, assets, trading
execution, accounts, product redesigns, and unbounded AI expansion are not
planned without a concrete user, reliability, or interview-driven reason.

## Analytics and links

The public site currently includes Cloudflare Web Analytics' beacon. It is
used only for aggregate page/referrer measurement; the application must not
send thesis text, research payloads, wallets, or other user-entered content as
analytics events. Standard UTM query parameters remain compatible with the
site and do not change research behavior.

Recommended links:

- Resume: `https://bitcoinbot.uk/?utm_source=resume&utm_medium=portfolio&utm_campaign=internship_2027`
- Repository: `https://bitcoinbot.uk/?utm_source=github&utm_medium=repository`

## Release gate

Do not create `v1.0.0` until the production backend and deployed frontend
artifact each resolve to reviewed commits in the public source history, the
release branch is tested, and the final production browser smoke passes.
