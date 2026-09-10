# V1 source and production reconciliation

## Identity policy

| Identity | Evidence | Result |
| --- | --- | --- |
| Historical production backend | `/api/health` reports `d1d02dc28c67` | Traceable Git commit; ancestor of release candidate |
| Historical production frontend | public entry asset `index-Bgnf_8Kd.js` | Recovered source `e30ec590`; pin `5e41a01` |
| Final v1 source | `release/portfolio-v1-final` | Newer reconciled source baseline |

`d1d02dc` is an ancestor of the candidate. The candidate is ahead by 72 commits
at initial reconciliation, with no commits behind it. Final source and the
historical runtime are intentionally not asserted to be the same commit.

## Behavior matrix

| Core flow | Production evidence | Candidate evidence | Status |
| --- | --- | --- | --- |
| Tracking V2 baseline | recovered `e30ec590` fallback avoids fabricated zero count | `baseline.ts` + unit/browser fixtures | MATCH |
| What Changed | public route/API smoke | fixture browser flow | MATCH |
| Advanced canonical source | `e30ec590` changes `fetchEthSnapshot` to canonical API | source review + browser mount | MATCH |
| Prediction Markets | public route/API health; recovered bounded async resource change | READY / EMPTY / ERROR fixture smoke | MATCH |
| Navigation hierarchy | recovered `b48ea59` and deployment pin | navigation unit tests + browser navigation | MATCH |
| Thesis Capability V2 | public capabilities endpoint | V2 fixture browser tests | MATCH |
| Polymarket | public health reports fresh collector and safe disk guard | source/API checks | MATCH |
| WAL guard | production operational incident recovery | candidate `000ba9c` and regression test | REPRESENTED_BY_V1 |

No core portfolio flow remains semantically unknown. The exact historical
frontend recovery is preserved rather than reconstructed from minified output.
