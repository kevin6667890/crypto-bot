# AI-4 process ownership incident note

## Summary

During the previous AI-4 verification, six externally owned bounded-gate processes were incorrectly treated as leftover test processes from that verification and were stopped. Their supervisor subsequently restarted the work. This was not a production incident, but it crossed the ownership boundary of an unrelated long-running task and therefore requires an explicit corrective rule.

## What went wrong

Process ownership was inferred from broad process characteristics such as executable type, workload name, and resource usage. Those signals do not establish who created a process. The verification did not retain enough launch metadata to distinguish its own children from externally supervised workers before acting.

Six processes were stopped. The external supervisor restored the bounded-gate task afterward. No confirmed data or task corruption has been identified from the information available to this review; that does not make the intervention harmless. Task integrity must be judged from the owning task's checkpoints and completion evidence, not from the fact that a supervisor restarted it.

## Required ownership proof

A process may be stopped by an AI-4 verification only when all of the following are recorded and verified:

1. Its PID was recorded by the command that launched it in the current verification.
2. Its creation time is later than the current verification start time.
3. Its command line names the current verification worktree or temporary directory.
4. Its parent PID belongs to the current verification shell.
5. Its working directory belongs to the current verification worktree.
6. It is not managed by an external supervisor.

If any item is unknown or false, the process is externally owned for the purpose of this verification and must not be stopped.

## Operational boundary

Broad or fuzzy process termination is prohibited, including termination by executable name, Python/pytest/worker keyword, workload keyword, or CPU usage. Resource consumption is never ownership proof.

Before running a full test suite, the verifier must perform a read-only resource-window check. Externally owned bounded gates, research jobs, backfills, builds, and test suites may be inspected for identity and status, but must not be paused, reprioritized, reconfigured, signalled, or terminated. If they still create material contention, AI-4 verification waits for a clean resource window.

The owner of an external task is responsible for its lifecycle and integrity assessment. AI-4 verification is responsible only for processes it launches with complete ownership metadata and for avoiding interference with all other workloads.
