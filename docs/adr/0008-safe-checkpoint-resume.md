# ADR 0008: Stable Checkpoints and Fail-Closed Resume

## Status

Accepted for M3c.

## Context

M3b records lifecycle facts but deliberately excludes conversation state. Replaying from an old
prompt after a crash can repeat a completed write. Treating `ToolCompleted` as a Checkpoint is
also unsafe because its result may not have been included in durable model input.

## Decision

Persist complete typed state only at stable model-input boundaries. Bind each snapshot to a
`CheckpointSaved` event in the same SQLite transaction.

Before Resume, verify Trace integrity, exact Tool/Workspace compatibility, and every event after
the selected Checkpoint. Block all uncheckpointed write/execute/network actions. Require explicit
policy for possible Provider and read-only retries.

Consume the Checkpoint, interrupt the source Run, and start a new Run atomically. Reanalyze inside
the claim API rather than trusting a serializable plan supplied by a caller.

## Consequences

- A clean Provider interruption or read-only interruption can resume under explicit retry policy.
- An uncheckpointed side effect requires human/external reconciliation; automatic Resume stops.
- Concurrent claim has one winner.
- Full transcript state is durable and therefore increases confidentiality exposure.
- Workspace scans add bounded I/O at stable boundaries.
- SQLite coordinates local processes only; no distributed exactly-once guarantee is created.

## Alternatives Rejected

- **Replay from the last user prompt:** can duplicate writes already performed.
- **Checkpoint after each ToolStarted/Completed:** intermediate transcripts are not valid Provider
  input and can still omit an observed result.
- **Trust `ResumePlan`:** callers could construct one and bypass risk analysis.
- **Automatically accept changed Workspace:** resumes against state the transcript did not
  observe.
- **Claim the same Run ID:** obscures the process boundary and permits ambiguous concurrent
  ownership.

