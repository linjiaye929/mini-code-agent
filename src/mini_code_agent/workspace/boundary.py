from __future__ import annotations

import re
import stat
from os import fstat
from pathlib import Path
from typing import Final

from mini_code_agent.workspace.errors import WorkspaceError, WorkspaceErrorCode
from mini_code_agent.workspace.models import SearchLimits, WorkspaceLimits, WorkspaceTextFile

_WINDOWS_DRIVE: Final = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED_NAMES: Final = frozenset(
    {
        "AUX",
        "CON",
        "NUL",
        "PRN",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)


class WorkspaceBoundary:
    def __init__(
        self,
        root: Path,
        *,
        limits: WorkspaceLimits | None = None,
    ) -> None:
        try:
            resolved_root = root.resolve(strict=True)
        except OSError:
            raise ValueError("workspace root must be an existing directory") from None
        if not resolved_root.is_dir():
            raise ValueError("workspace root must be an existing directory")

        self._root = resolved_root
        self._limits = limits or WorkspaceLimits()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def limits(self) -> WorkspaceLimits:
        return self._limits

    def resolve_file(self, untrusted_path: str) -> Path:
        parts = self._validate_relative_path(untrusted_path)
        candidate = self._root.joinpath(*parts)
        current = self._root
        for part in parts:
            current = current / part
            if self._is_link_or_junction(current):
                raise WorkspaceError(
                    WorkspaceErrorCode.LINK_TRAVERSAL,
                    "Workspace path traverses a link.",
                )

        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError:
            raise WorkspaceError(
                WorkspaceErrorCode.NOT_FOUND,
                "Workspace file was not found.",
            ) from None
        except OSError:
            raise WorkspaceError(
                WorkspaceErrorCode.INVALID_PATH,
                "Workspace path could not be resolved.",
            ) from None

        try:
            resolved.relative_to(self._root)
        except ValueError:
            raise WorkspaceError(
                WorkspaceErrorCode.OUTSIDE_WORKSPACE,
                "Requested path is outside the workspace.",
            ) from None

        try:
            mode = resolved.stat(follow_symlinks=False).st_mode
        except OSError:
            raise WorkspaceError(
                WorkspaceErrorCode.NOT_FOUND,
                "Workspace file was not found.",
            ) from None
        if not stat.S_ISREG(mode):
            raise WorkspaceError(
                WorkspaceErrorCode.WRONG_FILE_TYPE,
                "Workspace path is not a regular file.",
            )
        return resolved

    def relative_path(self, resolved_path: Path) -> str:
        try:
            relative = resolved_path.resolve(strict=True).relative_to(self._root)
        except (OSError, ValueError):
            raise WorkspaceError(
                WorkspaceErrorCode.OUTSIDE_WORKSPACE,
                "Requested path is outside the workspace.",
            ) from None
        return relative.as_posix()

    def read_text(self, untrusted_path: str) -> WorkspaceTextFile:
        resolved = self.resolve_file(untrusted_path)
        limit = self._limits.max_file_bytes
        try:
            if resolved.stat(follow_symlinks=False).st_size > limit:
                raise self._too_large()
            with resolved.open("rb") as stream:
                if not stat.S_ISREG(fstat(stream.fileno()).st_mode):
                    raise WorkspaceError(
                        WorkspaceErrorCode.WRONG_FILE_TYPE,
                        "Workspace path is not a regular file.",
                    )
                content = stream.read(limit + 1)
        except WorkspaceError:
            raise
        except OSError:
            raise WorkspaceError(
                WorkspaceErrorCode.INVALID_PATH,
                "Workspace file could not be read.",
            ) from None

        if len(content) > limit:
            raise self._too_large()
        if b"\0" in content:
            raise WorkspaceError(
                WorkspaceErrorCode.BINARY_FILE,
                "Workspace file is binary.",
            )
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise WorkspaceError(
                WorkspaceErrorCode.INVALID_ENCODING,
                "Workspace file is not valid UTF-8 text.",
            ) from None
        return WorkspaceTextFile(
            path=self.relative_path(resolved),
            text=text,
            byte_count=len(content),
            line_count=len(text.splitlines()),
        )

    def list_files(
        self,
        start_path: str | None = None,
        *,
        limits: SearchLimits | None = None,
    ) -> tuple[str, ...]:
        active_limits = limits or SearchLimits()
        start = self._root if start_path is None else self._resolve_directory(start_path)
        stack: list[tuple[Path, int]] = [(start, 0)]
        files: list[str] = []
        total_bytes = 0

        while stack:
            directory, depth = stack.pop()
            try:
                entries = sorted(
                    directory.iterdir(),
                    key=lambda path: (path.name.casefold(), path.name),
                    reverse=True,
                )
            except OSError:
                raise self._traversal_error("Workspace directory could not be scanned.") from None

            for entry in entries:
                if entry.name.casefold() == ".git":
                    continue
                if self._is_link_or_junction(entry):
                    raise WorkspaceError(
                        WorkspaceErrorCode.LINK_TRAVERSAL,
                        "Workspace traversal encountered a link.",
                    )
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError:
                    raise self._traversal_error(
                        "Workspace entry changed during traversal."
                    ) from None

                if stat.S_ISDIR(entry_stat.st_mode):
                    child_depth = depth + 1
                    if child_depth > active_limits.max_depth:
                        raise self._traversal_error("Workspace traversal exceeded the depth limit.")
                    stack.append((entry, child_depth))
                    continue
                if not stat.S_ISREG(entry_stat.st_mode):
                    raise WorkspaceError(
                        WorkspaceErrorCode.WRONG_FILE_TYPE,
                        "Workspace traversal encountered a special file.",
                    )

                files.append(self.relative_path(entry))
                total_bytes += entry_stat.st_size
                if (
                    len(files) > active_limits.max_files
                    or total_bytes > active_limits.max_total_bytes
                ):
                    raise self._traversal_error("Workspace traversal exceeded its resource budget.")

        return tuple(sorted(files, key=lambda path: (path.casefold(), path)))

    def _resolve_directory(self, untrusted_path: str) -> Path:
        parts = self._validate_relative_path(untrusted_path)
        candidate = self._root.joinpath(*parts)
        current = self._root
        for part in parts:
            current = current / part
            if self._is_link_or_junction(current):
                raise WorkspaceError(
                    WorkspaceErrorCode.LINK_TRAVERSAL,
                    "Workspace path traverses a link.",
                )
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self._root)
        except FileNotFoundError:
            raise WorkspaceError(
                WorkspaceErrorCode.NOT_FOUND,
                "Workspace directory was not found.",
            ) from None
        except (OSError, ValueError):
            raise WorkspaceError(
                WorkspaceErrorCode.OUTSIDE_WORKSPACE,
                "Requested path is outside the workspace.",
            ) from None
        if not resolved.is_dir():
            raise WorkspaceError(
                WorkspaceErrorCode.WRONG_FILE_TYPE,
                "Workspace path is not a directory.",
            )
        return resolved

    def _validate_relative_path(self, value: object) -> tuple[str, ...]:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > self._limits.max_path_chars
            or "\0" in value
            or "\\" in value
            or "%" in value
            or value.startswith("/")
            or _WINDOWS_DRIVE.match(value)
        ):
            raise self._invalid_path()
        parts = tuple(value.split("/"))
        if any(not self._valid_part(part) for part in parts):
            raise self._invalid_path()
        return parts

    @staticmethod
    def _valid_part(part: str) -> bool:
        stem = part.split(".", maxsplit=1)[0].upper()
        return not (
            part in {"", ".", ".."}
            or part.casefold() == ".git"
            or ":" in part
            or part.rstrip(" .") != part
            or stem in _WINDOWS_RESERVED_NAMES
            or any(ord(character) < 32 for character in part)
        )

    @staticmethod
    def _is_link_or_junction(path: Path) -> bool:
        try:
            if path.is_symlink():
                return True
            is_junction = getattr(path, "is_junction", None)
            return bool(is_junction and is_junction())
        except OSError:
            return True

    @staticmethod
    def _invalid_path() -> WorkspaceError:
        return WorkspaceError(
            WorkspaceErrorCode.INVALID_PATH,
            "Workspace path is invalid.",
        )

    @staticmethod
    def _too_large() -> WorkspaceError:
        return WorkspaceError(
            WorkspaceErrorCode.TOO_LARGE,
            "Workspace file exceeds the configured size limit.",
        )

    @staticmethod
    def _traversal_error(message: str) -> WorkspaceError:
        return WorkspaceError(
            WorkspaceErrorCode.TRAVERSAL_BUDGET,
            message,
        )
