# Phase 6F deterministic factor AutoResearch

Phase 6F researches bounded causal factor expressions. It does not generate
strategies, entries, exits, stops, targets, position sizing, orders, or
deployable signals.

The immutable factor AST has explicit terminal, unary, binary, and causal
regime-conditional nodes. Canonical algebraic normalization, commutative
operand ordering, a versioned identity, structural validation, behavior
deduplication, and score/portfolio-return correlation clustering bound the
search. Generation is deterministic at semantic seed `20260727`; low
complexity is enumerated and higher complexity is selected by a deterministic
quality-diversity beam.

Every raw attempt is written to a resumable SQLite ledger, including structural
failures, semantic and behavior duplicates, insufficient samples, adverse
relationships, validation reversals, locked-verification failures, and retained
diagnostics. The dataset snapshot hash and the actual Phase 6E per-feature by
instrument eligibility response are immutable run inputs.

Formal inputs are settled funding for BTC, ETH, and SOL; BTC basis; and causal
mark/index price context. CVD, open interest, predicted funding, liquidations,
and ETH/SOL basis are future terminals only and cannot generate formal factors.

Usable overlap is split 60/20/20 into discovery, selection validation, and a
once-opened locked verification segment. A 24-hour purge and embargo is applied
at both boundaries, and no label may cross a boundary. Locked verification is
not a completed forward OOT claim.

Diagnostic portfolios use causal standardized factor ranks, fixed unit
notional, upper/lower quantile exposure, zero middle exposure, no stops or
targets, and non-overlapping forward returns at each horizon. They exist only
to measure turnover, cost drag, Sharpe, moments, drawdown, PSR, and DSR.
Benjamini-Hochberg uses the complete applicable factor/horizon family;
Bonferroni is comparative. DSR reports raw ledger trials and correlation-cluster
effective trials. PBO is unavailable when there are too few independent
chronological blocks.

Generated ledgers, reports, libraries, checkpoints, and database snapshots are
local research artifacts and must not be committed.
