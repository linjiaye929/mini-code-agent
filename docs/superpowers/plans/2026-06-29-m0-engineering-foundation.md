# M0 Engineering Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible, typed, tested, cross-platform Python project foundation with configuration, secret-safe structured logging, a diagnostic CLI, documentation, and CI.

**Architecture:** M0 establishes a small `src`-layout package. Configuration loading is explicit and follows `defaults < TOML < environment < CLI overrides`; logging is standard-library based with recursive redaction; the CLI depends on typed configuration and diagnostics services rather than embedding their logic.

**Tech Stack:** Python 3.12/3.13, uv 0.11.25, Hatchling, Pydantic v2, pydantic-settings, Platformdirs, Typer, Rich, Pytest, Coverage, Ruff, Pyright, GitHub Actions.

---

## File Map

| Path | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, dependencies, quality tool configuration |
| `.python-version` | Local default Python version |
| `.gitignore` | Worktree, generated, secret, cache, build and runtime exclusions |
| `README.md` | Product status, install and M0 usage |
| `config.example.toml` | Safe file-based configuration example |
| `.env.example` | Environment variable names without secret values |
| `src/mini_code_agent/__init__.py` | Package version export |
| `src/mini_code_agent/__main__.py` | `python -m mini_code_agent` entry point |
| `src/mini_code_agent/config.py` | Typed settings and precedence-aware loader |
| `src/mini_code_agent/logging.py` | JSON logging and recursive secret redaction |
| `src/mini_code_agent/diagnostics.py` | Side-effect-free runtime health report |
| `src/mini_code_agent/cli.py` | Typer CLI composition |
| `tests/unit/test_package.py` | Package/version contract |
| `tests/unit/test_config.py` | Configuration precedence and secret safety |
| `tests/unit/test_logging.py` | Structured logging and redaction |
| `tests/unit/test_diagnostics.py` | Runtime support and writable-path checks |
| `tests/cli/test_cli.py` | CLI version, doctor and error exit contracts |
| `tests/smoke_test.py` | Installed package smoke test |
| `.github/workflows/ci.yml` | Quality and Windows/Linux test matrix |
| `SECURITY.md` | Supported security posture and reporting |
| `CONTRIBUTING.md` | Local development and quality commands |
| `CHANGELOG.md` | SemVer change history |
| `docs/adr/0001-framework-light-core.md` | First architecture decision |
| `docs/architecture/threat-model.md` | Initial trust boundaries and non-claims |
| `docs/learning/progress.md` | Learning unit status and evidence links |

## Task 1: Bootstrap a Reproducible Package

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Modify: `.gitignore`
- Create: `README.md`
- Create: `tests/unit/test_package.py`
- Create: `src/mini_code_agent/__init__.py`
- Create: `src/mini_code_agent/__main__.py`

- [ ] **Step 1: Install uv and a managed Python 3.13**

Run from PowerShell:

```powershell
python -m pip install uv==0.11.25
uv python install 3.13
uv --version
uv run --python 3.13 python --version
```

Expected:

```text
uv 0.11.25
Python 3.13.x
```

- [ ] **Step 2: Create project metadata and tool configuration**

Create `pyproject.toml`:

```toml
[project]
name = "mini-code-agent"
version = "0.1.0a0"
description = "A framework-light, provider-neutral, enterprise-grade mini code agent."
readme = "README.md"
requires-python = ">=3.12,<3.14"
license = "Apache-2.0"
authors = [
    { name = "Lin Jiaye" },
]
classifiers = [
    "Development Status :: 2 - Pre-Alpha",
    "Environment :: Console",
    "License :: OSI Approved :: Apache Software License",
    "Operating System :: Microsoft :: Windows",
    "Operating System :: POSIX :: Linux",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Typing :: Typed",
]
dependencies = [
    "platformdirs>=4.3,<5",
    "pydantic>=2.10,<3",
    "pydantic-settings>=2.7,<3",
    "rich>=13.9,<15",
    "typer>=0.15,<1",
]

[project.scripts]
mini-code-agent = "mini_code_agent.cli:app"

[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[dependency-groups]
dev = [
    "build>=1.2,<2",
    "pyright>=1.1.390",
    "pytest>=8.3,<10",
    "pytest-cov>=6,<8",
    "ruff>=0.9",
]

[tool.hatch.build.targets.wheel]
packages = ["src/mini_code_agent"]

[tool.pytest.ini_options]
addopts = ["-ra", "--strict-config", "--strict-markers"]
testpaths = ["tests"]

[tool.coverage.run]
branch = true
source = ["mini_code_agent"]

[tool.coverage.report]
fail_under = 85
show_missing = true
skip_covered = true

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
line-ending = "lf"

[tool.pyright]
include = ["src", "tests"]
exclude = [".venv", "build", "dist"]
pythonVersion = "3.12"
pythonPlatform = "All"
typeCheckingMode = "strict"
reportMissingTypeStubs = false
```

Create `.python-version`:

```text
3.13
```

Create `.gitignore`:

```gitignore
.worktrees/
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.coverage
htmlcov/
.pyright/
build/
dist/
*.egg-info/
.env
*.log
.mini-code-agent/
.idea/
.vscode/
```

Create the bootstrap `README.md` required by the build backend:

```markdown
# Mini CodeAgent

> Status: pre-alpha engineering foundation.

A framework-light, provider-neutral coding agent built from first principles.
```

- [ ] **Step 3: Write the failing package contract test**

Create `tests/unit/test_package.py`:

```python
from mini_code_agent import __version__


def test_package_exports_release_version() -> None:
    assert __version__ == "0.1.0a0"
```

- [ ] **Step 4: Sync dependencies and verify the test fails**

Run:

```powershell
uv lock
uv sync --all-groups --no-install-project
uv run --no-sync pytest tests/unit/test_package.py -v
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'mini_code_agent'`.

- [ ] **Step 5: Add the minimal package implementation**

Create `src/mini_code_agent/__init__.py`:

```python
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mini-code-agent")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
```

Create `src/mini_code_agent/__main__.py`:

```python
from mini_code_agent.cli import app

if __name__ == "__main__":
    app()
```

- [ ] **Step 6: Run the package test**

Run:

```powershell
uv sync --all-groups
uv run --no-sync pytest tests/unit/test_package.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit the package foundation**

```powershell
git add pyproject.toml uv.lock .python-version .gitignore README.md src tests/unit/test_package.py
git commit -m "build: bootstrap typed Python package"
```

## Task 2: Implement Typed Configuration and Secret Safety

**Files:**
- Create: `tests/unit/test_config.py`
- Create: `src/mini_code_agent/config.py`

- [ ] **Step 1: Write configuration contract tests**

Create `tests/unit/test_config.py`:

```python
from pathlib import Path

import pytest

from mini_code_agent.config import (
    AppSettings,
    ConfigurationError,
    LogLevel,
    load_settings,
)


@pytest.fixture(autouse=True)
def clear_project_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MINI_CODE_AGENT_LOG_LEVEL",
        "MINI_CODE_AGENT_DATA_DIR",
        "MINI_CODE_AGENT_TRACE_ENABLED",
        "MINI_CODE_AGENT_ANTHROPIC_API_KEY",
        "MINI_CODE_AGENT_OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_valid_without_a_config_file(tmp_path: Path) -> None:
    settings = load_settings(config_path=tmp_path / "missing.toml")

    assert settings.log_level is LogLevel.INFO
    assert settings.trace_enabled is True
    assert settings.anthropic_api_key is None
    assert settings.openai_api_key is None


def test_precedence_is_defaults_then_file_then_env_then_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[mini_code_agent]
log_level = "warning"
trace_enabled = false
data_dir = "from-file"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("MINI_CODE_AGENT_LOG_LEVEL", "error")
    monkeypatch.setenv("MINI_CODE_AGENT_DATA_DIR", str(tmp_path / "from-env"))

    settings = load_settings(
        config_path=config_path,
        overrides={"log_level": "debug"},
    )

    assert settings.log_level is LogLevel.DEBUG
    assert settings.trace_enabled is False
    assert settings.data_dir == tmp_path / "from-env"


def test_safe_dict_never_contains_secret_values(tmp_path: Path) -> None:
    settings = AppSettings.model_validate(
        {
            "data_dir": tmp_path,
            "anthropic_api_key": "anthropic-secret",
            "openai_api_key": "openai-secret",
        }
    )

    payload = settings.safe_dict()
    rendered = str(payload)

    assert "anthropic-secret" not in rendered
    assert "openai-secret" not in rendered
    assert payload["anthropic_api_key_configured"] is True
    assert payload["openai_api_key_configured"] is True


def test_invalid_toml_section_has_an_actionable_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("mini_code_agent = 42", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="must be a TOML table"):
        load_settings(config_path=config_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
uv run pytest tests/unit/test_config.py -v
```

Expected: FAIL during collection because `mini_code_agent.config` does not exist.

- [ ] **Step 3: Implement explicit configuration precedence**

Create `src/mini_code_agent/config.py`:

```python
from __future__ import annotations

import tomllib
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

from platformdirs import user_config_path, user_data_path
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(ValueError):
    """Raised when application configuration cannot be loaded safely."""


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


def default_data_dir() -> Path:
    return user_data_path("mini-code-agent", appauthor=False)


def default_config_path() -> Path:
    return user_config_path("mini-code-agent", appauthor=False) / "config.toml"


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    log_level: LogLevel = LogLevel.INFO
    data_dir: Path = Field(default_factory=default_data_dir)
    trace_enabled: bool = True
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    def safe_dict(self) -> dict[str, object]:
        return {
            "log_level": self.log_level.value,
            "data_dir": str(self.data_dir),
            "trace_enabled": self.trace_enabled,
            "anthropic_api_key_configured": self.anthropic_api_key is not None,
            "openai_api_key_configured": self.openai_api_key is not None,
        }


class EnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MINI_CODE_AGENT_",
        extra="ignore",
        case_sensitive=False,
    )

    log_level: LogLevel | None = None
    data_dir: Path | None = None
    trace_enabled: bool | None = None
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"Unable to read configuration {path}: {exc}") from exc

    section = document.get("mini_code_agent", {})
    if not isinstance(section, dict):
        raise ConfigurationError(
            f"Configuration section 'mini_code_agent' in {path} must be a TOML table."
        )
    return section


def load_settings(
    *,
    config_path: Path | None = None,
    overrides: Mapping[str, object] | None = None,
) -> AppSettings:
    path = config_path or default_config_path()
    file_values = _read_toml(path)
    environment = EnvironmentSettings()
    environment_values = environment.model_dump(exclude_none=True)
    merged = {
        **file_values,
        **environment_values,
        **dict(overrides or {}),
    }
    try:
        return AppSettings.model_validate(merged)
    except ValidationError as exc:
        raise ConfigurationError(f"Invalid application configuration: {exc}") from exc
```

- [ ] **Step 4: Run configuration tests**

Run:

```powershell
uv run pytest tests/unit/test_config.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Run static checks for the new module**

Run:

```powershell
uv run ruff check src/mini_code_agent/config.py tests/unit/test_config.py
uv run pyright src/mini_code_agent/config.py tests/unit/test_config.py
```

Expected: both commands exit 0.

- [ ] **Step 6: Commit configuration**

```powershell
git add src/mini_code_agent/config.py tests/unit/test_config.py
git commit -m "feat: add typed configuration loading"
```

## Task 3: Add Structured Logging with Recursive Redaction

**Files:**
- Create: `tests/unit/test_logging.py`
- Create: `src/mini_code_agent/logging.py`

- [ ] **Step 1: Write logging and redaction tests**

Create `tests/unit/test_logging.py`:

```python
import json
from io import StringIO

from pydantic import SecretStr

from mini_code_agent.logging import configure_logging, redact


def test_redact_masks_sensitive_keys_recursively() -> None:
    payload = {
        "api_key": "secret-a",
        "nested": {
            "authorization": "Bearer secret-b",
            "safe": 7,
        },
        "items": [{"token": "secret-c"}],
        "secret_object": SecretStr("secret-d"),
    }

    redacted = redact(payload)
    rendered = str(redacted)

    assert "secret-a" not in rendered
    assert "secret-b" not in rendered
    assert "secret-c" not in rendered
    assert "secret-d" not in rendered
    assert isinstance(redacted, dict)
    nested = redacted["nested"]
    assert isinstance(nested, dict)
    assert nested["safe"] == 7


def test_json_log_contains_safe_event_data() -> None:
    stream = StringIO()
    logger = configure_logging("info", stream=stream)

    logger.info(
        "provider request",
        extra={
            "event_data": {
                "provider": "anthropic",
                "api_key": "must-not-appear",
            }
        },
    )

    event = json.loads(stream.getvalue())
    assert event["level"] == "INFO"
    assert event["message"] == "provider request"
    assert event["data"]["provider"] == "anthropic"
    assert event["data"]["api_key"] == "***"
    assert "must-not-appear" not in stream.getvalue()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
uv run pytest tests/unit/test_logging.py -v
```

Expected: FAIL during collection because `mini_code_agent.logging` does not exist.

- [ ] **Step 3: Implement JSON logging and redaction**

Create `src/mini_code_agent/logging.py`:

```python
from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, TextIO

from pydantic import SecretStr

LOGGER_NAME = "mini_code_agent"
MASK = "***"
SENSITIVE_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold()
    return any(fragment in normalized for fragment in SENSITIVE_FRAGMENTS)


def redact(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _is_sensitive_key(key):
        return MASK
    if isinstance(value, SecretStr):
        return MASK
    if isinstance(value, Mapping):
        return {
            str(item_key): redact(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        event_data = getattr(record, "event_data", None)
        if event_data is not None:
            payload["data"] = redact(event_data)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    level: str,
    *,
    stream: TextIO | None = None,
) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.setLevel(level.upper())
    logger.propagate = False

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger
```

- [ ] **Step 4: Run logging tests**

Run:

```powershell
uv run pytest tests/unit/test_logging.py -v
```

Expected: 2 tests PASS.

- [ ] **Step 5: Run static checks**

Run:

```powershell
uv run ruff check src/mini_code_agent/logging.py tests/unit/test_logging.py
uv run pyright src/mini_code_agent/logging.py tests/unit/test_logging.py
```

Expected: both commands exit 0. Do not add broad file-level type suppressions to make strict checking pass.

- [ ] **Step 6: Commit structured logging**

```powershell
git add src/mini_code_agent/logging.py tests/unit/test_logging.py
git commit -m "feat: add secret-safe structured logging"
```

## Task 4: Add Diagnostics and the CLI Contract

**Files:**
- Create: `tests/unit/test_diagnostics.py`
- Create: `tests/cli/test_cli.py`
- Create: `src/mini_code_agent/diagnostics.py`
- Create: `src/mini_code_agent/cli.py`

- [ ] **Step 1: Write diagnostic tests**

Create `tests/unit/test_diagnostics.py`:

```python
from pathlib import Path

from mini_code_agent.config import AppSettings
from mini_code_agent.diagnostics import (
    build_diagnostic_report,
    is_supported_python,
)


def test_supported_python_range_is_explicit() -> None:
    assert is_supported_python((3, 12)) is True
    assert is_supported_python((3, 13)) is True
    assert is_supported_python((3, 11)) is False
    assert is_supported_python((3, 14)) is False


def test_report_does_not_create_the_data_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "not-created"
    settings = AppSettings.model_validate({"data_dir": data_dir})

    report = build_diagnostic_report(
        settings,
        config_path=tmp_path / "missing.toml",
        python_version=(3, 13),
    )

    assert report.healthy is True
    assert report.data_dir_exists is False
    assert report.data_dir_parent_writable is True
    assert data_dir.exists() is False
```

- [ ] **Step 2: Write CLI contract tests**

Create `tests/cli/test_cli.py`:

```python
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mini_code_agent.cli import app

runner = CliRunner()


def test_version_option_prints_package_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0a0"


def test_doctor_json_never_prints_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINI_CODE_AGENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINI_CODE_AGENT_ANTHROPIC_API_KEY", "must-not-appear")

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["settings"]["anthropic_api_key_configured"] is True
    assert "must-not-appear" not in result.stdout


def test_doctor_returns_configuration_exit_code_two(tmp_path: Path) -> None:
    config_path = tmp_path / "invalid.toml"
    config_path.write_text("mini_code_agent = 7", encoding="utf-8")

    result = runner.invoke(app, ["doctor", "--config", str(config_path), "--json"])

    assert result.exit_code == 2
    assert "must be a TOML table" in result.stderr
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```powershell
uv run pytest tests/unit/test_diagnostics.py tests/cli/test_cli.py -v
```

Expected: FAIL because `mini_code_agent.diagnostics` and `mini_code_agent.cli` do not exist.

- [ ] **Step 4: Implement side-effect-free diagnostics**

Create `src/mini_code_agent/diagnostics.py`:

```python
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from mini_code_agent import __version__
from mini_code_agent.config import AppSettings


class DiagnosticReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    package_version: str
    python_version: str
    python_supported: bool
    platform: str
    config_path: str
    config_file_exists: bool
    data_dir_exists: bool
    data_dir_parent_writable: bool
    settings: dict[str, object]

    @property
    def healthy(self) -> bool:
        return self.python_supported and self.data_dir_parent_writable


def is_supported_python(version: tuple[int, int]) -> bool:
    return (3, 12) <= version < (3, 14)


def _nearest_existing_parent(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def build_diagnostic_report(
    settings: AppSettings,
    *,
    config_path: Path,
    python_version: tuple[int, int] | None = None,
) -> DiagnosticReport:
    runtime_version = python_version or (sys.version_info.major, sys.version_info.minor)
    existing_parent = _nearest_existing_parent(settings.data_dir)
    return DiagnosticReport(
        package_version=__version__,
        python_version=platform.python_version(),
        python_supported=is_supported_python(runtime_version),
        platform=platform.platform(),
        config_path=str(config_path),
        config_file_exists=config_path.exists(),
        data_dir_exists=settings.data_dir.exists(),
        data_dir_parent_writable=os.access(existing_parent, os.W_OK),
        settings=settings.safe_dict(),
    )
```

- [ ] **Step 5: Implement the CLI**

Create `src/mini_code_agent/cli.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mini_code_agent import __version__
from mini_code_agent.config import (
    ConfigurationError,
    default_config_path,
    load_settings,
)
from mini_code_agent.diagnostics import DiagnosticReport, build_diagnostic_report
from mini_code_agent.logging import configure_logging

app = typer.Typer(
    name="mini-code-agent",
    help="Framework-light, provider-neutral coding agent.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
error_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the package version and exit.",
        ),
    ] = False,
) -> None:
    del version


def _render_report(report: DiagnosticReport) -> None:
    table = Table(title="Mini CodeAgent Doctor")
    table.add_column("Check")
    table.add_column("Value")
    table.add_row("Version", report.package_version)
    table.add_row("Python", report.python_version)
    table.add_row("Python supported", str(report.python_supported))
    table.add_row("Platform", report.platform)
    table.add_row("Config", report.config_path)
    table.add_row("Data directory writable", str(report.data_dir_parent_writable))
    console.print(table)


@app.command()
def doctor(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a TOML configuration file."),
    ] = None,
    output_json: Annotated[
        bool,
        typer.Option("--json", help="Emit a machine-readable JSON report."),
    ] = False,
) -> None:
    config_path = config or default_config_path()
    try:
        settings = load_settings(config_path=config_path)
    except ConfigurationError as exc:
        if output_json:
            typer.echo(json.dumps({"error": str(exc)}, ensure_ascii=False), err=True)
        else:
            error_console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    configure_logging(settings.log_level.value)
    report = build_diagnostic_report(settings, config_path=config_path)
    if output_json:
        typer.echo(report.model_dump_json(indent=2))
    else:
        _render_report(report)
    if not report.healthy:
        raise typer.Exit(code=1)
```

- [ ] **Step 6: Run diagnostics and CLI tests**

Run:

```powershell
uv run pytest tests/unit/test_diagnostics.py tests/cli/test_cli.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 7: Run the CLI manually**

Run:

```powershell
uv run mini-code-agent --version
uv run mini-code-agent doctor --json
uv run python -m mini_code_agent --version
```

Expected:

- Both version commands print `0.1.0a0`.
- Doctor prints JSON with `python_supported: true`.
- No API key value appears.

- [ ] **Step 8: Run static checks**

Run:

```powershell
uv run ruff format --check src tests
uv run ruff check src tests
uv run pyright
```

Expected: all commands exit 0.

- [ ] **Step 9: Commit diagnostics and CLI**

```powershell
git add src/mini_code_agent/diagnostics.py src/mini_code_agent/cli.py tests
git commit -m "feat: add diagnostic CLI"
```

## Task 5: Add Governance Documentation and Learning Evidence

**Files:**
- Modify: `README.md`
- Create: `LICENSE`
- Create: `config.example.toml`
- Create: `.env.example`
- Create: `SECURITY.md`
- Create: `CONTRIBUTING.md`
- Create: `CHANGELOG.md`
- Create: `docs/adr/0001-framework-light-core.md`
- Create: `docs/architecture/threat-model.md`
- Create: `docs/learning/progress.md`

- [ ] **Step 1: Create the M0 README**

Create `README.md`:

````markdown
# Mini CodeAgent

A framework-light, provider-neutral coding agent built from first principles.

> Status: pre-alpha. M0 provides the engineering foundation only; it does not yet execute model or file tools.

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
```

## Configuration

Precedence is:

```text
defaults < TOML file < MINI_CODE_AGENT_* environment variables < CLI overrides
```

Default config paths follow the operating system conventions provided by Platformdirs.
Secrets are accepted from environment variables but are never printed by `doctor`.
See `config.example.toml` and `.env.example` for supported inputs.

## Documentation

- Product design: `docs/superpowers/specs/2026-06-29-mini-code-agent-design.md`
- Learning map: `docs/learning/knowledge-map.md`
- Resume evidence: `docs/resume/project-profile.md`
- Threat model: `docs/architecture/threat-model.md`

## License

Apache-2.0
````

- [ ] **Step 2: Create safe configuration examples**

Create `config.example.toml`:

```toml
[mini_code_agent]
log_level = "info"
data_dir = ".mini-code-agent"
trace_enabled = true
```

Create `.env.example`:

```dotenv
MINI_CODE_AGENT_LOG_LEVEL=info
MINI_CODE_AGENT_TRACE_ENABLED=true
MINI_CODE_AGENT_ANTHROPIC_API_KEY=
MINI_CODE_AGENT_OPENAI_API_KEY=
```

The example files must never contain working credentials.

- [ ] **Step 3: Create security and contribution policies**

Create `SECURITY.md`:

```markdown
# Security Policy

## Current support

The project is pre-alpha. Only the latest commit on `main` is supported.

## Reporting

Do not open a public issue for a vulnerability. Use GitHub private vulnerability reporting after the repository is published. Until then, contact the repository owner privately.

## Current boundary

M0 does not execute model-generated tools. Future filesystem, shell, hooks, skills and MCP features are treated as untrusted inputs and must pass independent policy checks.

The project does not claim OS-level sandboxing unless an explicit sandbox backend is enabled and documented.
```

Create `CONTRIBUTING.md`:

````markdown
# Contributing

## Setup

```powershell
uv sync --all-groups
```

## Required checks

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest --cov
uv build
```

Changes to public behavior require tests. Security-sensitive behavior requires negative tests. Architecture changes require an ADR.
````

Create `CHANGELOG.md`:

```markdown
# Changelog

All notable changes follow Keep a Changelog. Versions follow Semantic Versioning.

## [Unreleased]

### Added

- Product design, learning map and resume evidence plan.
- M0 typed package, configuration, structured logging and diagnostic CLI.
```

Create the Apache-2.0 license from the canonical Apache source:

```powershell
Invoke-WebRequest https://www.apache.org/licenses/LICENSE-2.0.txt -OutFile LICENSE
Select-String -Path LICENSE -Pattern "Apache License"
```

Expected: the search prints the `Apache License` heading.

- [ ] **Step 4: Record the architecture decision**

Create `docs/adr/0001-framework-light-core.md`:

```markdown
# ADR 0001: Framework-light Agent Core

## Status

Accepted.

## Context

The learning and product goal requires direct understanding and ownership of the Agent Loop, tool contract, policy decisions and persisted state.

## Decision

The core will not depend on LangGraph or another Agent orchestration framework. Mature libraries remain appropriate for validation, HTTP, CLI, storage and testing. Optional workflow integrations may be added outside the core.

## Consequences

- State transitions and failure semantics remain explicit and testable.
- Provider and tool contracts belong to this project.
- The project carries the cost of maintaining the loop and persistence model.
- Framework-specific integrations cannot bypass policy, trace or session boundaries.
```

- [ ] **Step 5: Write the initial threat model**

Create `docs/architecture/threat-model.md`:

```markdown
# Threat Model

## Protected assets

- User source code and uncommitted changes.
- Files outside the selected workspace.
- API keys and environment secrets.
- Git history and repository integrity.
- Session, checkpoint and trace integrity.

## Untrusted inputs

- Model output and ToolCall arguments.
- Repository files and instructions.
- Skills, hooks and project configuration.
- MCP servers and their tool results.
- Shell output and generated patches.

## Initial controls

- Secret-safe settings and recursive log redaction.
- Explicit configuration precedence.
- No tool execution in M0.
- Future tools must pass Schema, Workspace and Policy checks.
- Trace data must be size-limited and redacted.

## Non-claims

- Regex command filtering is not a sandbox.
- Workspace path checks are not process isolation.
- Human approval does not make malicious code safe.
- MCP connection does not establish trust.
```

- [ ] **Step 6: Create a learning evidence ledger**

Create `docs/learning/progress.md`:

```markdown
# Learning Progress

| Unit | Status | Evidence |
|---|---|---|
| L0 Python engineering foundation | In progress | M0 plan and commits |
| L1 Agent Loop | Not started | |
| L2 Provider and Tool Calling | Not started | |
| L3 Tool Registry | Not started | |
| L4 Workspace and Policy | Not started | |
| L5 File/Edit/Shell/Git tools | Not started | |
| L6 Context Budget | Not started | |
| L7 Session/Checkpoint/Trace | Not started | |
| L8 Git/test/repair | Not started | |
| L9 Skills and Hooks | Not started | |
| L10 MCP | Not started | |
| L11 Subagent and Worktree | Not started | |
| L12 CI, benchmark and release | Not started | |

## L0 Notes

- `uv` owns the project interpreter, lock file and reproducible commands.
- Pydantic validates runtime boundaries; Pyright checks internal contracts statically.
- Configuration precedence is explicit and tested.
- Secret redaction is recursive and independent from caller discipline.
```

- [ ] **Step 7: Check Markdown and repository consistency**

Run:

```powershell
git diff --check
rg -n "FIXME|XXX" README.md SECURITY.md CONTRIBUTING.md CHANGELOG.md config.example.toml .env.example docs
```

Expected:

- `git diff --check` exits 0.
- The search has no new unresolved implementation placeholders. The intentional resume metric blanks in `docs/resume/project-profile.md` remain allowed evidence placeholders.

- [ ] **Step 8: Commit governance documentation**

```powershell
git add README.md LICENSE config.example.toml .env.example SECURITY.md CONTRIBUTING.md CHANGELOG.md docs
git commit -m "docs: add M0 governance and learning evidence"
```

## Task 6: Add Cross-platform CI and Package Smoke Tests

**Files:**
- Create: `tests/smoke_test.py`
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write the installed-package smoke test**

Create `tests/smoke_test.py`:

```python
from typer.testing import CliRunner

from mini_code_agent import __version__
from mini_code_agent.cli import app


def verify_installed_package() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_installed_package_starts() -> None:
    verify_installed_package()


if __name__ == "__main__":
    verify_installed_package()
```

- [ ] **Step 2: Run all local quality gates**

Run:

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest --cov
uv build
```

Expected:

- Ruff format/check exit 0.
- Pyright reports 0 errors.
- Pytest passes and total package coverage is at least 85%.
- `dist/` contains one wheel and one source distribution.

- [ ] **Step 3: Smoke-test built artifacts**

Run:

```powershell
uv run --python 3.13 --isolated --no-project --with (Get-ChildItem dist/*.whl | Select-Object -First 1 -ExpandProperty FullName) tests/smoke_test.py
uv run --python 3.13 --isolated --no-project --with (Get-ChildItem dist/*.tar.gz | Select-Object -First 1 -ExpandProperty FullName) tests/smoke_test.py
```

Expected: both commands exit 0.

- [ ] **Step 4: Add pinned GitHub Actions CI**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
        with:
          persist-credentials: false
      - name: Install uv
        uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b # v8.1.0
        with:
          version: "0.11.25"
          python-version: "3.13"
          enable-cache: true
      - run: uv sync --locked --all-groups
      - run: uv run --no-sync ruff format --check .
      - run: uv run --no-sync ruff check .
      - run: uv run --no-sync pyright
      - run: uv build
      - run: uv run --python 3.13 --isolated --no-project --with dist/*.whl tests/smoke_test.py
      - run: uv run --python 3.13 --isolated --no-project --with dist/*.tar.gz tests/smoke_test.py

  tests:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest]
        python-version: ["3.12", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
        with:
          persist-credentials: false
      - name: Install uv
        uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b # v8.1.0
        with:
          version: "0.11.25"
          python-version: ${{ matrix.python-version }}
          enable-cache: true
      - run: uv sync --locked --all-groups
      - run: uv run --no-sync pytest --cov
```

- [ ] **Step 5: Validate the workflow and lock file**

Run:

```powershell
uv lock --check
git diff --check
git status --short
```

Expected:

- Lock check exits 0.
- Diff check exits 0.
- Only intended M0 CI/smoke files are uncommitted.

- [ ] **Step 6: Commit CI**

```powershell
git add .github/workflows/ci.yml tests/smoke_test.py uv.lock
git commit -m "ci: add cross-platform quality gates"
```

## Task 7: Final M0 Verification and Evidence Update

**Files:**
- Modify: `docs/learning/progress.md`

- [ ] **Step 1: Run the complete M0 verification suite**

Run:

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest --cov
uv build
uv run mini-code-agent --version
uv run mini-code-agent doctor --json
git diff --check
git status --short --branch
```

Expected:

- All quality commands exit 0.
- Coverage is at least 85%.
- Version is `0.1.0a0`.
- Doctor reports supported Python and does not expose secrets.
- Worktree is clean after evidence updates are committed.

- [ ] **Step 2: Record only verified local evidence**

Update `docs/learning/progress.md`:

```markdown
| L0 Python engineering foundation | Complete locally | `uv run pytest --cov`, Ruff, Pyright, build and CLI doctor |
```

Append under `L0 Notes`:

```markdown
## L0 Verification

- Local Python: 3.13 managed by uv.
- Ruff format/check: passed.
- Pyright strict mode: passed.
- Pytest and package coverage: record the exact final output from the verification run.
- Wheel and source distribution build: passed.
- Cross-platform CI: pending until the repository is pushed to GitHub.
```

Do not replace any resume metric blank with a number unless the final command output proves it. Do not claim Windows/Linux success until GitHub Actions has run.

- [ ] **Step 3: Commit verified evidence**

```powershell
git add docs/learning/progress.md
git commit -m "docs: record verified M0 evidence"
```

- [ ] **Step 4: Create the local M0 milestone tag**

Run:

```powershell
git tag -a v0.1.0-alpha.0 -m "M0 engineering foundation"
git show --stat --oneline v0.1.0-alpha.0
```

Expected: the tag points to the verified M0 evidence commit.

## M0 Completion Gate

M0 is complete only when all statements below are supported by command output:

- `uv.lock` is current and `uv sync --locked --all-groups` succeeds.
- Ruff formatting and lint checks pass.
- Pyright strict mode passes.
- All tests pass with at least 85% package coverage.
- Wheel and source distribution both build and pass smoke tests.
- CLI version and doctor commands work under uv-managed Python 3.13.
- No secret value appears in doctor or structured log tests.
- Local Git worktree is clean.
- GitHub cross-platform claims remain marked pending until CI runs remotely.
