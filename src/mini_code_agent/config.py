from __future__ import annotations

import tomllib
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

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
    return cast(dict[str, Any], section)


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
