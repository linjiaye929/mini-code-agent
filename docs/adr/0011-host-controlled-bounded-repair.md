# ADR 0011: Host-Controlled Bounded Repair

## Status

Accepted.

## Context

M4b can return structured Pytest diagnostics, but an ordinary Agent loop still lets the model
choose when to edit, when to test, and when to stop. Counting ToolCalls inside `AgentRuntime` would
couple every Agent task to Pytest semantics and would not establish a trustworthy baseline or
detect test-induced repository drift.

Automatic edits also increase the consequence of stale diagnostics, repeated failures, dirty user
work, out-of-scope changes, and persistence loss.

## Decision

Add a separate `RepairRuntime` above `AgentRuntime`.

- The host approves one bounded Repair session.
- Admission requires a clean repository and exact Git-tracked existing files.
- `RepairActionGuard` restricts Worker Tool previews to read-only operations and exact scoped
  writes; execute and network actions are denied.
- One `AgentRepairWorker` call performs one repair attempt.
- The host runs the same fixed Pytest targets at baseline and after each accepted patch.
- Git status/diff and Workspace identity checks validate every attempt and test run.
- Canonical failure hashes, attempt/time/patch/prompt budgets, and typed stop reasons bound the
  loop.
- SQLite schema v3 stores a separate hash-chained Repair lifecycle trace.
- Interrupted Repair sessions are not automatically resumed.

## Consequences

### Positive

- Ordinary Agent behavior remains provider-neutral and repair-agnostic.
- Tests, success criteria, budgets, and stopping are deterministic host decisions.
- Dirty user work and ignored/untracked edit targets fail before code execution.
- Scope denial occurs before approval and mutation.
- Repair lifecycle evidence survives process exit without persisting patches or diagnostics.

### Negative

- Callers must construct an explicitly scoped Worker executor and a separate Repair approval.
- Repair currently requires a clean repository and existing tracked files.
- No automatic rollback means failed attempts remain for human inspection.
- The coordinator adds another state machine and SQLite projection.
- Fixed Pytest execution still requires explicit trust in repository test code.

## Rejected Alternatives

### Add counters to `AgentRuntime`

Rejected because it mixes general ToolCall orchestration with Pytest-specific workflow semantics
and still lets the model control verification timing.

### Tool middleware only

Rejected as the complete solution because a middleware can restrict actions but cannot own
baseline tests, compare repository evidence, or terminate on repeated diagnostics.

### Let the model decide completion

Rejected because model text is not verification evidence. Only a complete passing host test
result terminates with `repaired`.

### Automatically reset failed changes

Rejected because reset/checkout/clean can destroy user work and introduce a new Git mutation
boundary. Worktree-based isolation remains a later milestone.
