# M4a Hardened Read-only Git Evidence Implementation Plan

> Execute with TDD in an isolated worktree and commit each task independently.

**Goal:** Add bounded, machine-parsed Git status and diff evidence without mutating repository
state or invoking project-configured execution extensions.

**Target:** `v0.10.0-alpha.0`

## Task 1: Git Contracts and Errors

**Status:** Complete

- Add immutable limits, status/diff models, stable error codes, canonical fingerprints.
- Validate branch metadata, XY fields, paths, counts, and result bounds.
- Write N-1/N/N+1 tests first.

## Task 2: Porcelain-v2 Parser

**Status:** Complete

- Parse branch headers and `1`, `2`, `u`, `?` NUL records.
- Preserve unusual valid path text while rejecting incomplete/unknown records.
- Enforce entry limit and deterministic canonical hash.
- Test real rename/conflict fixtures plus synthetic malformed boundaries.

## Task 3: Hardened Git Client

**Status:** Pending

- Reuse argv-only `CommandRunner` with Git-specific limits.
- Verify exact non-bare repository top-level.
- Apply no-pager/no-optional-locks/fsmonitor/external-diff/textconv/submodule hardening.
- Normalize timeout, overflow, startup, exit, parse, and repository failures.
- Test argv exactly and prove repository bytes/status do not mutate.

## Task 4: Read-only Tools and Agent Integration

**Status:** Pending

- Add `git_status` and `git_diff` Tool definitions and canonical JSON results.
- Export through the tools package and compose through Tool Registry.
- Run a deterministic Agent through status and diff calls.
- Verify invalid arguments, result limits, duplicate IDs, and no shell invocation.

## Task 5: Documentation and `v0.10.0-alpha.0`

**Status:** Pending

- Add architecture/ADR/threat notes and M4a learning exercises.
- Update resume Git row with why, implementation, function, solved problem, limits, and evidence.
- Add version-first tests, bump package/lock, README, and Changelog.
- Run lock, dual-Python full tests, coverage, Ruff, strict Pyright, Bandit, pip-audit, hashed build,
  and four exact-artifact smoke tests.
- Fast-forward merge, verify merged suite/CLI, tag, and clean the owned worktree.
