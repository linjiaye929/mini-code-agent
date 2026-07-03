# 有限 Repair Loop

[English](bounded-repair-loop.md) | [简体中文](bounded-repair-loop.zh-CN.md)

## 目的

M4c 在普通 Agent Runtime 之上增加宿主控制的反馈循环：

```text
approve -> admit clean tracked scope -> baseline test
        -> one Agent repair attempt -> Git validation -> fixed Pytest verification
        -> success, typed stop, or another bounded attempt
```

Agent 只提出并执行一次受治理 Edit Attempt，不控制 Attempt Count、Test Executable、
Test Argument、Success Decision 或 Stop Condition。

## 组合

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

# 使用 GovernedToolExecutor 构造 AgentRuntime，其 guard 为
# RepairActionGuard(scope)，然后进行适配：
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

Worker Executor 仍执行普通 Tool Schema、Preview、Policy 和 Write Approval。Repair Action
Guard 是额外 Pre-policy Restriction，不替代原有治理。

## 准入

除非全部条件满足，否则 Runtime 在 Provider/Pytest 前拒绝 Session：

1. Repair Approval 成功；
2. Workspace、Git 与 Pytest Root 完全一致；
3. 仓库没有 Staged、Unstaged、Untracked、Rename、Conflict 或 Submodule Change；
4. 每个 Editable Path 都解析为已有 Regular Non-link Workspace File；
5. `git ls-files --error-unmatch -z -- :(top,literal)<path>` 确认每个精确 Path 已 Tracked；
6. Worker Scope Fingerprint 与 Coordinator Scope Fingerprint 一致；
7. Required `RepairStarted` 已 Durable Append。

仅有 Clean Git Status 不够，因为 Ignored File 不出现在 Status/Diff 中；Literal Tracked-path
Query 补上这部分证据。

## 动作范围

`RepairActionGuard` 从 `GovernedToolExecutor` 接收可信 `ActionPreview`：

- 只读动作继续进入 Policy；
- Write 必须至少有一个 Resource，且每个 Resource 精确等于 Approved Path；
- Execute 与 Network 被拒绝；
- Denial 发生在 Policy Approval 和 Tool Execution 前。

Pytest 由 Coordinator 控制。Repair Worker 会被指示不要运行 Test 或 Command。

## 测试协议

Baseline 和每个 Accepted Patch 后运行相同的 Host-owned Target。成功必须同时满足：

- Pytest Execution Status 为 `passed`；
- JUnit Report Status 为 `complete`。

Exit 1 且 Report Complete，并至少有一个 Failure/Error Diagnostic 时可 Repair。Timeout、
Output Overflow、Interruption、Internal/Usage Error、No Test、Unknown Exit 或 Incomplete
Report 都属于 Infrastructure Stop。

固定 Python Prefix 为 `python -I -B -m pytest`。`-B` 避免 Bytecode Cache Write 使仓库
变脏；Ambient Plugin Autoload 和 Pytest Cache 继续关闭。

## Git 证据

每次 Worker Attempt 后：

- Branch OID/Head/Upstream/Ahead/Behind Metadata 必须不变；
- Staged Diff 必须为空；
- 每个 Status Entry 必须是 Scope 内普通 Unstaged `M` 且不是 Submodule；
- 完整 Unstaged Diff 必须非空、不同于上次 Attempt，并符合 Patch Byte Budget；
- Test Execution 前重新通过 Workspace Resolve Editable Path。

Test 后再次读取 Status/Diff。Fingerprint 与 Workspace File Identity 必须与 Pre-test
Evidence 一致；任何存活的 Test Mutation 都会停止 Session。

## Failure Fingerprint 与预算

Normalized Failure SHA-256 包含 Process/Report Status、重新计算的 Count，以及排序后的
Diagnostic Outcome/Name/Class/File/Line/Message。它排除 stdout、stderr、Duration 和 Detail
Body，因为这些通常包含不稳定格式或 Path。

| 预算 | 默认值 | 硬上限 |
|---|---:|---:|
| Repair Attempt | 3 | 10 |
| Session Elapsed Time | 900 秒 | 3600 秒 |
| Full Unstaged Patch | 256 KiB | 8 MiB |
| Same Failure Appearance | 2 | 5 |
| Worker Prompt | 64 KiB 字符 | 256 KiB 字符 |

Baseline Fingerprint 算第一次出现，因此默认情况下一个 Attempt 后 Failure 不变就停止。

## Durable Repair Trace

SQLite Schema v3 增加独立 `repair_runs` 和 `repair_events`。Repair Event 使用 UUID ID、
Exact-payload Idempotency、Per-repair Sequence、Canonical JSON、SHA-256 Predecessor Chain，
并在一个 `BEGIN IMMEDIATE` 中提交 Event 与 Projection。

Event 包含 ID、Status、Count、Budget 和 Hash，不含 Prompt、Patch、Diagnostic、stdout、
stderr 或 ToolResult。Agent Checkpoint 是单独的有界明文，可能包含完整 Repair Context。
Projection 与 Event-chain Verification 使用同一 SQLite Read Transaction Snapshot。

没有 `RepairStopped` 的 Active Repair Row 是 Indeterminate Evidence。M4c 不自动 Resume，
因为 Replay 可能重复 Write；应先检查 Working Tree，再显式批准新 Session。

## 停止原因

代表性 Terminal Reason：

- `already_passing`、`repaired`；
- `not_approved`、`invalid_scope`、`dirty_repository`；
- `worker_failed`、`no_progress`、`scope_violation`、`patch_limit`；
- `test_infrastructure_error`、`test_mutated_repository`；
- `repeated_failure`、`max_attempts`、`time_limit`；
- `persistence_error`。

Cancellation 会继续传播，不会转换为普通 Result。

## 安全边界

这是 Bounded Orchestration，不是隔离：

- Approved Pytest 仍以 Agent User OS Authority 执行 Repository Code；
- Malicious Test 或 Host Process 可以绕过 Tool Governance；
- Git/Workspace Check 观察最终状态，不能阻止临时 Change-and-restore；
- SHA-256 Chain 可发现不一致 Row，但没有 Signature/Authentication；
- Failed Repair Change 留在 Working Tree 供检查；
- M4c 不 Stage、Commit、Reset、Clean、Stash、Revert 或创建 Worktree；
- 有意不提供 Automatic Crash Resume。
