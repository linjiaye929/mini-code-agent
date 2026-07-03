# Governed Skills and Tool Hooks

[English](governed-extensions.md) | [简体中文](governed-extensions.zh-CN.md)

## Purpose

M5a adds two extension surfaces while keeping authority in host code:

- a Skill is source-qualified, bounded, lazily loaded Markdown data;
- a Hook is a host-registered in-process callback around governed Tool execution.

Repository content cannot register Python code, Tools, Hooks, Providers, or Policy rules. This is
the central distinction between an instruction extension and an executable plugin.

## Skill Flow

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

`SkillRoot` is explicit host configuration. Discovery does not recursively search a home
directory or repository. It accepts at most 32 configured roots, 512 entries per root, 128 Skill
candidates, and 64 disabled IDs. Each Skill is a direct child directory with one regular
`SKILL.md`; links, junctions, other reparse points, special files, invalid UTF-8, oversized input,
unsafe YAML, unknown metadata, and empty bodies are quarantined with bounded issue codes.

The accepted frontmatter is intentionally small:

```yaml
name: review-python
description: Review Python changes against repository conventions.
version: 1.0.0
model_invocable: true
```

`name` must match the directory. IDs are always qualified as `managed:name`, `user:name`, or
`project:name`. There is no silent cross-source precedence. Same-source duplicates are all
quarantined.

Discovery stores the file device, inode, creation/change timestamp, byte count, metadata, and
SHA-256 in a private entry. `load_skill` requires the descriptor SHA observed by the caller, then
revalidates the root, directory, regular file, open file handle, identity, parse result, metadata,
size, and hash. Any time-of-check/time-of-use drift returns `skill_changed`; the caller must
rediscover.

`list_skills` never returns bodies or absolute paths. `load_skill` labels content as
`untrusted_markdown` and includes derived source/trust provenance. Loading does not modify Tool
definitions or Policy.

## Hook Flow

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

`HookRegistration` is supplied by the trusted application composition root. M5a never imports a
module or starts a command named by Skill Markdown, repository configuration, environment input,
or model output.

Registrations have a bounded ID, host-selected source, priority, phase, and typed async handler.
IDs are unique across phases; at most 64 Hooks are allowed. Ordering is deterministic by priority
and ID. Each invocation has an independently bounded timeout.

Pre-Hooks return only `continue` or `block`:

- `continue` means "continue to ordinary Policy"; it does not grant permission;
- `block` returns the existing generic `permission_denied`;
- timeout, exception, malformed result, or audit failure also fails closed;
- cancellation propagates.

Post-Hooks are observers. They receive the actual `ToolResult`, but cannot replace it. An
exception, timeout, malformed return, or audit failure is isolated and later observers continue.
Cancellation still propagates because the caller must know execution ended at an uncertain
lifecycle boundary.

## Audit Boundary

`HookAuditRecord` includes only Hook ID/source/phase/outcome, Tool call ID/name, bounded elapsed
milliseconds, and a static failure code. It excludes Tool arguments, previews, resources, diffs,
results, Skill bodies, and raw exception text.

M5a provides null and in-memory recording sinks. Hook audit is not yet durable because the current
Tool executor API does not receive stable `run_id` and `turn`. Existing Agent Trace still records
Tool start/completion and the returned denial or result. Durable correlation requires an explicit
execution-context contract rather than hidden global state.

## Composition

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

## Failure Matrix

| Boundary | Failure | Result |
|---|---|---|
| Skill root | missing, linked, reparse, not directory | root issue; no entries |
| Skill entry | linked, non-regular, invalid YAML/metadata/body | entry quarantined |
| Skill identity | same qualified ID in multiple roots | every conflict quarantined |
| Skill load | stale SHA, replacement, deletion, metadata/content drift | `skill_changed` |
| Pre-Hook | explicit block | generic permission denial |
| Pre-Hook | timeout, exception, invalid result, audit failure | fail-closed denial |
| Policy | deny after Hook continue | denial; no Tool or post-Hook |
| Post-Hook | timeout, exception, invalid return, audit failure | original result retained |
| Either Hook phase | cancellation | `CancelledError` propagates |

## Threat Boundary and Non-Claims

- Parsed Skill Markdown remains untrusted and can contain prompt injection.
- SHA-256 proves equality with observed bytes, not authorship or safety.
- Source labels are host provenance, not signatures.
- Lazy loading reduces default context use; it does not sanitize loaded instructions.
- In-process Hook handlers execute with the Agent process authority and are trusted host code.
- Hook timeout cannot stop work a handler delegated to another thread or process.
- M5a does not execute project command/HTTP/prompt/MCP Hooks.
- M5a does not load supporting Skill files, install plugins, provide durable Hook audit, or claim
  OS isolation.

The public `SKILL.md`, lazy-loading, and lifecycle Hook concepts align with the official
[Claude Code Skills](https://code.claude.com/docs/en/slash-commands) and
[Hooks reference](https://code.claude.com/docs/en/hooks). The implementation intentionally
supports a narrower, non-executable subset.
