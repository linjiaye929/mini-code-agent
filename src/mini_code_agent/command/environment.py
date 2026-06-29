from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

_POSIX_KEYS = (
    "HOME",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "PATH",
    "TEMP",
    "TERM",
    "TMP",
    "TMPDIR",
    "USER",
)
_WINDOWS_KEYS = (
    "APPDATA",
    "COMSPEC",
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
)


def build_minimal_environment(
    source: Mapping[str, str],
    *,
    platform: Literal["posix", "windows"] | None = None,
) -> dict[str, str]:
    active_platform = platform or ("windows" if os.name == "nt" else "posix")
    keys = _WINDOWS_KEYS if active_platform == "windows" else _POSIX_KEYS
    if active_platform == "windows":
        normalized = {key.upper(): value for key, value in source.items()}
        return {key: normalized[key] for key in keys if key in normalized}
    return {key: source[key] for key in keys if key in source}
