# Mini CodeAgent

A framework-light, provider-neutral coding agent built from first principles.

> Status: pre-alpha. M6b provides a provider-neutral Agent Core, Anthropic/OpenAI-compatible
> adapters, a schema-validating Tool Registry, a cross-platform Workspace boundary, bounded
> Read/Search, conflict-aware Write/Edit, policy-governed argv command execution, and deterministic
> context admission, hardened read-only Git evidence, governed Pytest diagnostics, versioned SQLite
> Session/Trace persistence, fail-closed Checkpoint/Resume, and a host-controlled bounded Repair
> loop, provenance-aware lazy Skills, deterministic host-registered Tool Hooks, and host-pinned
> local MCP stdio Tools, bounded host-profiled read-only analysis Subagents, and host-managed
> Worktree implementation candidates with separately approved adoption. OS sandboxing,
> shell-string execution, project-provided executable Hooks, automatic Repair resume, remote
> HTTP/OAuth MCP, automatic commit/merge/push, and live-provider CI are not implemented.

## Requirements

- Python 3.12 or 3.13
- uv 0.11.25

## Development

```powershell
uv sync --all-groups
uv run mini-code-agent --version
uv run mini-code-agent doctor
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv build --build-constraint build-constraints.txt --require-hashes
```

If `uv` was installed by `pip` but is not available on the Windows `PATH`, use
`python -m uv` in the commands above.

## Configuration

Precedence is:

```text
defaults < TOML file < MINI_CODE_AGENT_* environment variables < CLI overrides
```

Default config paths follow the operating system conventions provided by Platformdirs.
Secrets are accepted from environment variables but are never printed by `doctor`.
See `config.example.toml` for supported inputs.

## Provider Adapters

Both adapters implement the same `ModelProvider` protocol:

```python
from pydantic import SecretStr

from mini_code_agent.providers import (
    AnthropicProvider,
    OpenAICompatibleProvider,
)

anthropic = AnthropicProvider(
    api_key=SecretStr("..."),
    model="your-claude-model",
)

compatible = OpenAICompatibleProvider(
    api_key=SecretStr("..."),
    model="your-model",
    base_url="https://your-provider.example/v1",
)
```

Applications must call `await provider.aclose()` for internally created clients. Injected
`httpx.AsyncClient` instances remain caller-owned. M1b tests use `httpx.MockTransport`; no live
API success is claimed without a separate credentialed smoke test.

## Read-only Workspace

```python
from pathlib import Path

from mini_code_agent.tools import ReadFileTool, SearchTextTool, ToolRegistry
from mini_code_agent.workspace import WorkspaceBoundary

workspace = WorkspaceBoundary(Path.cwd())
tools = ToolRegistry([
    ReadFileTool(workspace),
    SearchTextTool(workspace),
])
```

Model paths must be workspace-relative POSIX-style paths. The boundary rejects links, `.git`,
cross-platform special path forms, non-regular/binary/non-UTF-8 files, and resource-limit
violations. This is a filesystem policy, not an OS sandbox.

## Governed Writes

Side-effecting tools must be composed through `GovernedToolExecutor`. The secure defaults allow
reads, ask for interactive approval on writes, and deny execute/network actions. Existing files
require the SHA-256 returned by `read_file`; new files use create-only semantics.

See `docs/architecture/governed-writes.md` for the complete composition and concurrency limits.

## Governed Commands

`run_command` uses explicit argv and never `shell=True`. Execute is denied by default and requires
an explicit policy rule. The runner validates cwd, strips arbitrary environment variables, bounds
time/output, and cleans process trees on timeout, overflow, or cancellation.

This is local process lifecycle control, not an OS sandbox. See
`docs/architecture/governed-command-execution.md`.

## Context Budget

Every provider request is estimated before network I/O. Compaction keeps the original goal,
newest completed unit, and all side-effecting or unknown-tool exchanges; ToolCall and ToolResult
batches remain atomic. Older read-only history may be omitted with bounded count/fingerprint
evidence. If required history cannot fit, the run stops without calling the provider.

The default UTF-8 estimator is deterministic and provider-neutral, not an exact vendor tokenizer.
M3a is not durable memory or crash-safe replay prevention. See
`docs/architecture/context-budget.md`.

## Session and Trace

```python
from pathlib import Path

from mini_code_agent.agent.runtime import AgentRuntime
from mini_code_agent.persistence import SqliteSessionTraceStore

with SqliteSessionTraceStore(Path("agent-state.db")) as store:
    session = store.create_session()
    runtime = AgentRuntime(
        provider,
        tools,
        journal=store.journal(session.session_id),
    )
    result = await runtime.run(user_prompt="Inspect the project.")
    verification = store.verify_trace(session.session_id)
```

SQLite database schema v3 stores bounded Trace-envelope-v1 lifecycle events, Checkpoints,
Session/Run projections, and an independent bounded Repair lifecycle journal in transactional
boundaries.
The required journal records `ModelStarted` before Provider I/O and `ToolStarted` before Tool
execution; a persistence failure stops later work. Event IDs are idempotency keys, and a
per-Session SHA-256 chain detects inconsistent rows and projections.

Trace events exclude prompts, Tool arguments/results, patches, and command output. The hash chain
is not signed or tamper-proof. See
`docs/architecture/session-trace.md`.

## Checkpoint and Resume

M3c saves full typed state only before Provider calls: initially and after complete ToolResult
batches. SQLite schema v2 atomically binds each snapshot to `CheckpointSaved`. Resume verifies the
Trace, Tool contract, and bounded Workspace fingerprint, then explicitly gates possible
Provider/read-only replay. Any uncheckpointed write, execute, or network action blocks automatic
Resume.

Checkpoint payloads contain full prompts, model text, Tool arguments/results, and command output
as bounded plaintext. This is local crash recovery, not encryption, distributed coordination, or
exactly-once external execution. Keep the database outside the model-controlled Workspace. See
`docs/architecture/checkpoint-resume.md`.

## Read-only Git

`git_status` parses bounded `--porcelain=v2 -z` output into typed branch and file entries.
`git_diff` returns a bounded staged or unstaged patch. The Workspace must be the exact repository
top-level.

The client disables paging, optional locks, fsmonitor, external diff, textconv, and submodule
recursion. It never exposes model-controlled Git argv and does not provide add/commit/reset/
checkout/push. Git evidence may contain source code or secrets and is a point-in-time observation.
See `docs/architecture/readonly-git.md`.

## Governed Pytest

`run_tests` executes a host-configured Pytest profile. The model can provide only optional existing
workspace-relative files/directories and a reason; it cannot choose the Python executable, cwd,
plugins, timeout, report path, environment, or arbitrary options.

The fixed argv uses isolated Python startup, disables bytecode and `.pytest_cache` writes from the
harness, disables ambient plugin autoload, places `--` before targets, and converts bounded
built-in JUnit XML into typed process/report statuses, counts, and diagnostics. Execute remains
denied by default and requires an explicit policy rule plus approval.

Approved tests still run arbitrary repository code with the Agent process's OS permissions. This
is governed execution, not a sandbox. See
`docs/architecture/governed-test-execution.md`.

## Bounded Repair

`RepairRuntime` owns one explicit feedback-control session above `AgentRuntime`. It requires a
clean repository, an exact set of existing Git-tracked editable files, explicit approval, a
required Repair journal, and one fixed host-owned Pytest target set. Each Agent attempt is
restricted by `RepairActionGuard` to read operations and exact scoped writes; execute and network
actions are denied before ordinary Policy and approval.

The host validates Git status/diff and Workspace identity before accepting a patch, reruns the
same tests, and stops only on a complete passing report or a typed safety/budget reason. Attempts,
elapsed time, patch size, prompt size, and repeated failure fingerprints are independently
bounded. Failed changes remain in the working tree for inspection; the runtime never stages,
commits, resets, cleans, or automatically resumes an interrupted Repair. See
`docs/architecture/bounded-repair-loop.md`.

## Governed Skills and Hooks

Skills are bounded direct-child `SKILL.md` files discovered only from host-configured roots.
Strict UTF-8, restricted YAML, Pydantic metadata, source-qualified IDs, regular-file checks, file
identity, and SHA-256 protect the discovery/load contract. `list_skills` exposes metadata only;
`load_skill` requires the observed fingerprint and returns explicitly labelled untrusted
Markdown. Skill content never registers executable capabilities or bypasses Tool Policy.

Tool Hooks are typed async handlers registered by trusted host code. Pre-Hooks may continue or
block, but continue still passes through ordinary Policy and approval. Post-Hooks observe the
actual result; timeout, exception, or invalid return cannot replace it. Repository command/HTTP/
prompt Hooks and dynamic Python imports are not supported. In-process Hooks have the Agent
process authority and are not sandboxed. See `docs/architecture/governed-extensions.md`.

## Governed MCP Stdio

Local MCP Tools use the official stable Python SDK v1 over direct stdio. A trusted host profile
pins an absolute executable/argv/cwd, server identity, exact Tool grant set, host-owned
description/side-effect/risk, canonical input/output-schema hashes, and hard lifecycle/content
limits.

Starting the process requires dedicated connection approval. Verified local aliases still pass
through the ordinary Tool Registry, Hooks, Policy, and optional Tool approval with
`TrustSource.EXTENSION`. Server instructions, descriptions, annotations, icons, and `_meta` are
not authority and are not copied into model-facing definitions.

Only bounded text and object-shaped structured JSON results are accepted. Calls are serialized
and never retried; a timed-out side-effecting Tool reports uncertain completion. Stdio and user
approval are not OS sandboxing. Remote HTTP/OAuth, Resources, Prompts, Roots, Sampling,
Elicitation, Tasks, dynamic Tool lists, and package installation are not supported. See
`docs/architecture/governed-mcp.md`.

## Governed Analysis Subagents

M6a exposes one governed parent Tool per immutable host profile. The model supplies one to four
unique bounded tasks; the host fixes the child system prompt, exact read-only Tool names, Agent
limits, concurrency, deadlines, and result budgets.

Before any child Provider request, `SubagentSupervisor` requires distinct Providers/executors,
exact `READ_ONLY` definitions, `governance_enforced is True`, and
`TrustSource.SUBAGENT` for every child Tool. Each child receives one fresh task message, not the
parent or sibling transcript. Delegation Tools are structurally unavailable to children.

All children belong to one `asyncio.TaskGroup`. A semaphore bounds concurrency, individual and
batch timeouts have typed outcomes, input order is preserved, and external cancellation cancels
and joins every child before being re-raised. Parent results contain bounded untrusted summaries
and ToolResult metadata/SHA-256 evidence, not raw child transcripts or Tool content. Events omit
tasks, prompts, summaries, arguments, results, repository content, and exception text.

In-process context isolation is not an OS sandbox. M6a cannot write, run commands, call network
Tools, open nested approval prompts, persist durable child traces, create Worktrees, or merge
changes. See `docs/architecture/governed-subagents.md`.

## Governed Worktree Candidates

M6b adds one separately governed implementation Tool. The parent model supplies only `task` and
`reason`; the host pins the exact clean repository, external state root, Git executable, allowed
path prefixes, implementation profile, optional fixed tests, and resource limits.

The host creates a locked detached `--no-checkout` Worktree and materializes the exact Git index
from raw object bytes. A fresh non-interactive child may use only host-approved Read/Search/
Write/Edit and optional `run_tests` Tools with `TrustSource.SUBAGENT`. It cannot use Git, arbitrary
commands, MCP/network, recursive delegation, or parent approval.

After the child stops, the host independently reconciles the complete tree with the immutable base
manifest and mutation ledger. Ready candidates persist canonical manifests and content-addressed
blobs outside the repository; the temporary Worktree is then verified and removed. Child
completion never mutates the parent checkout.

`adopt_subagent_candidate` is a separate high-risk WRITE Tool and approval. It requires the
original clean `HEAD`, revalidates every path/hash before the first replacement, applies only the
verified additions/modifications, and leaves them unstaged and uncommitted. Conflicts write
nothing; partial failures are either proven rolled back or marked uncertain for operator
recovery. `discard_subagent_candidate` accepts only a verified ready candidate.

Worktree path separation and rollback-aware adoption are not OS sandboxing or crash-atomic
transactions. M6b does not delete/rename files, run arbitrary commands, commit, merge, push, or
claim token/latency improvements. See
`docs/architecture/governed-worktree-candidates.md`.

## Documentation

- Product design: `docs/superpowers/specs/2026-06-29-mini-code-agent-design.md`
- Learning map: `docs/learning/knowledge-map.md`
- Learning evidence: `docs/learning/progress.md`
- Resume evidence: `docs/resume/project-profile.md`
- Agent Core: `docs/architecture/agent-core.md`
- Provider adapters: `docs/architecture/provider-adapters.md`
- Read-only tools: `docs/architecture/readonly-tools.md`
- Governed writes: `docs/architecture/governed-writes.md`
- Governed commands: `docs/architecture/governed-command-execution.md`
- Context budget: `docs/architecture/context-budget.md`
- Session and Trace: `docs/architecture/session-trace.md`
- Checkpoint and Resume: `docs/architecture/checkpoint-resume.md`
- Read-only Git: `docs/architecture/readonly-git.md`
- Governed test execution: `docs/architecture/governed-test-execution.md`
- Bounded Repair loop: `docs/architecture/bounded-repair-loop.md`
- Governed Skills and Hooks: `docs/architecture/governed-extensions.md`
- Governed MCP stdio: `docs/architecture/governed-mcp.md`
- Governed analysis Subagents: `docs/architecture/governed-subagents.md`
- Governed Worktree candidates: `docs/architecture/governed-worktree-candidates.md`
- Threat model: `docs/architecture/threat-model.md`
- Provider protocol ADR: `docs/adr/0002-provider-wire-protocols.md`
- Workspace boundary ADR: `docs/adr/0003-workspace-boundary.md`
- Governed file writes ADR: `docs/adr/0004-governed-file-writes.md`
- Argv command runner ADR: `docs/adr/0005-argv-command-runner.md`
- Context budget ADR: `docs/adr/0006-deterministic-context-budget.md`
- SQLite Session/Trace ADR: `docs/adr/0007-sqlite-session-trace.md`
- Safe Checkpoint/Resume ADR: `docs/adr/0008-safe-checkpoint-resume.md`
- Hardened read-only Git ADR: `docs/adr/0009-hardened-readonly-git.md`
- Fixed Pytest/JUnit boundary ADR: `docs/adr/0010-fixed-pytest-junit-boundary.md`
- Host-controlled bounded Repair ADR: `docs/adr/0011-host-controlled-bounded-repair.md`
- Inert Skills and host Hooks ADR: `docs/adr/0012-inert-skills-host-hooks.md`
- Host-pinned stdio MCP ADR: `docs/adr/0013-host-pinned-stdio-mcp.md`
- Bounded host-profiled Subagents ADR: `docs/adr/0014-bounded-host-profiled-subagents.md`
- Governed Worktree candidates ADR: `docs/adr/0015-governed-worktree-candidates.md`

## License

Apache-2.0
