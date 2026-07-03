# 威胁模型

[English](threat-model.md) | [简体中文](threat-model.zh-CN.md)

## 受保护资产

- 用户源码与未提交改动。
- 所选 Workspace 外的文件。
- API Key 与 Environment Secret。
- Git History 与 Repository Integrity。
- Session、Checkpoint 与 Trace Integrity。

## 不可信输入

- Model Output 与 ToolCall Argument。
- Repository File 与 Instruction。
- Skill、Hook 与 Project Configuration。
- MCP Server 及其 Tool Result。
- Child Agent Output、Summary 与 Delegated Task Result。
- Command Output 与 Generated Patch。

## 已实现控制

- Settings 与 Log 对 Secret 安全，配置优先级明确。
- 有副作用 Tool 必须经过 Schema、Workspace、Policy、Preview 和 Approval；非交互 `ask`
  Fail Closed。
- Read/Search 通过 Draft 2020-12 Schema 与统一 `WorkspaceBoundary`，拒绝跨平台 Traversal、
  Link/Junction、`.git`、ADS/Device、错误 Type/Size/Encoding/Binary，并限制 Traversal 与 Result。
- Write/Edit 使用有界 Diff Preview、SHA-256 Precondition、Create-only Publish 和同目录
  Atomic Replace；Approval 后再次校验，降低 Stale Write 风险。
- Command 只接受 argv，验证 Workspace cwd，继承最小 Environment，限制 Output/Time 并
  Cleanup Process Tree；Execute 默认 Deny，必须有显式 Tool/Executable Policy。
- 每次 Provider Call 前执行确定性 Context Admission；ToolCall/ToolResult Batch 原子保留；
  Write/Execute/Network/Unknown Tool Exchange 固定，避免模型因忘记动作而重复执行。
- Compaction Marker/Event 只保存有界 Count 与 Transcript Fingerprint，不复制被省略内容。
- SQLite Trace 只存有界 Typed Lifecycle Metadata，不存 Prompt、Argument、Result、Patch
  或 Command Output。Required Journal 在 Provider/Tool 前持久化 Started Event，失败立即停止。
- SQLite Append 与 Session/Run Projection 在一个 `BEGIN IMMEDIATE` 中更新，启用 WAL、
  Foreign Key、`synchronous=FULL`、Bounded Busy Timeout 和 Parameterized SQL。
- Event ID 提供 Exact-payload Idempotency；Per-session Sequence 与 SHA-256 Chain 检测
  Inconsistent Row/Projection；Configured Secret 从 Stop Error 中清除。
- Checkpoint 保存完整 Typed State；Resume 验证 Trace、Tool Contract、Workspace Fingerprint
  和 Post-checkpoint Event。未进入 Checkpoint 的 Write/Execute/Network 阻止自动 Replay。
- Git Status/Diff 使用固定只读 argv、Porcelain-v2 NUL Protocol，关闭 Optional Lock、
  fsmonitor、External Diff、Textconv 与 Submodule Recursion，并限制 Output。
- Pytest 由 Host-owned Profile 固定 Executable、Option、cwd、Environment、Timeout、Plugin
  与 Report Path；Target 继续经过 Workspace、Execute Policy、Critical Preview 和 Approval。
- Pytest 关闭 Ambient Plugin Autoload 和 Cache Write；Time/Output/JUnit Byte/Case/Diagnostic
  各自有界；Report 拒绝 Link/Special File、非法 UTF-8、DTD/Entity、Malformed XML 和
  Contradictory Outcome，并在所有 Exit Path 清理 Temporary Report。
- Repair 要求 Explicit Approval、Clean Repository、精确 Existing Regular Git-tracked Scope、
  Matching Worker Fingerprint 和 Durable `RepairStarted`。`RepairActionGuard` 在普通 Policy
  前拒绝 Execute/Network/Out-of-scope Write。
- 每次 Repair Attempt 校验 Branch、Staged/Untracked/Rename/Conflict/Submodule、Scope、
  Patch 与 Workspace Identity；Attempt、Elapsed Time、Patch Byte、Prompt Character 和
  Repeated Failure Fingerprint 均有独立 Hard Limit，只有 Complete Passing Host Test 成功。
- Skill 只是 Explicit Root 下的 Inert Bounded Markdown；Source-qualified ID、Restricted
  YAML、Regular-file Check、Fingerprint-required Load 与 TOCTOU Revalidation 防止其注册
  Executable Capability 或静默 Shadow Source。
- Pre-tool Hook 是 Trusted Host Code，只能 Continue to Policy 或 Veto，不能 Grant；
  Post-hook Failure 不能替换实际 ToolResult。
- Local MCP 要求 Absolute Executable、Exact argv/cwd/Environment Name、Connection Approval、
  Protocol/Identity、Static Complete Tool List、Host-owned Side Effect/Risk 与 Canonical
  Schema Hash。Verified Alias 标为 `TrustSource.EXTENSION`，仍经过 Schema、Preview、Hook、
  Policy 和可选 Tool Approval。
- MCP Server Instruction/Description/Annotation/Icon/Metadata/stderr/`_meta`/Image/Audio/
  Resource Content 不进入 Model-facing Contract 或 Successful Result。
- Subagent 只能由 Immutable Host Profile 创建；完整 Batch 在 Provider I/O 前校验 Task/
  Child ID 唯一有界、Provider/Executor Object 不复用。每个 Child 使用 Fresh One-message
  Context、Exact Read-only Definition、`TrustSource.SUBAGENT` 和 Non-interactive Policy，
  并拒绝 Recursive Delegation。
- 一个 `asyncio.TaskGroup` 拥有全部 Child；Child/Batch Deadline 产生 Ordered Typed Result，
  External Cancellation 会 Cancel/Join 后重新抛出。Summary 标记不可信，Evidence 只保存
  ToolCall Identity、Error/Count 和 ToolResult Hash。
- Implementation Delegation 要求 Host-owned Profile、Exact Clean Repository/HEAD、
  Non-overlapping State Root、Fixed Git、Path Prefix 与 Hard Limit。Host 创建 Locked
  Detached No-checkout Worktree，从 Raw Git Object 只物化 Regular `100644`/`100755`。
- Implementation Child 只得到 Fresh Non-interactive Read/Search/Write/Edit 和可选 Fixed
  Test；没有 Git、Arbitrary Command、Network、MCP、Delegation、Nested Approval、
  Delete/Rename/Mode Change。
- Successful Mutation 进入 Hash-chained Ledger；Candidate Readiness 由 Host 将 Complete Tree
  与 Base Manifest、Ledger、Path Allowlist、Mode、UTF-8/Content Hash 和 Resource Limit
  独立对账。Ready Manifest/Blob 保存于 Repository 外。
- Parent Adoption 是单独 High-risk WRITE Tool 与 Approval；Claim Candidate State，要求
  Original Clean HEAD，Preflight 并即时 Revalidate Path/Hash，按 Canonical Order Apply，
  验证 Final Change，保留 Unstaged/Uncommitted。Conflict 零写入；Partial Failure Reverse
  Rollback，并明确记录 Rolled-back 或 Uncertain。

## 非承诺

- Regex/Glob Command Filtering、Workspace Path Check、人类 Approval 都不是 Sandbox。
- Approved Process/Test/MCP Server 仍可使用当前 OS Identity 可访问的 File、Network、
  Credential 与其他 Host Resource；Process Group/Tree Termination 只是生命周期控制。
- Workspace/Git/Hash Revalidation 只能缩小 TOCTOU，不能消除同权限并发 Process 的 Race。
- Context/Trace/Candidate SHA-256 是 Equality Fingerprint，不是 Encryption、Authentication、
  Signature、Provenance、Semantic Correctness 或 Confidentiality。
- UTF-8 Context Estimator 不是精确 Vendor Tokenizer，不保证 Provider 接受请求。
- SQLite WAL 与 `synchronous=FULL` 改善本地 Durability，不提供 Replication、Distributed
  Consistency 或 Storage-failure Protection。Trace Hash Chain 未签名；可写数据库的攻击者
  可以同时重写 Payload 与 Hash。
- Checkpoint 包含完整 Prompt、Response、Tool Argument/Result、Patch 与 Command Output，
  以有界明文保存。Event Secret Scrubbing 不保护 Checkpoint Payload。
- Resume 对 Provider/Read-only Replay 仍可能重复 Cost 或 Observation，不提供 External
  Exactly-once 或 Reconciliation；SQLite Claim 也不是 Multi-host Lease。
- Git Status/Diff 可能含 Credential 或 Proprietary Source，并作为 ToolResult 发送给模型。
  `--no-optional-locks` 不会让 Point-in-time Evidence 对并发变化免疫。
- Approved Pytest 会执行 Repository Test、`conftest.py` 与 Host-trusted Plugin。Fixed argv、
  Minimal Environment、Approval 和 Limit 不等于 Filesystem/Process/Credential/Network Sandbox。
- JUnit 可被 Test Code 篡改；有界 Parser 不证明 Provenance，也不能阻止通过 stdout/stderr
  Exfiltration。禁用 `.pytest_cache` 只阻止 Harness Cache，Project Test 仍可修改 Workspace。
- Repair Clean/Tracked/Scope Check 是 Final-state Observation，不是 Isolation。Failure Change
  留在 Working Tree；M4c 不 Reset/Clean/Checkout/Stash/Stage/Commit，也不提供 Automatic
  Crash Resume、Rollback 或 External Exactly-once。
- Configured-value Scrubbing 不能发现未知 Secret，SQLite 也没有 At-rest Encryption。
- MCP Connection/Schema Equality 不证明 Executable Provenance、Implementation Safety、
  Read-only Behavior 或 Sandboxing；Local Server 在任何 Tool Policy 前就可使用 User Privilege
  执行 Startup Code。Timeout/Termination 不能证明 Side Effect 未完成。
- In-process Subagent 只隔离 Agent Message Context，不隔离 Python Memory、OS Identity、
  Credential、Provider Access 或 Malicious Host Code。Read-only Admission 只约束经过 Child
  Executor 的 Call；Deadline 依赖 Cooperative Cancellation。
- Worktree 只分离 Checkout Path，不隔离 Memory、Identity、Credential、Filesystem、Process
  或 Network。No-checkout Materialization 也不证明 Repository Content 安全。
- Candidate Manifest/Ledger/Git ID/Hash 不证明 Test Pass 或 Correctness。Adoption 是
  Process-serialized、Rollback-aware，但不是 Power-loss Atomic、Distributed、Exactly-once
  或 Database 2PC；Mixed State 会明确进入 `uncertain`。
- M6b 只支持有界 Add/Modify，不 Delete、Rename、Stage、Commit、Merge、Push、Reset、Clean、
  Automatic Adopt、Durable Child Resume 或 Recursive Delegation。
- 没有可复现 Benchmark 时，不声称 Token、Latency、Cost、Quality 或 Throughput 改善。
