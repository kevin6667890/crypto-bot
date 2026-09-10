# Crypto-Bot interview handoff

## 30-second pitch

Crypto-Bot is a production-deployed crypto research system, not a trading bot.
It lets someone test a market hypothesis on immutable historical data, inspect
the event evidence, save the exact baseline, and later see only material
changes in current confirmed evidence. I focused on reproducibility, explicit
data quality, and operational reliability rather than price prediction.

## Two-minute explanation

A user starts with a bounded idea. The deterministic thesis engine validates
it against a capability registry and evaluates independent historical events
without future-bar leakage. The result includes dataset identity, coverage,
event evidence, and deterministic statistics. When a thesis is tracked, its
historical baseline is immutable. A background worker evaluates the same
definition on current confirmed data and records only material state, quality,
or identity changes. React renders API evidence; it never recomputes research
statistics. AI is optional and constrained to a validated draft or audited
explanation.

## Engineering story: SQLite WAL incident

Under sustained ingestion, a custom checkpoint policy skipped checkpoints when
the writer queue was non-empty. That was safe for small bursts but could starve
checkpointing indefinitely and let the WAL grow. The permanent fix preserves
the low-I/O behavior for small WALs, but forces a bounded checkpoint above a
hard guard even when the queue is busy. The regression suite covers the queue
condition; operations expose WAL, checkpoint, disk, and collector freshness.

## Trade-offs and scale

SQLite is a good fit for a single-node, durable research product: simple
backups, transactional tracking state, and low operational overhead. At 100x
write/read load, I would separate ingestion from query storage, use a managed
database or append-oriented store for raw flow, and keep immutable research
datasets versioned separately. The product semantics—confirmed data,
no-lookahead, immutable baselines, and explicit unknown states—would remain.
