# M3c Durable Checkpoint and Safe Resume Implementation Plan

> Execute with TDD in an isolated worktree. Commit each completed task independently.

**Goal:** Persist stable Agent state and safely resume interrupted Sessions without automatically
replaying uncheckpointed side effects.

**Target:** `v0.9.0-alpha.0`

## Task 1: Checkpoint Contracts and Lifecycle Events

**Status:** Complete

- Add `CheckpointSaved` and `StopReason.INTERRUPTED`.
- Add immutable Checkpoint limits, snapshot, record, compatibility, policy, plan, and resume-state
  models.
- Validate stable transcript grammar, cumulative counters, identifiers, timestamps, hashes, and
  bounded collections.
- Add canonical Tool contract fingerprinting.
- Write failing boundary tests first.

## Task 2: Bounded Workspace Fingerprint

**Status:** Complete

- Add deterministic regular-file manifest hashing.
- Include relative path, mode-independent file bytes, exclusions, and scanner configuration.
- Reject symlinks, non-regular files, traversal, file-count overflow, byte overflow, and races.
- Test empty, changed, renamed, excluded, limit, and symlink workspaces.

## Task 3: SQLite Schema v2 Migration

**Status:** Complete

- Separate database schema version 2 from Trace envelope version 1.
- Add transactional v1-to-v2 migration and fresh-v2 creation.
- Add `checkpoints` table, status constraints, foreign keys, and deterministic indexes.
- Test migration preservation, rollback, reopen, malformed schema, and unsupported future version.

## Task 4: Transactional Checkpoint Save and Query

**Status:** Pending

- Refactor Trace append internals for reuse inside an existing transaction.
- Save `CheckpointSaved`, Trace head, and canonical snapshot row atomically.
- Add exact idempotency and conflicting-ID rejection.
- Add bounded get/list/latest APIs and payload/hash verification.
- Test event/row rollback, limits, lock timeout, corruption, and error redaction.

## Task 5: Resume Eligibility Analysis

**Status:** Pending

- Verify full Trace and Checkpoint binding.
- Validate active source Run, availability, Tool fingerprint, and Workspace fingerprint.
- Scan all post-checkpoint events in pages.
- Block every uncheckpointed write/execute/network Tool.
- Require explicit policy for model or read-only replay.
- Return a bounded immutable `ResumePlan`; never mutate during analysis.

## Task 6: Atomic Resume Claim

**Status:** Pending

- Revalidate the plan under `BEGIN IMMEDIATE`.
- Atomically append interrupted source `RunStopped`, append resumed `RunStarted`, and consume the
  Checkpoint.
- Return restored state only after commit.
- Test stale plans, duplicate/concurrent claims, rollback, and new Run projection.

## Task 7: Runtime Checkpoint and Resume Integration

**Status:** Pending

- Save initial and post-ToolResult stable Checkpoints.
- Treat save failure as required persistence failure before the next Provider call.
- Add `resume` entry point using claimed state without duplicate `RunStarted`.
- Continue at `turns + 1` with cumulative Tool/token limits and seen ToolCall IDs.
- Test cancellation, context failure, Provider failure, save failure, and no extra I/O.

## Task 8: End-to-End Fault Injection

**Status:** Pending

- Reopen and resume from a stable Checkpoint through final completion.
- Replay explicitly permitted model/read-only work.
- Crash after a real governed write and prove Resume blocks with no duplicate file mutation.
- Reject changed Workspace and changed Tool contract.
- Race two claims and prove exactly one succeeds.

## Task 9: Documentation and Release

**Status:** Pending

- Add architecture guide and ADR; update threat model and M3b non-claims.
- Expand learning map/progress with Java transaction and Flink checkpoint analogies plus exercises.
- Replace the resume placeholder only with measured M3c evidence and retain all non-claims.
- Add version-first tests, bump package to `0.9.0a0`, update lock, README, and Changelog.
- Run lock, dual-Python full tests, coverage, Ruff, strict Pyright, Bandit, pip-audit, hashed build,
  and four exact-artifact smoke tests.
- Fast-forward merge, verify merged suite/CLI, tag `v0.9.0-alpha.0`, and clean the owned worktree.
