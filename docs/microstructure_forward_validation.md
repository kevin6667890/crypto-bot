# Forward-only microstructure validation protocol

The active research epoch is the hash of the schema, public-source and feature
versions. Any change to one of those versions starts a new epoch.

1. The initial research window activates after 30 complete chronological days.
2. A later validation window activates after 60 complete chronological days.
3. The untouched final forward OOT window activates after 90 complete
   chronological days and may not be used for feature selection.

Missing days delay all activation dates. A timestamp belongs to exactly one
window; it cannot be recycled into a later window. The current short sample is
blocked from automatic discovery, formal ranking, promotion, robustness,
ablation, and holdout/OOT claims. Multiple regimes remain required before any
formal research claim, regardless of elapsed days.

