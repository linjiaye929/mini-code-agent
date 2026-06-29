# M2b Policy, Approval, Write, and Edit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an independently testable allow/ask/deny Policy Engine and conflict-safe atomic Write/Edit tools with approval previews and diff evidence.

**Architecture:** `GovernedToolExecutor` is the only executor accepted by Agent Runtime when any definition has side effects. It validates ToolCall arguments, obtains a bounded `ActionPreview`, evaluates immutable policy rules, requests approval for `ask`, and only then dispatches through Tool Registry. Write/Edit use `WorkspaceBoundary` write resolution, optimistic SHA-256 preconditions, same-directory atomic replacement, and bounded unified diffs.

**Tech Stack:** Python 3.12/3.13, Pydantic v2, JSON Schema Draft 2020-12, `pathlib`, `hashlib`, `difflib`, `tempfile`, `os.replace`, Pytest, strict Pyright.

---

## Security Invariants

1. Agent Runtime rejects non-read-only definitions unless the executor declares and implements the
   governed execution contract.
2. Tool arguments validate before policy or approval; malformed calls never prompt or execute.
3. Policy is code/data outside the model prompt. Model text cannot grant permission.
4. Default policy is read-only `allow`, writes `ask`, execute/network `deny`.
5. Non-interactive approval denies every `ask`.
6. A denial or rejected approval returns one correlated ToolResult and never calls the tool.
7. Approval shows bounded tool, target, summary, side effect, risk, and diff; it never displays
   secrets, absolute host paths, or unbounded content.
8. Existing files require an exact SHA-256 precondition. New files use create-only semantics.
9. Edit requires exactly one literal old-text match; zero or multiple matches fail without write.
10. Writes reject links/junctions, special files, `.git`, out-of-root parents, binary/invalid
    files, and content over configured limits.
11. Atomic replacement uses a same-directory temporary file, flush/fsync, and `os.replace`; temp
    files are cleaned on every failure.
12. Results include relative path, before/after hashes, changed bytes/lines, and bounded unified
    diff evidence.
13. Workspace checks and atomic replacement do not claim OS sandboxing or eliminate every
    concurrent filesystem race.

## File Map

- Create `src/mini_code_agent/policy/models.py`: decision, risk, rule, request, preview DTOs.
- Create `src/mini_code_agent/policy/engine.py`: deterministic first-match policy evaluation.
- Create `src/mini_code_agent/policy/approval.py`: async approval protocol and deny/static handlers.
- Create `src/mini_code_agent/policy/executor.py`: governed validation/policy/approval/dispatch.
- Create `src/mini_code_agent/policy/__init__.py`: stable exports.
- Modify `src/mini_code_agent/tools/base.py`: governed-executor marker and preview protocol.
- Modify `src/mini_code_agent/tools/registry.py`: non-executing call validation and definition/tool lookup.
- Modify `src/mini_code_agent/agent/runtime.py`: accept side effects only from governed executor.
- Modify `src/mini_code_agent/workspace/models.py`: write limits, file snapshot, mutation result.
- Modify `src/mini_code_agent/workspace/boundary.py`: resolve/create/replace and atomic mutation.
- Create `src/mini_code_agent/tools/write_file.py`: create-only or hash-guarded replacement.
- Create `src/mini_code_agent/tools/edit_file.py`: unique literal replacement.
- Create unit tests under `tests/unit/policy`, `tests/unit/workspace`, and `tests/unit/tools`.
- Create `tests/integration/test_governed_write_agent.py`.
- Create ADR/architecture/learning/resume/release evidence.

## Task 1: Policy Domain and Deterministic Engine

**Tests first:**

- decision enum is exactly `allow/ask/deny`;
- risk is `low/medium/high/critical`;
- rules are immutable, bounded, and reject empty matches;
- first matching rule wins;
- tool-name glob, side effect, resource glob, session mode, and trust source match;
- default read/write/execute/network behavior;
- no matching custom rule falls back to secure defaults;
- result includes rule ID and static rationale, not raw arguments.

**Implementation:**

```python
class PolicyDecision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"

class PolicyEngine:
    def evaluate(self, request: PolicyRequest) -> PolicyResult: ...
```

Rules use bounded `fnmatchcase` globs, deterministic tuple order, and no model-controlled regex.

**Gate and commit:**

```powershell
python -m uv run pytest tests/unit/policy/test_engine.py -q
python -m uv run pyright src/mini_code_agent/policy tests/unit/policy
git commit -m "feat: add deterministic policy engine"
```

## Task 2: Approval and Governed Executor

**Tests first:**

- invalid Schema returns before policy and approval;
- `allow` dispatches once;
- `deny` never dispatches;
- `ask` dispatches only after explicit approval;
- non-interactive handler denies;
- approval exception/cancellation behavior is safe;
- preview failure denies;
- IDs remain correlated;
- sink/handler cannot change ToolCall arguments;
- non-read-only definitions expose `governance_enforced=True`.

**Implementation flow:**

```text
ToolCall
  -> registry.validate
  -> tool.preview (bounded ActionPreview)
  -> policy.evaluate
  -> allow | approval.ask | deny
  -> registry.execute_validated
  -> validate correlated bounded ToolResult
```

Cancellation is re-raised. Other approval/preview exceptions become a static permission failure.

**Gate and commit:**

```powershell
python -m uv run pytest tests/unit/policy/test_executor.py -q
git commit -m "feat: govern tool execution"
```

## Task 3: Agent Runtime Governance Contract

**Tests first:**

- plain ToolExecutor with write definition is rejected;
- spoofed truthy values other than literal `True` are rejected;
- GovernedToolExecutor with write definitions is accepted;
- existing read-only executors remain accepted;
- deny/approval rejection produces normal Agent ToolResult and next model turn;
- policy cannot be bypassed by direct model ToolCall.

Move the old M1 read-only restriction to:

```python
has_side_effects = any(definition.side_effect is not SideEffect.READ_ONLY ...)
if has_side_effects and getattr(tools, "governance_enforced", None) is not True:
    raise ValueError("Side-effecting tools require governed execution.")
```

The marker is a trusted composition assertion, not a Python security sandbox; public construction
and integration tests prove the intended application wiring.

## Task 4: Workspace Write Resolution and Atomic Mutation

**Tests first:**

- create new UTF-8 file under existing safe parent;
- create refuses existing target;
- replace requires/matches expected SHA-256;
- mismatch reports conflict and leaves bytes unchanged;
- parent traversal/link/junction/`.git`/ADS/device/special target rejected;
- content exact limit/limit-plus-one;
- preserve UTF-8 and requested newline bytes;
- preserve existing permission bits where supported;
- injected failure before replace leaves original intact and removes temp;
- mutation returns before/after hashes and bounded diff;
- absolute host path never appears.

Add:

```python
WorkspaceBoundary.preview_write(...)
WorkspaceBoundary.atomic_write(...)
```

Preview returns an immutable mutation plan containing the expected precondition and diff. Execution
revalidates immediately before replace so approval of one snapshot cannot authorize another.

## Task 5: Write File Tool

Schema:

```json
{
  "type": "object",
  "properties": {
    "path": {"type": "string", "minLength": 1, "maxLength": 1024},
    "content": {"type": "string", "maxLength": 1048576},
    "expected_sha256": {
      "type": ["string", "null"],
      "pattern": "^[0-9a-f]{64}$"
    },
    "reason": {"type": "string", "minLength": 1, "maxLength": 500}
  },
  "required": ["path", "content", "reason"],
  "additionalProperties": false
}
```

No expected hash means create-only. Existing replacement requires the exact current hash.
`preview()` and `execute()` use the same Workspace mutation primitives.

## Task 6: Edit File Tool

Inputs: `path`, `old_text`, `new_text`, `expected_sha256`, and bounded `reason`.

- old text must be non-empty and occur exactly once;
- expected hash is mandatory;
- preview shows bounded unified diff;
- execution recomputes occurrence and hash;
- no-op replacement is rejected;
- newline and encoding are preserved because replacement operates on decoded text then writes exact
  UTF-8 bytes.

## Task 7: End-to-End Governed Write Agent

ScriptedProvider requests:

1. read target;
2. edit with current hash;
3. final answer.

Prove:

- deny mode leaves file unchanged;
- non-interactive ask leaves file unchanged;
- explicit approval writes once;
- stale hash after approval leaves file unchanged;
- result and event sequence remain correlated;
- Agent Runtime itself has no path/policy/write branches.

## Task 8: Documentation, Review, and `v0.5.0-alpha.0`

Document:

- policy precedence/defaults;
- approval UX contract;
- optimistic concurrency and atomic replacement;
- diff truncation;
- why approval is not a sandbox;
- Java optimistic locking / transaction and Flink checkpoint analogies;
- every resume highlight's why, implementation, function, and solved problem.

Run:

```powershell
python -m uv lock --check
python -m uv run ruff format --check .
python -m uv run ruff check .
python -m uv run pyright
python -m uv run --python 3.12 --all-groups pytest -q
python -m uv run --python 3.13 --all-groups pytest --cov
python -m uv run --with bandit bandit -q -r src
python -m uv run --with pip-audit pip-audit
python -m uv build --build-constraint build-constraints.txt --require-hashes
```

Adversarial review covers policy precedence, glob confusion, malformed preview, approval races,
stale hashes, symlink swaps, temp cleanup, permission preservation, CRLF, Unicode, huge diffs,
direct executor bypass, cancellation, absolute-path leakage, and documentation overclaims.

Bump to `0.5.0a0`, run wheel/sdist smoke on Python 3.12/3.13, merge to `main`, and create annotated
tag `v0.5.0-alpha.0`.
