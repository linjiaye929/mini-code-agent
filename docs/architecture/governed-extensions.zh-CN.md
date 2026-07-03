# 受治理 Skills 与 Tool Hooks

[English](governed-extensions.md) | [简体中文](governed-extensions.zh-CN.md)

## 目的

M5a 增加两个 Extension Surface，同时把权限保留在 Host Code：

- Skill 是带来源、有界、惰性加载的 Markdown Data；
- Hook 是围绕受治理 Tool Execution、由宿主注册的 In-process Callback。

Repository Content 不能注册 Python Code、Tool、Hook、Provider 或 Policy Rule。这是
Instruction Extension 与 Executable Plugin 的核心区别。

## Skill 流程

```text
host-configured roots
  -> bounded direct-child scan
  -> lstat/reparse/regular-file checks
  -> strict UTF-8 + restricted YAML + Pydantic
  -> source-qualified descriptor + SHA-256
  -> list_skills metadata
  -> load_skill(skill_id, expected_sha256)
  -> path/file identity and content revalidation
  -> labelled untrusted Markdown
```

`SkillRoot` 是显式 Host Configuration。Discovery 不递归搜索 Home 或 Repository。最多
32 个 Root、每个 Root 512 个 Entry、128 个 Skill Candidate 和 64 个 Disabled ID。
每个 Skill 必须是 Direct Child Directory，且只含一个 Regular `SKILL.md`。Link、Junction、
Reparse Point、Special File、非法 UTF-8、Oversized Input、Unsafe YAML、Unknown Metadata
和 Empty Body 会使用有界 Issue Code Quarantine。

允许的 Frontmatter 有意保持很小：

```yaml
name: review-python
description: Review Python changes against repository conventions.
version: 1.0.0
model_invocable: true
```

`name` 必须匹配 Directory。ID 总是限定为 `managed:name`、`user:name` 或 `project:name`。
不存在静默 Cross-source Precedence；同一来源 Duplicate 全部 Quarantine。

Discovery 在 Private Entry 中保存 File Device、Inode、Creation/Change Timestamp、Byte
Count、Metadata 和 SHA-256。`load_skill` 要求调用方观察到的 Descriptor SHA，并重新验证
Root、Directory、Regular File、Open Handle、Identity、Parse Result、Metadata、Size 和
Hash。任何 TOCTOU Drift 返回 `skill_changed`，调用方必须重新 Discover。

`list_skills` 不返回 Body 或 Absolute Path。`load_skill` 将内容标记为
`untrusted_markdown` 并包含 Derived Source/Trust Provenance。加载不会修改 Tool Definition
或 Policy。

## Hook 流程

```text
ToolCall JSON Schema
  -> ActionPreview
  -> optional ActionGuard
  -> ordered pre-Tool Hooks
  -> Policy allow / ask / deny
  -> approval when required
  -> Tool execution
  -> ordered post-Tool Hooks
  -> original ToolResult
```

`HookRegistration` 由可信 Application Composition Root 提供。M5a 不导入由 Skill Markdown、
Repository Configuration、Environment Input 或 Model Output 指定的 Module，也不启动其
指定的 Command。

Registration 有有界 ID、Host-selected Source、Priority、Phase 和 Typed Async Handler。
ID 跨 Phase 唯一，最多 64 个 Hook；按 Priority 与 ID 确定性排序，每次调用独立 Timeout。

Pre-Hook 只能返回 `continue` 或 `block`：

- `continue` 表示继续进入普通 Policy，不授予权限；
- `block` 返回已有通用 `permission_denied`；
- Timeout、Exception、Malformed Result 或 Audit Failure 都 Fail Closed；
- Cancellation 传播。

Post-Hook 是 Observer，接收实际 `ToolResult` 但不能替换。Exception、Timeout、Malformed
Return 或 Audit Failure 被隔离，后续 Observer 继续。Cancellation 仍传播，因为调用方
必须知道执行终止在不确定 Lifecycle Boundary。

## Audit 边界

`HookAuditRecord` 只含 Hook ID/Source/Phase/Outcome、Tool Call ID/Name、有界 Elapsed
Milliseconds 和静态 Failure Code，不含 Tool Argument、Preview、Resource、Diff、Result、
Skill Body 或 Raw Exception。

M5a 提供 Null 与 In-memory Recording Sink。Hook Audit 尚未 Durable，因为当前 Tool
Executor API 没有稳定 `run_id` 和 `turn`。现有 Agent Trace 仍记录 Tool Start/Completion
及返回的 Denial/Result。Durable Correlation 需要显式 Execution-context Contract。

## 组合

```python
from mini_code_agent.hooks import HookRegistration, ToolHookRunner
from mini_code_agent.policy import GovernedToolExecutor
from mini_code_agent.skills import ListSkillsTool, LoadSkillTool
from mini_code_agent.tools import ToolRegistry

tools = ToolRegistry(
    [
        ListSkillsTool(skill_catalog),
        LoadSkillTool(skill_catalog),
        *workspace_tools,
    ]
)
hooks = ToolHookRunner(host_registrations)
executor = GovernedToolExecutor(
    tools,
    policy=policy,
    approval=approval,
    session_mode=session_mode,
    trust_source=trust_source,
    hooks=hooks,
)
```

## 失败矩阵

| 边界 | 失败 | 结果 |
|---|---|---|
| Skill Root | Missing、Linked、Reparse、Not Directory | Root Issue；零 Entry |
| Skill Entry | Linked、Non-regular、Invalid YAML/Metadata/Body | Entry Quarantined |
| Skill Identity | 多个 Root 中相同 Qualified ID | 所有冲突 Quarantined |
| Skill Load | Stale SHA、Replacement、Deletion、Metadata/Content Drift | `skill_changed` |
| Pre-Hook | Explicit Block | Generic Permission Denial |
| Pre-Hook | Timeout、Exception、Invalid Result、Audit Failure | Fail-closed Denial |
| Policy | Hook Continue 后 Deny | Denial；不执行 Tool/Post-Hook |
| Post-Hook | Timeout、Exception、Invalid Return、Audit Failure | 保留 Original Result |
| 任一 Hook Phase | Cancellation | `CancelledError` 传播 |

## 威胁边界与非承诺

- Parsed Skill Markdown 仍不可信，可能包含 Prompt Injection。
- SHA-256 只证明与观察 Byte 相等，不证明 Author 或 Safety。
- Source Label 是 Host Provenance，不是 Signature。
- Lazy Loading 降低 Default Context Use，不净化 Loaded Instruction。
- In-process Hook Handler 以 Agent Process Authority 执行，是可信 Host Code。
- Hook Timeout 无法终止 Handler 已委托给其他 Thread/Process 的工作。
- M5a 不执行 Project Command/HTTP/Prompt/MCP Hook。
- M5a 不加载 Supporting Skill File、不安装 Plugin、不提供 Durable Hook Audit，也不声称
  OS Isolation。

公开 `SKILL.md`、Lazy Loading 和 Lifecycle Hook 概念与官方
[Claude Code Skills](https://code.claude.com/docs/en/slash-commands) 和
[Hooks Reference](https://code.claude.com/docs/en/hooks) 对齐，但实现有意支持更窄且
不可执行的子集。
