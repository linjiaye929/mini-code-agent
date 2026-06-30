# M5a Governed Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [x]`) syntax for tracking.

**Goal:** Discover bounded source-qualified `SKILL.md` files, expose metadata without bodies, and
load revalidated untrusted Markdown through two read-only Agent tools.

**Architecture:** Add an inert `skills` package with strict Pydantic contracts, a restricted
PyYAML parser, hardened direct-child filesystem discovery, immutable catalog state, and
fingerprint-checked lazy loading. Skills remain data and cannot register executable capabilities.

**Tech Stack:** Python 3.12/3.13, Pydantic v2, PyYAML `SafeLoader`, pathlib/os/stat, SHA-256,
existing Tool Registry, Pytest, Ruff, strict Pyright.

---

## File Map

**Create**

- `src/mini_code_agent/skills/__init__.py`: stable public Skill API.
- `src/mini_code_agent/skills/models.py`: immutable source, metadata, descriptor, issue, report,
  and loaded-content models.
- `src/mini_code_agent/skills/parser.py`: bounded strict YAML-frontmatter and Markdown parser.
- `src/mini_code_agent/skills/catalog.py`: hardened discovery, conflict quarantine, disable set,
  and drift-checked loading.
- `src/mini_code_agent/skills/tools.py`: `list_skills` and `load_skill` read-only tools.
- `tests/unit/skills/test_skill_models.py`: model bounds and invariants.
- `tests/unit/skills/test_parser.py`: document syntax, YAML safety, and size tests.
- `tests/unit/skills/test_catalog.py`: filesystem, source, conflict, disable, and drift tests.
- `tests/unit/skills/test_tools.py`: Tool schemas and public result tests.
- `tests/integration/test_governed_skills_agent.py`: Fake Provider list/load and Policy non-bypass.

**Modify**

- `pyproject.toml`: add PyYAML runtime and type-stub development dependencies.
- `uv.lock`: lock the new dependencies.
- `tests/smoke_test.py`: import the stable Skill API.

### Task 1: Define Skill contracts

**Files:**
- Create: `src/mini_code_agent/skills/models.py`
- Create: `tests/unit/skills/test_skill_models.py`

- [x] **Step 1: Write failing model tests**

Cover source-to-trust derivation, qualified identity, exact metadata fields, semantic versions,
descriptor bounds, issue privacy, report uniqueness, and frozen collections:

```python
def test_descriptor_derives_qualified_identity_and_trust() -> None:
    descriptor = descriptor_for(source=SkillSource.PROJECT, name="review-python")
    assert descriptor.skill_id == "project:review-python"
    assert descriptor.trust is SkillTrust.UNTRUSTED_PROJECT


def test_metadata_rejects_unknown_fields_and_non_semver() -> None:
    with pytest.raises(ValidationError):
        SkillMetadata.model_validate(
            {
                "name": "review-python",
                "description": "Review Python.",
                "version": "latest",
                "model_invocable": True,
            }
        )
```

- [x] **Step 2: Run tests and verify collection fails**

Run: `uv run pytest tests/unit/skills/test_skill_models.py -q`

Expected: FAIL because `mini_code_agent.skills.models` does not exist.

- [x] **Step 3: Implement immutable contracts**

Implement these public shapes with `extra="forbid"` and `frozen=True`:

```python
class SkillSource(StrEnum):
    MANAGED = "managed"
    USER = "user"
    PROJECT = "project"


class SkillTrust(StrEnum):
    MANAGED = "managed"
    USER = "user"
    UNTRUSTED_PROJECT = "untrusted_project"


class SkillMetadata(BaseModel):
    name: Annotated[str, Field(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")]
    description: Annotated[str, Field(min_length=1, max_length=500)]
    version: Annotated[str, Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")]
    model_invocable: bool = True
```

Add `SkillRoot`, `SkillDescriptor`, `LoadedSkill`, `SkillIssueCode`, `SkillIssue`, and
`SkillDiscoveryReport`. Derive trust in host code; never accept it from YAML.

- [x] **Step 4: Run focused tests**

Run: `uv run pytest tests/unit/skills/test_skill_models.py -q`

Expected: PASS.

- [x] **Step 5: Commit contracts**

```powershell
git add src/mini_code_agent/skills/models.py tests/unit/skills/test_skill_models.py
git commit -m "feat: define governed skill contracts"
```

### Task 2: Parse bounded `SKILL.md` documents

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/mini_code_agent/skills/parser.py`
- Create: `tests/unit/skills/test_parser.py`

- [x] **Step 1: Add parser dependency and refresh the lock**

Add:

```toml
dependencies = [
    # existing dependencies
    "pyyaml>=6.0.2,<7",
]

[dependency-groups]
dev = [
    # existing dependencies
    "types-pyyaml>=6.0.12.20250516,<7",
]
```

Run: `uv lock`

Expected: lock succeeds with PyYAML and its stubs.

- [x] **Step 2: Write failing parser tests**

Test valid LF/CRLF input and reject BOM, invalid UTF-8, missing delimiters, empty body, excessive
frontmatter/body/file sizes, duplicate keys, aliases, custom tags, non-mapping YAML, unknown
fields, and metadata/name mismatch:

```python
def test_parser_rejects_duplicate_yaml_keys() -> None:
    raw = skill_bytes(
        "name: review-python\nname: shadowed\n"
        "description: Review Python.\nversion: 1.0.0"
    )
    with pytest.raises(SkillParseError) as caught:
        parse_skill_document(raw, directory_name="review-python")
    assert caught.value.code is SkillIssueCode.INVALID_FRONTMATTER
```

- [x] **Step 3: Run tests and verify failure**

Run: `uv run pytest tests/unit/skills/test_parser.py -q`

Expected: FAIL because the parser is absent.

- [x] **Step 4: Implement restricted YAML and parser**

Use a `yaml.SafeLoader` subclass. Override `compose_node` to reject aliases before construction
and install a mapping constructor that rejects duplicate and non-string keys. Expose:

```python
class SkillParseError(ValueError):
    def __init__(self, code: SkillIssueCode) -> None: ...


class ParsedSkill(NamedTuple):
    metadata: SkillMetadata
    body: str
    sha256: str
    byte_count: int


def parse_skill_document(
    raw: bytes,
    *,
    directory_name: str,
    max_file_bytes: int = 262_144,
    max_frontmatter_bytes: int = 32_768,
    max_body_chars: int = 131_072,
) -> ParsedSkill: ...
```

Never include parser/YAML exception text in `SkillParseError`.

- [x] **Step 5: Run parser and dependency checks**

Run:

```powershell
uv run pytest tests/unit/skills/test_parser.py -q
uv run pyright src/mini_code_agent/skills/parser.py tests/unit/skills/test_parser.py
```

Expected: both pass.

- [x] **Step 6: Commit the parser**

```powershell
git add pyproject.toml uv.lock src/mini_code_agent/skills/parser.py tests/unit/skills/test_parser.py
git commit -m "feat: parse bounded skill documents"
```

### Task 3: Discover Skills through a hardened catalog

**Files:**
- Create: `src/mini_code_agent/skills/catalog.py`
- Create: `tests/unit/skills/test_catalog.py`

- [x] **Step 1: Write failing discovery tests**

Use temporary roots to test direct-child discovery, deterministic ordering, source-qualified
coexistence, same-source conflict quarantine, malformed entry quarantine, unsafe root rejection,
linked root/directory/file rejection, candidate limits, disabled IDs, and no absolute-path leak:

```python
def test_cross_source_names_coexist_without_shadowing(tmp_path: Path) -> None:
    user = make_root(tmp_path / "user", SkillSource.USER, "user-root")
    project = make_root(tmp_path / "project", SkillSource.PROJECT, "project-root")
    write_skill(user.path, "review-python")
    write_skill(project.path, "review-python")

    catalog, report = SkillCatalog.discover((user, project))

    assert tuple(item.skill_id for item in report.skills) == (
        "project:review-python",
        "user:review-python",
    )
    assert catalog.descriptor("project:review-python") is not None
```

On Windows, create symlinks only when supported and mark capability-dependent assertions with a
skip reason. Also detect reparse points through `st_file_attributes`.

- [x] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/unit/skills/test_catalog.py -q`

Expected: FAIL because `SkillCatalog` does not exist.

- [x] **Step 3: Implement path-chain validation and discovery**

Implement:

```python
class SkillCatalog:
    @classmethod
    def discover(
        cls,
        roots: Iterable[SkillRoot],
        *,
        disabled_ids: Iterable[str] = (),
        max_roots: int = 8,
        max_candidates: int = 128,
    ) -> tuple[SkillCatalog, SkillDiscoveryReport]: ...

    @property
    def report(self) -> SkillDiscoveryReport: ...

    def descriptor(self, skill_id: str) -> SkillDescriptor | None: ...
```

Validate every root, direct child, and `SKILL.md` with `lstat`; reject symlink/reparse and
non-directory/non-regular objects. Keep absolute `Path` values only in private catalog entries.
Sort by source, name, and root ID before conflict processing so filesystem iteration order cannot
change results.

- [x] **Step 4: Run catalog tests on the current platform**

Run:

```powershell
uv run pytest tests/unit/skills/test_catalog.py -q
uv run pyright src/mini_code_agent/skills/catalog.py tests/unit/skills/test_catalog.py
```

Expected: pass, with only explicit platform capability skips.

- [x] **Step 5: Commit discovery**

```powershell
git add src/mini_code_agent/skills/catalog.py tests/unit/skills/test_catalog.py
git commit -m "feat: discover provenance-aware skills"
```

### Task 4: Revalidate and lazily load content

**Files:**
- Modify: `src/mini_code_agent/skills/catalog.py`
- Modify: `tests/unit/skills/test_catalog.py`

- [x] **Step 1: Add failing load and drift tests**

Cover exact expected SHA, unknown/disabled/non-model-invocable IDs, metadata drift, body drift,
file replacement, linked replacement, deleted file, and successful unchanged load:

```python
def test_load_rejects_content_drift_until_rediscovery(tmp_path: Path) -> None:
    catalog, descriptor = discovered_skill(tmp_path)
    write_skill_body(tmp_path, "changed instructions")

    with pytest.raises(SkillLoadError) as caught:
        catalog.load(descriptor.skill_id, expected_sha256=descriptor.sha256)

    assert caught.value.code is SkillIssueCode.SKILL_CHANGED
```

- [x] **Step 2: Run the focused failure**

Run: `uv run pytest tests/unit/skills/test_catalog.py -q -k load`

Expected: FAIL because loading is absent.

- [x] **Step 3: Implement fail-closed loading**

Add:

```python
class SkillLoadError(ValueError):
    def __init__(self, code: SkillIssueCode) -> None: ...


def load(self, skill_id: str, *, expected_sha256: str) -> LoadedSkill: ...
```

Require the caller SHA to match the descriptor before filesystem work. Revalidate the complete
path chain, read no more than `max_file_bytes + 1`, parse again, and require metadata, byte count,
and SHA to equal discovery. Return the preserved body only after all checks pass.

- [x] **Step 4: Run full catalog tests**

Run: `uv run pytest tests/unit/skills/test_catalog.py -q`

Expected: PASS.

- [x] **Step 5: Commit lazy loading**

```powershell
git add src/mini_code_agent/skills/catalog.py tests/unit/skills/test_catalog.py
git commit -m "feat: revalidate skill content on load"
```

### Task 5: Expose read-only Agent tools

**Files:**
- Create: `src/mini_code_agent/skills/tools.py`
- Create: `src/mini_code_agent/skills/__init__.py`
- Create: `tests/unit/skills/test_tools.py`
- Modify: `tests/smoke_test.py`

- [x] **Step 1: Write failing Tool tests**

Verify exact JSON schemas, read-only side effects, metadata-only list output, fingerprint-required
load, stable public errors, result provenance, model visibility, and no absolute path/body leak
from listing:

```python
@pytest.mark.asyncio
async def test_load_tool_returns_labelled_untrusted_content(tmp_path: Path) -> None:
    catalog, descriptor = discovered_project_skill(tmp_path)
    tool = LoadSkillTool(catalog)
    result = await tool.execute(
        ToolCall(
            id="load-1",
            name="load_skill",
            arguments={"skill_id": descriptor.skill_id, "expected_sha256": descriptor.sha256},
        )
    )
    payload = json.loads(result.content)
    assert payload["trust"] == "untrusted_project"
    assert payload["content_type"] == "untrusted_markdown"
```

- [x] **Step 2: Run tests and verify failure**

Run: `uv run pytest tests/unit/skills/test_tools.py tests/smoke_test.py -q`

Expected: FAIL because the tools/public exports are absent.

- [x] **Step 3: Implement `ListSkillsTool` and `LoadSkillTool`**

Both definitions use `SideEffect.READ_ONLY`. Validate arguments again with small Pydantic request
models inside each Tool even though `ToolRegistry` validates the published JSON Schema. Serialize
with `ensure_ascii=True`, compact separators, sorted keys, and stable error envelopes.

- [x] **Step 4: Run Tool contract tests**

Run:

```powershell
uv run pytest tests/unit/skills/test_tools.py tests/unit/tools/test_registry.py tests/smoke_test.py -q
uv run pyright src/mini_code_agent/skills tests/unit/skills
```

Expected: PASS.

- [x] **Step 5: Commit the Tool API**

```powershell
git add src/mini_code_agent/skills tests/unit/skills/test_tools.py tests/smoke_test.py
git commit -m "feat: expose skills through read-only tools"
```

### Task 6: Prove Agent loading and Policy non-bypass

**Files:**
- Create: `tests/integration/test_governed_skills_agent.py`

- [x] **Step 1: Write the end-to-end tests**

Create a project Skill whose Markdown asks the model to write outside policy. Use `FakeProvider`
responses to call `list_skills`, call `load_skill` with the observed hash, then attempt the
governed write. Assert:

- metadata is listed before body content appears;
- loaded content is labelled `untrusted_project`;
- the write receives `permission_denied`;
- no target file is created;
- the run completes with correlated Tool results.

- [x] **Step 2: Run the integration test**

Run: `uv run pytest tests/integration/test_governed_skills_agent.py -q`

Expected: PASS after composing the existing `AgentRuntime`, `FakeProvider`, `ToolRegistry`,
`GovernedToolExecutor`, and a deny Policy rule.

- [x] **Step 3: Run Skills regression and static checks**

Run:

```powershell
uv run pytest tests/unit/skills tests/integration/test_governed_skills_agent.py -q
uv run ruff format --check src tests
uv run ruff check src tests
uv run pyright
```

Expected: all pass.

- [x] **Step 4: Commit integration evidence**

```powershell
git add tests/integration/test_governed_skills_agent.py
git commit -m "test: prove governed skill loading"
```

### Task 7: Review the Skills implementation

**Files:**
- Review all files changed by Tasks 1-6.

- [x] **Step 1: Run the full suite with branch coverage**

Run:

```powershell
uv run pytest --cov=mini_code_agent --cov-branch --cov-report=term-missing
```

Expected: all tests pass and total coverage is at least 85%.

- [x] **Step 2: Run security and package gates**

Run:

```powershell
uv run bandit -q -r src
uv export --locked --no-dev --format requirements.txt -o build/runtime-requirements.txt
uv run pip-audit -r build/runtime-requirements.txt
uv build
```

Expected: all commands pass.

- [x] **Step 3: Inspect the diff for contract leaks**

Run:

```powershell
git diff main...HEAD --check
git diff main...HEAD -- src/mini_code_agent/skills tests/unit/skills tests/integration/test_governed_skills_agent.py
```

Expected: no path, exception, body-listing, precedence, or executable-capability leak.

- [x] **Step 4: Record any review fixes as a focused commit**

Use `fix: harden governed skills` only when review changes are required. Leave the branch clean.
