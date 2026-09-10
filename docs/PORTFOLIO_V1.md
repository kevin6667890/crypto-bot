# Portfolio v1 handoff

**Project state:** release candidate awaiting final CI and release publication.

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

The historical production backend is `d1d02dc`; it is an ancestor of the v1
candidate, not the same runtime revision. The production frontend recovery
commit is `e30ec590`, with deployment pin `5e41a01`. Both are preserved in Git
history and the reconciled source build reproduces the deployed entry asset
name `index-Bgnf_8Kd.js`. The v1 tag is therefore a newer, reproducible source
baseline that represents validated production behavior; it must not be claimed
that the running backend image is the exact v1 tag until that is separately
deployed.

Do not create `v1.0.0` until branch CI and final-main validation pass.
