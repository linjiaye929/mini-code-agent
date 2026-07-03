# Bounded Repair Loop

[English](bounded-repair-loop.md) | [简体中文](bounded-repair-loop.zh-CN.md)

## Purpose

M4c adds a host-controlled feedback loop above the ordinary Agent runtime:

```text
approve -> admit clean tracked scope -> baseline test
        -> one Agent repair attempt -> Git validation -> fixed Pytest verification
        -> success, typed stop, or another bounded attempt
```

The Agent proposes and executes one governed edit attempt. It does not control the number of
attempts, the test executable, the test arguments, the success decision, or the stop conditions.

## Composition

```python
from pathlib import Path

from mini_code_agent.git import GitClient
from mini_code_agent.repair import (
    AgentRepairWorker,
    RepairActionGuard,
    RepairRequest,
    RepairRuntime,
    RepairScope,
    StaticRepairApprovalHandler,
)
from mini_code_agent.testing import PytestProfile, PytestRunner
from mini_code_agent.workspace import WorkspaceBoundary

root = Path.cwd()
workspace = WorkspaceBoundary(root)
scope = RepairScope.create(workspace, ("src/calculator.py",))

# Build AgentRuntime with a GovernedToolExecutor whose guard is
# RepairActionGuard(scope), then adapt it:
worker = AgentRepairWorker(agent_runtime, scope_sha256=scope.sha256)

runtime = RepairRuntime(
    workspace,
    GitClient(root),
    PytestRunner(
        root,
        profile=PytestProfile(default_targets=("tests",)),
    ),
    worker,
    StaticRepairApprovalHandler(approved=True),
    journal=sqlite_store.repair_journal(),
)
result = await runtime.run(
    RepairRequest(
        user_prompt="Fix the failing arithmetic test.",
        test_targets=("tests",),
        editable_paths=("src/calculator.py",),
        reason="Run one bounded repair session.",
    )
)
```

The Worker executor still applies ordinary Tool schema, preview, Policy, and write approval. The
Repair action guard is an additional pre-policy restriction, not a replacement.

## Admission

The runtime rejects the session before Provider or Pytest work unless:

1. Repair approval succeeds;
2. Workspace, Git, and Pytest roots are identical;
3. the repository has no staged, unstaged, untracked, rename, conflict, or submodule changes;
4. every editable path resolves to an existing regular non-link Workspace file;
5. `git ls-files --error-unmatch -z -- :(top,literal)<path>` confirms every exact path is tracked;
6. the Worker scope fingerprint equals the coordinator scope fingerprint;
7. the required `RepairStarted` event is durably appended.

A clean Git status alone is insufficient: ignored files are absent from status and diff. The
literal tracked-path query closes that evidence gap.

## Action Scope

`RepairActionGuard` receives trusted `ActionPreview` values from `GovernedToolExecutor`:

- read-only actions are permitted to continue to Policy;
- writes require at least one resource and every resource must exactly equal an approved path;
- execute and network actions are denied;
- denial occurs before Policy approval and Tool execution.

The coordinator owns Pytest execution. A Repair Worker is instructed not to run tests or commands.

## Test Protocol

The same host-owned targets run at baseline and after every accepted patch. Success requires both:

- Pytest execution status `passed`;
- JUnit report status `complete`.

Exit 1 with a complete report and at least one failure/error diagnostic is repairable. Timeout,
output overflow, interruption, internal/usage error, no tests, unknown exit, or incomplete report
is an infrastructure stop.

The fixed Python prefix is `python -I -B -m pytest`. `-B` prevents bytecode cache writes that would
otherwise make an ordinary test run dirty the repository. Ambient plugin autoload and Pytest cache
remain disabled.

## Git Evidence

After each Worker attempt:

- the branch OID/head/upstream/ahead/behind metadata must remain unchanged;
- staged diff must remain empty;
- every status entry must be ordinary unstaged `M`, non-submodule, and in exact scope;
- the full unstaged diff must be non-empty, differ from the previous attempt, and fit the patch
  byte budget;
- editable paths are re-resolved through Workspace before test execution.

The runtime reads status and diff again after tests. Both fingerprints and the Workspace file
identities must match the pre-test evidence. A surviving test mutation stops the session.

## Failure Fingerprint and Budgets

The normalized failure SHA-256 includes process/report status, recomputed counts, and sorted
diagnostic outcome/name/class/file/line/message. It excludes stdout, stderr, duration, and detail
bodies because those commonly contain unstable formatting or paths.

Default independent limits are:

| Budget | Default | Hard maximum |
|---|---:|---:|
| Repair attempts | 3 | 10 |
| Elapsed session time | 900 seconds | 3600 seconds |
| Full unstaged patch | 256 KiB | 8 MiB |
| Same failure appearances | 2 | 5 |
| Worker prompt | 64 KiB characters | 256 KiB characters |

The baseline fingerprint counts as the first appearance. An unchanged failure after one attempt
therefore stops under the default threshold.

## Durable Repair Trace

SQLite schema v3 adds independent `repair_runs` and `repair_events` tables. Repair events use:

- UUID event IDs and exact-payload idempotency;
- per-Repair sequence allocation;
- canonical JSON;
- SHA-256 predecessor chains;
- one `BEGIN IMMEDIATE` transaction for event and projection changes;
- bounded event/query counts and busy timeout;
- projection checks against first/last lifecycle events.

Events contain IDs, statuses, counts, budgets, and hashes. They exclude prompts, patches,
diagnostics, stdout, stderr, and ToolResults. Agent Checkpoints remain separate bounded plaintext
and can contain full repair context. Repair projection and event-chain verification use one SQLite
read transaction snapshot so active concurrent appends do not create mixed-snapshot false alarms.

An active Repair row without `RepairStopped` is indeterminate evidence. M4c does not automatically
resume it because replay could repeat writes. Inspect the working tree and start a new explicitly
approved session.

## Stop Reasons

Representative terminal reasons include:

- `already_passing`, `repaired`;
- `not_approved`, `invalid_scope`, `dirty_repository`;
- `worker_failed`, `no_progress`, `scope_violation`, `patch_limit`;
- `test_infrastructure_error`, `test_mutated_repository`;
- `repeated_failure`, `max_attempts`, `time_limit`;
- `persistence_error`.

Cancellation propagates instead of becoming an ordinary result.

## Security Boundary

This is bounded orchestration, not isolation:

- approved Pytest still executes repository code with the Agent user's OS authority;
- malicious tests or host processes can bypass Tool governance;
- Git and Workspace checks observe final state and do not prevent transient change-and-restore;
- SHA-256 chains detect inconsistent stored rows but are not signed or authenticated;
- failed Repair changes are left in the working tree for inspection;
- M4c does not stage, commit, reset, clean, stash, revert, or create a Worktree;
- automatic crash Resume is intentionally not provided.
