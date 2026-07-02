# Governed Worktree Candidates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task by task, `test-driven-development` for every behavior change, and `verification-before-completion` before release claims.

**Goal:** Add host-managed, no-checkout Git worktree leases in which a bounded implementation Subagent can make CAS-protected text edits, then persist an independently verified candidate that the parent may separately preview, approve, adopt, or discard.

**Architecture:** A new `mini_code_agent.worktrees` package owns immutable policy models, a byte-safe fixed-argv Git adapter, secure state storage, index-based materialization, mutation-ledger capture, candidate snapshotting, exact cleanup, and rollback-aware adoption. The child never receives Git, shell, MCP, Skills, Hooks, adoption, or delegation capabilities. Parent-repository mutation remains a separate high-risk Tool operation and is never performed by child completion.

**Tech Stack:** Python 3.12/3.13, Pydantic v2, `asyncio`, `subprocess` without a shell, SHA-256 canonical manifests, existing `AgentRuntime`, `WorkspaceBoundary`, Tool/Policy interfaces, pytest, Ruff, mypy, GitHub Actions.

---

## Non-Negotiable Invariants

- Model input supplies only an implementation task and reason. Lease IDs, candidate IDs, paths, repository identity, base SHA, profiles, limits, and Git argv are host-controlled.
- A lease starts from a clean, non-bare repository at an exact full HEAD and uses `git worktree add --detach --no-checkout --lock`.
- Materialization reads the parent index and Git object database. It never copies the parent working tree, ignored files, untracked files, credentials, caches, or virtual environments.
- The child can only read/search, perform CAS-protected Write/Edit operations, and optionally invoke one fixed governed test Tool.
- Candidate readiness requires an independent filesystem scan whose changed set and hash chains exactly match the host mutation ledger.
- Child completion persists evidence but does not mutate the parent checkout. Adoption and discard are separate high-risk parent Tools.
- Adoption preflights every path before the first parent write, revalidates immediately before applying, and rolls back in reverse order after partial I/O failure.
- Unsafe or ambiguous state fails closed. Dirty unsnapshotted leases are retained for diagnosis rather than force-removed.

## Task 1: Define Immutable Worktree Contracts

**Files:**
- Create: `src/mini_code_agent/worktrees/__init__.py`
- Create: `src/mini_code_agent/worktrees/models.py`
- Modify: `src/mini_code_agent/subagents/models.py`
- Test: `tests/unit/worktrees/test_models.py`
- Test: `tests/unit/subagents/test_models.py`

- [x] Write failing tests for hard ceilings, absolute/existing path requirements, literal allowed-prefix normalization, exact implementation profile mode, canonical identifiers, immutable records, and invalid limit relationships.
- [x] Extend `SubagentProfile.mode` to support `implementation` without weakening analysis-profile validation.
- [x] Implement `WorktreeLimits`, `WorktreeProfile`, repository/base/index records, lease states, ledger records, candidate states, candidate file records, and public error codes.
- [x] Ensure canonical manifests exclude mutable state fields and reject duplicate/case-colliding paths.
- [x] Run:

```powershell
py -m uv run --no-sync pytest tests/unit/worktrees/test_models.py tests/unit/subagents/test_models.py -q
py -m uv run --no-sync ruff check src/mini_code_agent/worktrees src/mini_code_agent/subagents/models.py tests/unit/worktrees
py -m uv run --no-sync pyright
```

- [x] Commit: `feat: define governed worktree contracts`

## Task 2: Build the Fixed Git and Secure State Foundations

**Files:**
- Create: `src/mini_code_agent/worktrees/git.py`
- Create: `src/mini_code_agent/worktrees/state.py`
- Test: `tests/unit/worktrees/test_git.py`
- Test: `tests/unit/worktrees/test_state.py`

- [x] Write failing tests for executable revalidation, no shell invocation, fixed global options/config, bounded byte output, timeout cleanup, stable NUL index parsing, stage/mode rejection, batch blob validation, and hostile filenames.
- [x] Write failing tests for an absolute state root outside the repository, secure ancestor checks, link/reparse rejection, POSIX permission checks, opaque state IDs, atomic writes/renames, canonical JSON, and immutable blob hashing.
- [x] Implement a narrow Git adapter with allowlisted operations only: repository discovery, status/HEAD/index inspection, `cat-file --batch`, worktree add/lock/unlock/remove/prune/list.
- [x] Implement the state layout:

```text
leases/
candidates/building/
candidates/ready/
candidates/applying/
candidates/applied/
candidates/rejected/
candidates/uncertain/
hooks-empty/
```

- [x] Run focused tests, Ruff, and Pyright.
- [x] Commit: `feat: add secure worktree git and state foundations`

## Task 3: Create No-Checkout Leases and Materialize the Index

**Files:**
- Create: `src/mini_code_agent/worktrees/materialize.py`
- Create: `src/mini_code_agent/worktrees/manager.py`
- Test: `tests/unit/worktrees/test_materialize.py`
- Test: `tests/unit/worktrees/test_manager_leases.py`
- Test: `tests/integration/test_worktree_materialization.py`

- [x] Write failing tests that verify exact top-level/non-bare identity, full clean status including untracked files, exact base SHA, active-lease limits, and host-generated paths.
- [x] Write failing tests that assert the mandatory `--no-checkout`, detached/locked worktree argv and empty Hooks directory.
- [x] Write failing tests for tracked file/byte/depth/path limits, 100644/100755-only entries, sparse/unmerged/gitlink/symlink/special rejection, duplicate/case collision rejection, and truncated Git output.
- [x] Materialize regular files exclusively from index blob bytes, preserving only executable/non-executable regular modes.
- [x] Build a fresh `WorkspaceBoundary` rooted at the lease and persist the immutable base manifest before child execution.
- [x] Prove with a real Git repository that ignored, untracked, `.env`, cache, and virtual-environment files are absent.
- [x] Commit: `feat: materialize governed worktree leases`

## Task 4: Capture a Trusted Mutation Ledger

**Files:**
- Create: `src/mini_code_agent/worktrees/ledger.py`
- Create: `src/mini_code_agent/worktrees/tools.py`
- Modify: `src/mini_code_agent/subagents/tools.py`
- Test: `tests/unit/worktrees/test_ledger.py`
- Test: `tests/unit/worktrees/test_child_tools.py`

- [x] Write failing tests for implementation child capability validation: Read/Search plus Write/Edit and optional fixed test Tool only.
- [x] Prove rejection of arbitrary process/Git/MCP/Skills/Hooks/adoption/delegation Tools and all undeclared Tool names.
- [x] Wrap successful Write/Edit execution so ledger entries are derived from parsed `MutationResult`, never model arguments.
- [x] Enforce ordered contiguous before/after hash chains, exact path normalization, call-ID uniqueness, bounded records, and no ledger entry on failed or preview-only mutations.
- [x] Preserve existing analysis Subagent behavior and read-only validator tests.
- [x] Commit: `feat: record implementation mutation evidence`

## Task 5: Snapshot and Persist Verified Candidates

**Files:**
- Create: `src/mini_code_agent/worktrees/snapshot.py`
- Extend: `src/mini_code_agent/worktrees/state.py`
- Extend: `src/mini_code_agent/worktrees/manager.py`
- Test: `tests/unit/worktrees/test_snapshot.py`
- Test: `tests/unit/worktrees/test_candidate_store.py`

- [x] Write failing tests for an independent complete scan, link/reparse/special rejection, `.git` rejection, case collisions, path/file/byte/diff limits, and allowed-prefix enforcement.
- [x] Compare every materialized base path by raw SHA-256 and detect additions, modifications, deletions, binary/invalid UTF-8 changes, and mode changes.
- [x] Require the filesystem changed set to equal the ledger set and every final hash to equal the last ledger hash.
- [x] Generate deterministic bounded unified diffs and store after-content blobs separately from the canonical immutable manifest.
- [x] Persist valid candidates atomically from `building` to `ready`; return no candidate when there are no changes.
- [x] Persist extra regular mutations as a forensic `rejected` manifest before cleanup; retain locked `cleanup_required` leases for severe unsafe or budget failures.
- [x] Commit: `feat: persist verified worktree candidates`

## Task 6: Implement Exact Cleanup and Cancellation Finalization

**Files:**
- Extend: `src/mini_code_agent/worktrees/manager.py`
- Test: `tests/unit/worktrees/test_cleanup.py`
- Test: `tests/unit/worktrees/test_cancellation.py`
- Test: `tests/integration/test_worktree_cleanup.py`

- [x] Write failing tests for exact lease/repository/admin identity checks before unlock/remove, bounded prune, and postcondition verification.
- [x] Permit manager-owned removal only after candidate persistence or a verified clean tree.
- [x] Prove dirty unsnapshotted and path-ambiguous leases are retained and marked `cleanup_required`.
- [x] Add bounded shielded snapshot/cleanup finalization on child cancellation, then re-raise `CancelledError`.
- [x] Record actionable diagnostics when cleanup exceeds its budget.
- [x] Commit: `feat: finalize worktree leases safely`

## Task 7: Expose Bounded Implementation Delegation

**Files:**
- Create: `src/mini_code_agent/worktrees/runner.py`
- Extend: `src/mini_code_agent/worktrees/tools.py`
- Modify: `src/mini_code_agent/app/composition.py`
- Modify: `src/mini_code_agent/cli.py`
- Test: `tests/unit/worktrees/test_runner.py`
- Test: `tests/unit/worktrees/test_delegate_tool.py`
- Test: `tests/integration/test_governed_worktree_agent.py`

- [x] Define a parent `delegate_implementation` Tool whose model-visible input is only `{task, reason}` and whose output is bounded candidate metadata/evidence.
- [x] Compose the exact local implementation profile and host factories before any Provider I/O.
- [x] Run one implementation child per Tool call inside its lease, project bounded child evidence, snapshot independently, and finalize cleanup.
- [x] Ensure child timeout, failure, cancellation, no-change completion, rejected snapshot, and candidate-ready paths have deterministic public results.
- [x] Add public composition exports and smoke coverage without enabling the feature unless a host supplies a `WorktreeProfile`.
- [x] Prove end-to-end with a scripted Provider that child completion leaves the parent checkout unchanged.
- [x] Commit: `feat: delegate bounded implementation work`

## Task 8: Adopt, Roll Back, Recover, and Discard Candidates

**Files:**
- Create: `src/mini_code_agent/worktrees/adoption.py`
- Extend: `src/mini_code_agent/worktrees/tools.py`
- Test: `tests/unit/worktrees/test_adoption.py`
- Test: `tests/unit/worktrees/test_discard.py`
- Test: `tests/integration/test_candidate_adoption.py`

- [x] Define separate high-risk WRITE Tools `adopt_subagent_candidate` and `discard_subagent_candidate`.
- [x] Preview adoption by verifying manifest/blob hashes and returning bounded repo/base/path/byte/diff resources without parent mutation.
- [x] On execute, atomically claim `ready -> applying`, require exact clean repo/HEAD base, preflight every path, stage same-directory temporary files, and revalidate all paths immediately before the first replacement.
- [x] Apply in canonical order, verify the exact final set/hashes, and move `applying -> applied`; leave changes unstaged and uncommitted.
- [x] On preflight conflict, perform zero writes and return to `ready`.
- [x] On partial I/O failure, roll back in reverse order and persist `rolled_back` evidence or `uncertain` when rollback cannot be proven.
- [x] Recover interrupted `applying` candidates: all-before to `ready`, all-after to `applied`, mixed to `uncertain`.
- [x] Permit discard only for a verified `ready` candidate through an atomic claim; reject applied/applying/uncertain candidates.
- [x] Commit: `feat: adopt and discard verified candidates`

## Task 9: Run Adversarial and Cross-Version Quality Gates

**Files:**
- Create: `tests/adversarial/test_worktree_safety.py`
- Extend: `tests/integration/test_governed_worktree_agent.py`
- Extend: `tests/integration/test_candidate_adoption.py`
- Modify: `.github/workflows/ci.yml` only if a required platform gate is missing

- [x] Cover hostile filenames, Unicode/case aliases, links/reparse points, path swaps, parent HEAD/status races, stale CAS hashes, duplicate IDs, output truncation, killed Git processes, lease exhaustion, candidate tampering, blob tampering, rollback failure, and cancellation races.
- [x] Run focused real-Git integration tests on Python 3.12 and 3.13.
- [x] Run all unit, integration, adversarial, type, lint, format, package, and coverage gates.
- [x] Inspect coverage for all new trust-boundary modules and add missing branch tests.
- [x] Commit: `test: harden governed worktree candidates`

## Task 10: Document, Package, Publish, and Record Evidence

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `SECURITY.md`
- Create: `docs/architecture/governed-worktree-candidates.md`
- Create: `docs/adr/0015-governed-worktree-candidates.md`
- Modify: `docs/architecture/threat-model.md`
- Modify: `docs/learning/knowledge-map.md`
- Modify: `docs/learning/progress.md`
- Modify: `docs/resume/project-profile.md`
- Modify: `pyproject.toml`
- Create: `tests/worktree_artifact_test.py`
- Modify: release-version tests and `tests/smoke_test.py`
- Modify: `uv.lock`

- [x] Explain the trust boundaries, state machine, limits, failure modes, operator recovery, and why child completion is separated from parent adoption.
- [x] Add prerequisite knowledge and implementation notes for Git index/object storage, worktrees, CAS writes, manifests, rollback, TOCTOU defenses, cancellation shielding, and fail-closed cleanup.
- [x] Add resume-ready project description, stack, measurable highlights, and for each highlight: why it exists, the technical mechanism, the delivered function, the optimization, and the problem solved.
- [ ] Bump to `0.16.0a0`, build twice with a fixed epoch, compare artifacts byte-for-byte, and inspect members.
- [ ] Smoke-test wheel and sdist in isolated Python 3.12/3.13 environments, including real delegation and adoption flows.
- [ ] Push `codex/m6b-worktree-candidates`, open a PR, wait for all CI jobs, merge, verify merged-main CI, create annotated `v0.16.0-alpha.0`, publish a non-draft prerelease with verified artifacts, and update release evidence.
- [ ] Commit: `docs: prepare 0.16 worktree candidate alpha`

## Final Verification Commands

```powershell
py -m uv run --no-sync ruff format --check .
py -m uv run --no-sync ruff check .
py -m uv run --no-sync pyright
py -m uv run --no-sync pytest -q
py -m uv build
git status --short
```

Run the full test suite and artifact smoke matrix under both supported Python versions. Do not claim completion from focused tests alone.

## Plan Self-Review

- The plan preserves the accepted two-phase security model: child work creates evidence; a separate approved Tool mutates the parent.
- Git interaction is intentionally narrower than the existing general command abstraction because index blobs and hostile paths require byte-safe, NUL-delimited handling.
- Candidate verification does not trust the child transcript or ledger alone; it reconciles the complete materialized tree, immutable base manifest, and ordered mutation hash chains.
- Adoption is process-serialized and rollback-aware, not described as crash-atomic. Recovery explicitly handles interrupted `applying` state.
- The first release supports one implementation child per delegation Tool call. The manager still enforces the accepted global active-lease ceiling, allowing independent parent calls without introducing multi-child adoption ambiguity.
- The release task includes code, tests, learning material, resume material, reproducible artifacts, CI, tag, release, and evidence rather than treating documentation as a later add-on.
