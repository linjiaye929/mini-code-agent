# Governed File Writes

[English](governed-writes.md) | [简体中文](governed-writes.zh-CN.md)

## Execution Flow

```text
Model ToolCall
    |
    v
ToolRegistry.validate
    |-- known tool?
    `-- Draft 2020-12 arguments valid?
    |
    v
WriteFileTool / EditFileTool.preview
    |-- Workspace path and text policy
    |-- current SHA-256 precondition
    |-- unique edit match
    `-- bounded unified diff
    |
    v
PolicyEngine
    |-- allow: continue
    |-- ask: interactive approval only
    `-- deny: correlated permission error
    |
    v
ToolRegistry.execute
    |-- repeat argument and workspace validation
    |-- repeat SHA-256 precondition
    `-- same-directory atomic mutation
    |
    v
Correlated ToolResult with relative path and hashes
```

The model can request an action, but cannot grant its own permission. Policy rules and approval
handlers are trusted application dependencies outside the prompt.

## Policy Model

Rules are immutable and evaluated in tuple order; the first complete match wins. A rule can match
tool name, side effect, risk, every resource path, session mode, and trust source.

| Side effect | Default |
|---|---|
| Read-only | `allow` |
| Write | `ask` |
| Execute | `deny` |
| Network | `deny` |

An `ask` decision in non-interactive mode is denied without invoking an approval handler.
Malformed arguments and invalid previews fail before approval.

## Approval Contract

An approval request contains:

- tool name, side-effect class, and risk level;
- a static bounded summary and model-supplied bounded reason;
- workspace-relative resource paths;
- a unified diff capped at 32,768 characters;
- the matched policy rule ID and static rationale.

Approval means "permit this validated action under the current preconditions." It does not mean
the generated code is correct or safe, and it is not an OS sandbox.

## Write Semantics

`write_file` has two modes:

- no `expected_sha256`: create a new file only;
- exact `expected_sha256`: replace an existing file only when its raw bytes still match.

`edit_file` always requires the SHA-256 returned by `read_file`. Its non-empty `old_text` must
occur exactly once, and the replacement must change the file. Zero matches, multiple matches,
no-op edits, and stale snapshots fail without mutation.

Both tools delegate all filesystem access to `WorkspaceBoundary`. Successful results contain the
relative path, create/replace mode, before/after hashes, byte and line counts, and bounded diff.

## Atomicity and Concurrency

The boundary writes a temporary file in the target directory, flushes and `fsync`s its content,
preserves existing permission bits for replacements, rechecks the precondition, and then uses:

- `os.link` for create-only publication;
- `os.replace` for replacement.

Temporary files are removed on every observed failure. This prevents partial target content and
detects ordinary stale reads. It does not provide a general filesystem compare-and-swap:
an unrelated process can still replace a file in the small interval between the final check and
`os.replace`. Hostile concurrent writers require OS-level isolation or descriptor-relative
platform APIs.

## Java and Flink Analogies

| Existing experience | Mini CodeAgent concept |
|---|---|
| Spring interceptor / servlet filter | `GovernedToolExecutor` around tool dispatch |
| Spring Security decision manager | ordered `PolicyEngine` rules |
| Bean Validation before service code | JSON Schema plus Pydantic argument validation |
| JPA `@Version` optimistic locking | raw-byte SHA-256 write precondition |
| transaction commit conflict | stale snapshot returns `conflict` without write |
| temp table then atomic publish | same-directory temporary file plus `os.replace` |
| Flink checkpoint barrier | read hash identifies the state snapshot an edit was based on |
| exactly-once sink precondition | create-only publication and correlated ToolCall result |

The analogy is conceptual. Filesystem replacement is not a database transaction and does not
offer rollback, isolation, or distributed exactly-once semantics.
