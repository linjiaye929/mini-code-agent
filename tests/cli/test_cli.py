import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mini_code_agent.cli import app

runner = CliRunner()


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


def test_version_option_prints_package_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0.14.0a0"


def test_module_entrypoint_prints_package_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mini_code_agent", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "0.14.0a0"


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


def test_doctor_invalid_environment_returns_json_exit_code_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINI_CODE_AGENT_LOG_LEVEL", "definitely-invalid")

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 2
    payload = json.loads(result.stderr)
    assert "Invalid application configuration" in payload["error"]
    assert "Traceback" not in result.stderr


def test_doctor_validation_error_never_prints_secret(tmp_path: Path) -> None:
    config_path = tmp_path / "invalid-secret.toml"
    config_path.write_text(
        """
[mini_code_agent]
anthropic_api_key = { value = "validation-secret" }
""".strip(),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["doctor", "--config", str(config_path), "--json"])

    assert result.exit_code == 2
    assert "validation-secret" not in result.stderr


def test_doctor_human_output_explains_unusable_data_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_file = tmp_path / "data-file"
    data_file.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("MINI_CODE_AGENT_DATA_DIR", str(data_file))

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "Data path usable" in result.stdout
    assert "Overall healthy" in result.stdout
    assert "False" in result.stdout
