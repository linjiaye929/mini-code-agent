# M2a Read-only Workspace Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a cross-platform Workspace boundary, schema-validating Tool Registry, and bounded Read/Search tools without introducing write or process side effects.

**Architecture:** `WorkspaceBoundary` is the only component allowed to turn model-supplied relative paths into filesystem paths. `ToolRegistry` validates ToolCall arguments against each registered JSON Schema before dispatch. Read/Search receive the boundary by dependency injection, return structured bounded results, and remain compatible with the existing read-only Agent Runtime.

**Tech Stack:** Python 3.12/3.13, `pathlib`, `os`, Pydantic v2, `jsonschema`, Pytest, strict Pyright, Windows/Linux CI.

---

## Scope Split

M2a contains only read-only behavior:

- Workspace path resolution and file policy.
- Tool registration, lookup, argument validation, and normalized result errors.
- `read_file` and literal `search_text`.

M2b will add Policy Engine, approval, write, edit, atomic replacement, backup, and diff evidence.
M2c will add bounded Shell and Git adapters. No M2a implementation may invoke a shell, mutate the
workspace, or silently broaden access.

## File Map

- Create `src/mini_code_agent/workspace/errors.py`: public workspace error codes and safe exception.
- Create `src/mini_code_agent/workspace/models.py`: validated read/search limits and result DTOs.
- Create `src/mini_code_agent/workspace/boundary.py`: root ownership, path resolution, link checks, text/binary/size policy, and bounded traversal.
- Create `src/mini_code_agent/workspace/__init__.py`: stable exports.
- Create `src/mini_code_agent/tools/registry.py`: immutable definition snapshot, JSON Schema validation, dispatch, and safe errors.
- Create `src/mini_code_agent/tools/read_file.py`: line-aware bounded UTF-8 reads.
- Create `src/mini_code_agent/tools/search_text.py`: bounded literal text search with glob filtering.
- Modify `src/mini_code_agent/tools/__init__.py`: stable tool exports.
- Modify `pyproject.toml` and `uv.lock`: add direct bounded `jsonschema` dependency.
- Create `tests/unit/workspace/test_boundary.py`: path, link, size, type, encoding, and traversal tests.
- Create `tests/unit/tools/test_registry.py`: registration, schema, correlation, and failure tests.
- Create `tests/unit/tools/test_read_file.py`: read contracts and truncation.
- Create `tests/unit/tools/test_search_text.py`: search contracts and budgets.
- Create `tests/integration/test_readonly_workspace_agent.py`: ScriptedProvider drives Read/Search through unchanged Agent Runtime.
- Create `docs/adr/0003-workspace-boundary.md`: containment guarantees and non-claims.
- Create `docs/architecture/readonly-tools.md`: data flow and limits.
- Modify learning, resume, README, changelog, and version evidence.

## Security Invariants

1. Tool paths are non-empty workspace-relative POSIX-style strings, even on Windows.
2. Absolute paths, drive-qualified paths, UNC paths, backslashes, NUL, `.`/`..` segments,
   percent-encoded traversal, and paths over 1,024 characters are rejected before filesystem I/O.
3. The selected root must be an existing directory and is resolved once at construction.
4. Every existing path component is checked for symlink or Windows junction/reparse traversal.
5. The final resolved path must remain below the resolved root using `Path.relative_to`, never
   string prefix comparison.
6. Read targets must be regular files. Directories, devices, sockets, and FIFOs are rejected.
7. File size is checked before and during read; configured limit is capped at 16 MiB.
8. Text is UTF-8 with strict decoding. NUL-containing/binary and malformed text is rejected.
9. Directory traversal has maximum visited files, total bytes, results, line length, and depth.
10. Public ToolResult errors expose stable codes and safe messages, never absolute host paths,
    file contents, arbitrary exceptions, or stack traces.
11. Registry definitions are unique and snapshotted. The model cannot register or replace tools.
12. Schema validation, workspace validation, and execution remain separate stages.

## Task 1: Workspace Error and Limit Models

**Files:**
- Create: `src/mini_code_agent/workspace/errors.py`
- Create: `src/mini_code_agent/workspace/models.py`
- Create: `tests/unit/workspace/test_boundary.py`

- [ ] **Step 1: Write failing model tests**

Cover bounded defaults and invalid zero/negative/excessive values:

```python
def test_workspace_limits_have_hard_upper_bounds() -> None:
    with pytest.raises(ValidationError):
        WorkspaceLimits(max_file_bytes=16 * 1024 * 1024 + 1)
    with pytest.raises(ValidationError):
        SearchLimits(max_results=10_001)
```

Define stable error codes for invalid path, outside workspace, link traversal, not found, wrong
file type, too large, binary, invalid encoding, and traversal budget.

- [ ] **Step 2: Verify RED**

```powershell
python -m uv run pytest tests/unit/workspace/test_boundary.py -q
```

Expected: import failure because the workspace package does not exist.

- [ ] **Step 3: Implement immutable Pydantic limits and safe error**

Use frozen, extra-forbid models. `WorkspaceError.__str__` must return only its public message and
must not retain raw exceptions or host paths.

- [ ] **Step 4: Verify GREEN and commit**

```powershell
python -m uv run pytest tests/unit/workspace/test_boundary.py -q
python -m uv run pyright src/mini_code_agent/workspace tests/unit/workspace
git add src/mini_code_agent/workspace tests/unit/workspace
git commit -m "feat: define workspace safety contracts"
```

## Task 2: Workspace Path Boundary

**Files:**
- Create: `src/mini_code_agent/workspace/boundary.py`
- Modify: `tests/unit/workspace/test_boundary.py`

- [ ] **Step 1: Add failing path tests**

Parameterize:

```python
@pytest.mark.parametrize(
    "path",
    [
        "", ".", "..", "../secret", "a/../../secret",
        "/etc/passwd", "C:/Windows/system.ini", r"\\server\share\file",
        r"dir\file", "a/%2e%2e/secret", "bad\0name",
    ],
)
def test_resolve_rejects_untrusted_path_forms(boundary, path) -> None:
    with pytest.raises(WorkspaceError):
        boundary.resolve_file(path)
```

Add case-insensitive Windows drive/UNC cases without making Linux assertions platform-dependent.
Assert public errors never contain the workspace's absolute path.

- [ ] **Step 2: Add failing symlink/junction tests**

Create links to both inside and outside targets where privileges permit; reject both so path
meaning cannot change through a link. Skip only on an actual OS privilege error. On Python 3.12+
also test `Path.is_junction()` on Windows when a junction fixture is available.

- [ ] **Step 3: Implement lexical and physical containment**

Perform lexical validation first, walk each existing component with `lstat`, reject symlinks and
junctions, resolve strictly, and prove containment with `relative_to(self.root)`. Reject
non-regular final targets.

- [ ] **Step 4: Add race-oriented tests**

Resolve, replace a parent with a link, then resolve again and prove the second call rejects it.
Document that path checks reduce but do not eliminate filesystem TOCTOU; process isolation is a
separate control.

- [ ] **Step 5: Verify and commit**

```powershell
python -m uv run pytest tests/unit/workspace/test_boundary.py -q
git add src/mini_code_agent/workspace/boundary.py tests/unit/workspace/test_boundary.py
git commit -m "feat: enforce workspace path boundary"
```

## Task 3: Bounded Text Reads and Traversal

**Files:**
- Modify: `src/mini_code_agent/workspace/boundary.py`
- Modify: `src/mini_code_agent/workspace/models.py`
- Modify: `tests/unit/workspace/test_boundary.py`

- [ ] **Step 1: Add failing file-policy tests**

Cover exact-limit and limit-plus-one files, growth between metadata/read, UTF-8 BOM, malformed
UTF-8, NUL bytes, empty file, directory, FIFO/socket where supported, very long lines, and
absolute-path redaction.

- [ ] **Step 2: Implement bounded binary reads then strict text decode**

Open only after validation, read at most `limit + 1`, reject overflow, NUL, and decoding errors.
Return a DTO containing workspace-relative path, text, byte count, and line count.

- [ ] **Step 3: Add bounded tree iteration**

Traversal must be deterministic by normalized relative path, skip `.git`, reject linked
directories, and stop with a typed budget error when file/depth/byte limits are reached.

- [ ] **Step 4: Verify and commit**

```powershell
python -m uv run pytest tests/unit/workspace/test_boundary.py -q
git add src/mini_code_agent/workspace tests/unit/workspace
git commit -m "feat: add bounded workspace reads"
```

## Task 4: Schema-validating Tool Registry

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/mini_code_agent/tools/registry.py`
- Create: `tests/unit/tools/test_registry.py`

- [ ] **Step 1: Add failing registry tests**

Cover duplicate definitions, invalid Draft 2020-12 schemas, unknown tools, missing/extra/wrong
arguments, nested validation paths, executor exception redaction, invalid result type, result ID
mismatch, and immutable definition snapshots.

- [ ] **Step 2: Verify RED and add dependency**

```powershell
python -m uv run pytest tests/unit/tools/test_registry.py -q
python -m uv add "jsonschema>=4.23,<5"
```

- [ ] **Step 3: Implement registry**

Each registered tool exposes one `ToolDefinition` and `execute(ToolCall)`. At construction:

- validate every schema with `Draft202012Validator.check_schema`;
- reject duplicate names;
- compile one validator per tool;
- snapshot definitions in deterministic registration order.

At execution, return correlated JSON ToolResults with codes `unknown_tool`,
`invalid_arguments`, `tool_failed`, `invalid_tool_result`, or the successful content. Never pass
raw `ValidationError`, arguments, or exception text to the model.

- [ ] **Step 4: Verify and commit**

```powershell
python -m uv run pytest tests/unit/tools/test_registry.py -q
python -m uv run pyright src/mini_code_agent/tools/registry.py tests/unit/tools/test_registry.py
git add pyproject.toml uv.lock src/mini_code_agent/tools/registry.py tests/unit/tools/test_registry.py
git commit -m "feat: add schema-validating tool registry"
```

## Task 5: Read File Tool

**Files:**
- Create: `src/mini_code_agent/tools/read_file.py`
- Create: `tests/unit/tools/test_read_file.py`

- [ ] **Step 1: Add failing tool tests**

Schema:

```json
{
  "type": "object",
  "properties": {
    "path": {"type": "string", "minLength": 1, "maxLength": 1024},
    "start_line": {"type": "integer", "minimum": 1, "maximum": 10000000},
    "max_lines": {"type": "integer", "minimum": 1, "maximum": 2000}
  },
  "required": ["path"],
  "additionalProperties": false
}
```

Test full read, line window, EOF, empty file, truncation, Unicode, oversized/binary/escaped path,
and deterministic JSON output. No output may contain the absolute root.

- [ ] **Step 2: Implement through WorkspaceBoundary only**

Return compact JSON with `path`, `start_line`, `end_line`, `total_lines`, `content`, and
`truncated`. Never call `Path.read_text()` directly in the tool.

- [ ] **Step 3: Verify and commit**

```powershell
python -m uv run pytest tests/unit/tools/test_read_file.py -q
git add src/mini_code_agent/tools/read_file.py tests/unit/tools/test_read_file.py
git commit -m "feat: add bounded read file tool"
```

## Task 6: Literal Search Tool

**Files:**
- Create: `src/mini_code_agent/tools/search_text.py`
- Create: `tests/unit/tools/test_search_text.py`

- [ ] **Step 1: Add failing search tests**

Inputs are `query`, optional relative `path`, optional glob, case sensitivity, and bounded
`max_results`. Use literal search, not model-supplied regex. Cover deterministic order, Unicode,
multiple matches per line, long-line truncation, hidden files, `.git` exclusion, binary/invalid
files, linked directories, result budget, traversal budget, and no matches.

- [ ] **Step 2: Implement bounded search**

Use WorkspaceBoundary traversal and reads. Return compact JSON:

```json
{
  "query": "needle",
  "matches": [{"path": "src/a.py", "line": 3, "column": 8, "preview": "..."}],
  "files_scanned": 4,
  "truncated": false
}
```

Cap query length, preview length, line length, files, bytes, and results. Sort paths before scan
and preserve match order within each file.

- [ ] **Step 3: Verify and commit**

```powershell
python -m uv run pytest tests/unit/tools/test_search_text.py -q
git add src/mini_code_agent/tools/search_text.py tests/unit/tools/test_search_text.py
git commit -m "feat: add bounded literal search tool"
```

## Task 7: Agent Integration and Public Exports

**Files:**
- Modify: `src/mini_code_agent/tools/__init__.py`
- Modify: `src/mini_code_agent/workspace/__init__.py`
- Create: `tests/integration/test_readonly_workspace_agent.py`

- [ ] **Step 1: Add failing unchanged-runtime integration**

Drive `read_file`, then `search_text`, then a final answer through `ScriptedProvider`. Assert both
calls pass registry schema validation, remain inside a temporary workspace, correlate results,
and complete through the existing `AgentRuntime` without runtime-specific branches.

- [ ] **Step 2: Export stable types and verify**

```powershell
python -m uv run pytest tests/integration/test_readonly_workspace_agent.py tests/integration/test_agent_loop.py -q
git add src/mini_code_agent/tools/__init__.py src/mini_code_agent/workspace/__init__.py tests/integration/test_readonly_workspace_agent.py
git commit -m "test: integrate read-only workspace tools"
```

## Task 8: Documentation, Review, and Alpha Release

**Files:**
- Create: `docs/adr/0003-workspace-boundary.md`
- Create: `docs/architecture/readonly-tools.md`
- Modify: `docs/architecture/threat-model.md`
- Modify: `docs/learning/knowledge-map.md`
- Modify: `docs/learning/progress.md`
- Modify: `docs/resume/project-profile.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`
- Modify version assertions.

- [ ] **Step 1: Document guarantees and non-claims**

Explain lexical vs physical containment, symlink/junction handling, TOCTOU residual risk,
encoding/binary policy, registry validation, bounded search, and why Workspace checks are not an
OS sandbox.

- [ ] **Step 2: Add learning and resume evidence**

Teach `pathlib`, `lstat`, link/junction behavior, JSON Schema, registry dispatch, bounded
traversal, Java NIO analogies, and Flink state/budget analogies. For each resume highlight record
why, implementation, function, solved problem, and measured test evidence.

- [ ] **Step 3: Run full quality and security gates**

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

- [ ] **Step 4: Run adversarial review**

Check Windows path forms, case folding, UNC/device paths, junctions, symlink races, special files,
large files, malformed Unicode, hidden files, `.git`, absolute-path leakage, schema bypass,
duplicate registration, result correlation, deterministic ordering, and false documentation
claims. Every fix starts with a failing regression test.

- [ ] **Step 5: Release**

Bump to `0.4.0a0`, record exact evidence, build and smoke wheel/sdist on Python 3.12/3.13,
fast-forward merge to `main`, and create annotated tag `v0.4.0-alpha.0`.
