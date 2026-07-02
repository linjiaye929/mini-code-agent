from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from mini_code_agent.domain.content import ToolCall, ToolResult
from mini_code_agent.policy.models import ActionPreview, RiskLevel
from mini_code_agent.tools.base import SideEffect, ToolDefinition
from mini_code_agent.workspace.boundary import WorkspaceBoundary
from mini_code_agent.workspace.errors import WorkspaceError
from mini_code_agent.workspace.models import WorkspaceLimits
from mini_code_agent.worktrees.models import (
    AdoptionResult,
    AdoptionStatus,
    CandidateFile,
    CandidateManifest,
    CandidateState,
    DiscardResult,
    DiscardStatus,
    WorktreeProfile,
)
from mini_code_agent.worktrees.state import (
    VerifiedCandidate,
    WorktreeStateError,
    WorktreeStateStore,
)


class AdoptionGit(Protocol):
    async def repository_info(self) -> tuple[Path, bool]: ...

    async def head_sha(self) -> str: ...

    async def status_porcelain(self) -> bytes: ...

    async def changed_paths(self) -> tuple[str, ...]: ...


class CandidateAdoptionError(RuntimeError):
    pass


@dataclass(slots=True)
class _PreparedFile:
    candidate: CandidateFile
    target: Path
    after: bytes
    before: bytes | None
    mode: int
    temp_path: Path | None


class CandidateAdoptionService:
    def __init__(
        self,
        profile: WorktreeProfile,
        *,
        store: WorktreeStateStore,
        git: AdoptionGit,
    ) -> None:
        self._profile = profile
        self._store = store
        self._git = git
        self._workspace = WorkspaceBoundary(
            profile.repository_root,
            limits=WorkspaceLimits(
                max_file_bytes=profile.limits.max_file_bytes,
                max_path_chars=profile.limits.max_path_chars,
                max_write_bytes=profile.limits.max_file_bytes,
                max_diff_chars=profile.limits.max_diff_chars,
            ),
        )
        self._lock = asyncio.Lock()

    async def preview(self, candidate_id: str) -> CandidateManifest:
        try:
            return await asyncio.to_thread(
                self._store.load_candidate,
                CandidateState.READY,
                candidate_id,
            )
        except (WorktreeStateError, ValueError):
            raise CandidateAdoptionError("Candidate is not ready.") from None

    async def adopt(self, candidate_id: str) -> AdoptionResult:
        async with self._lock:
            try:
                ready = await asyncio.to_thread(
                    self._store.load_candidate,
                    CandidateState.READY,
                    candidate_id,
                )
                await asyncio.to_thread(
                    self._store.transition_candidate,
                    candidate_id,
                    CandidateState.READY,
                    CandidateState.APPLYING,
                )
                payload = await asyncio.to_thread(
                    self._store.load_candidate_payload,
                    CandidateState.APPLYING,
                    candidate_id,
                )
            except (WorktreeStateError, ValueError):
                raise CandidateAdoptionError("Candidate could not be claimed.") from None

            prepared: tuple[_PreparedFile, ...] = ()
            applied: list[_PreparedFile] = []
            try:
                await self._verify_repository_before(payload.manifest)
                prepared = await asyncio.to_thread(self._prepare_all, payload)
                await asyncio.to_thread(self._revalidate_all, prepared)
            except (_AdoptionConflict, WorkspaceError):
                _cleanup_temps(prepared)
                if not await self._return_to_ready(candidate_id):
                    return _adoption_result(ready, AdoptionStatus.APPLY_UNCERTAIN)
                return _adoption_result(ready, AdoptionStatus.CONFLICT)
            except Exception:
                _cleanup_temps(prepared)
                if not await self._return_to_ready(candidate_id):
                    return _adoption_result(ready, AdoptionStatus.APPLY_UNCERTAIN)
                return _adoption_result(
                    ready,
                    AdoptionStatus.APPLY_FAILED_ROLLED_BACK,
                )

            try:
                for item in prepared:
                    await asyncio.to_thread(_apply_prepared, item)
                    applied.append(item)
                await asyncio.to_thread(_verify_after, prepared)
                changed_paths = await self._git.changed_paths()
                if changed_paths != tuple(item.candidate.path for item in prepared):
                    raise OSError("Parent changed-path set is inconsistent.")
                await asyncio.to_thread(
                    self._store.transition_candidate,
                    candidate_id,
                    CandidateState.APPLYING,
                    CandidateState.APPLIED,
                )
            except Exception:
                _cleanup_temps(prepared)
                rolled_back = await asyncio.to_thread(_rollback, tuple(applied))
                if rolled_back and await self._repository_is_clean_at_base(ready):
                    try:
                        await asyncio.to_thread(
                            self._store.transition_candidate,
                            candidate_id,
                            CandidateState.APPLYING,
                            CandidateState.READY,
                        )
                    except WorktreeStateError:
                        rolled_back = False
                if rolled_back:
                    return _adoption_result(
                        ready,
                        AdoptionStatus.APPLY_FAILED_ROLLED_BACK,
                    )
                await self._mark_uncertain(candidate_id)
                return _adoption_result(ready, AdoptionStatus.APPLY_UNCERTAIN)
            _cleanup_temps(prepared)
            return _adoption_result(ready, AdoptionStatus.APPLIED)

    async def recover(self, candidate_id: str) -> AdoptionResult:
        async with self._lock:
            try:
                payload = await asyncio.to_thread(
                    self._store.load_candidate_payload,
                    CandidateState.APPLYING,
                    candidate_id,
                )
            except WorktreeStateError:
                raise CandidateAdoptionError("Applying candidate is unavailable.") from None
            manifest = payload.manifest
            with suppress(Exception):
                top_level, bare = await self._git.repository_info()
                head = await self._git.head_sha()
                states = await asyncio.to_thread(
                    _classify_parent_files,
                    self._workspace,
                    payload,
                )
                if bare or top_level != self._profile.repository_root or head != manifest.base_sha:
                    raise _AdoptionConflict
                if all(state == "before" for state in states):
                    if await self._git.status_porcelain():
                        raise _AdoptionConflict
                    await asyncio.to_thread(
                        self._store.transition_candidate,
                        candidate_id,
                        CandidateState.APPLYING,
                        CandidateState.READY,
                    )
                    return _adoption_result(
                        manifest,
                        AdoptionStatus.RECOVERED_READY,
                    )
                if all(state == "after" for state in states):
                    if await self._git.changed_paths() != tuple(
                        item.path for item in manifest.files
                    ):
                        raise _AdoptionConflict
                    await asyncio.to_thread(
                        self._store.transition_candidate,
                        candidate_id,
                        CandidateState.APPLYING,
                        CandidateState.APPLIED,
                    )
                    return _adoption_result(manifest, AdoptionStatus.APPLIED)
            await self._mark_uncertain(candidate_id)
            return _adoption_result(manifest, AdoptionStatus.APPLY_UNCERTAIN)

    async def discard(self, candidate_id: str) -> DiscardResult:
        async with self._lock:
            try:
                manifest = await asyncio.to_thread(
                    self._store.load_candidate,
                    CandidateState.READY,
                    candidate_id,
                )
                await asyncio.to_thread(
                    self._store.transition_candidate,
                    candidate_id,
                    CandidateState.READY,
                    CandidateState.DISCARDING,
                )
                await asyncio.to_thread(
                    self._store.delete_candidate,
                    CandidateState.DISCARDING,
                    candidate_id,
                )
            except (WorktreeStateError, ValueError):
                raise CandidateAdoptionError("Ready candidate could not be discarded.") from None
            return DiscardResult(
                candidate_id=candidate_id,
                status=DiscardStatus.DISCARDED,
                changed_files=manifest.changed_files,
                manifest_sha256=manifest.manifest_sha256,
            )

    async def _verify_repository_before(self, manifest: CandidateManifest) -> None:
        top_level, bare = await self._git.repository_info()
        if (
            bare
            or top_level != self._profile.repository_root
            or await self._git.head_sha() != manifest.base_sha
            or await self._git.status_porcelain()
        ):
            raise _AdoptionConflict

    def _prepare_all(
        self,
        payload: VerifiedCandidate,
    ) -> tuple[_PreparedFile, ...]:
        prepared: list[_PreparedFile] = []
        try:
            for candidate in payload.manifest.files:
                content = payload.blobs[candidate.content_blob_sha256]
                text = _decode_text(content)
                target = self._workspace.root.joinpath(*candidate.path.split("/"))
                before: bytes | None = None
                mode = 0o644
                if candidate.before_sha256 is not None:
                    before = _read_regular(target)
                    if hashlib.sha256(before).hexdigest() != candidate.before_sha256:
                        raise _AdoptionConflict
                    mode = stat.S_IMODE(target.stat(follow_symlinks=False).st_mode)
                elif target.exists() or _is_link_or_reparse(target):
                    raise _AdoptionConflict
                self._workspace.preview_write(
                    candidate.path,
                    text,
                    expected_sha256=candidate.before_sha256,
                )
                temp_path = _stage_content(target.parent, content, mode)
                prepared.append(
                    _PreparedFile(
                        candidate=candidate,
                        target=target,
                        after=content,
                        before=before,
                        mode=mode,
                        temp_path=temp_path,
                    )
                )
        except Exception:
            _cleanup_temps(tuple(prepared))
            raise
        return tuple(prepared)

    def _revalidate_all(self, prepared: tuple[_PreparedFile, ...]) -> None:
        for item in prepared:
            text = _decode_text(item.after)
            self._workspace.preview_write(
                item.candidate.path,
                text,
                expected_sha256=item.candidate.before_sha256,
            )
            if item.before is None:
                if item.target.exists() or _is_link_or_reparse(item.target):
                    raise _AdoptionConflict
            elif hashlib.sha256(_read_regular(item.target)).hexdigest() != (
                item.candidate.before_sha256
            ):
                raise _AdoptionConflict

    async def _return_to_ready(self, candidate_id: str) -> bool:
        try:
            await asyncio.to_thread(
                self._store.transition_candidate,
                candidate_id,
                CandidateState.APPLYING,
                CandidateState.READY,
            )
            return True
        except WorktreeStateError:
            await self._mark_uncertain(candidate_id)
            return False

    async def _mark_uncertain(self, candidate_id: str) -> None:
        with suppress(WorktreeStateError):
            await asyncio.to_thread(
                self._store.write_candidate_recovery,
                CandidateState.APPLYING,
                candidate_id,
                "apply_uncertain",
            )
        with suppress(WorktreeStateError):
            await asyncio.to_thread(
                self._store.transition_candidate,
                candidate_id,
                CandidateState.APPLYING,
                CandidateState.UNCERTAIN,
            )

    async def _repository_is_clean_at_base(
        self,
        manifest: CandidateManifest,
    ) -> bool:
        try:
            top_level, bare = await self._git.repository_info()
            return bool(
                not bare
                and top_level == self._profile.repository_root
                and await self._git.head_sha() == manifest.base_sha
                and not await self._git.status_porcelain()
            )
        except Exception:
            return False


class _CandidateArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    candidate_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
    reason: str = Field(min_length=1, max_length=500)


def _candidate_input_schema() -> dict[str, JsonValue]:
    return {
        "type": "object",
        "properties": {
            "candidate_id": {
                "type": "string",
                "pattern": r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$",
            },
            "reason": {"type": "string", "minLength": 1, "maxLength": 500},
        },
        "required": ["candidate_id", "reason"],
        "additionalProperties": False,
    }


class AdoptSubagentCandidateTool:
    _definition: ClassVar[ToolDefinition] = ToolDefinition(
        name="adopt_subagent_candidate",
        description="Apply one verified ready Subagent candidate to the parent workspace.",
        input_schema=_candidate_input_schema(),
        side_effect=SideEffect.WRITE,
    )

    def __init__(self, service: CandidateAdoptionService) -> None:
        self._service = service

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def preview(self, call: ToolCall) -> ActionPreview:
        arguments = _parse_candidate_arguments(call, self._definition.name)
        manifest = await self._service.preview(arguments.candidate_id)
        return _candidate_preview(call, arguments.reason, manifest, "Adopt")

    async def execute(self, call: ToolCall) -> ToolResult:
        try:
            arguments = _parse_candidate_arguments(call, self._definition.name)
            result = await self._service.adopt(arguments.candidate_id)
        except (ValueError, CandidateAdoptionError):
            return _candidate_error(call.id, "candidate_unavailable")
        return _candidate_result(call.id, result)


class DiscardSubagentCandidateTool:
    _definition: ClassVar[ToolDefinition] = ToolDefinition(
        name="discard_subagent_candidate",
        description="Discard one verified ready Subagent candidate.",
        input_schema=_candidate_input_schema(),
        side_effect=SideEffect.WRITE,
    )

    def __init__(self, service: CandidateAdoptionService) -> None:
        self._service = service

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def preview(self, call: ToolCall) -> ActionPreview:
        arguments = _parse_candidate_arguments(call, self._definition.name)
        manifest = await self._service.preview(arguments.candidate_id)
        return _candidate_preview(call, arguments.reason, manifest, "Discard")

    async def execute(self, call: ToolCall) -> ToolResult:
        try:
            arguments = _parse_candidate_arguments(call, self._definition.name)
            result = await self._service.discard(arguments.candidate_id)
        except (ValueError, CandidateAdoptionError):
            return _candidate_error(call.id, "candidate_unavailable")
        return _candidate_result(call.id, result)


class _AdoptionConflict(RuntimeError):
    pass


def _apply_prepared(item: _PreparedFile) -> None:
    if item.temp_path is None:
        raise OSError("Candidate staging file is unavailable.")
    if item.before is None:
        if item.target.exists() or _is_link_or_reparse(item.target):
            raise OSError("Candidate addition target changed before apply.")
        os.link(item.temp_path, item.target)
        item.temp_path.unlink()
    else:
        if hashlib.sha256(_read_regular(item.target)).hexdigest() != (item.candidate.before_sha256):
            raise OSError("Candidate target changed before apply.")
        os.replace(item.temp_path, item.target)
    item.temp_path = None
    _fsync_directory(item.target.parent)


def _verify_after(prepared: tuple[_PreparedFile, ...]) -> None:
    for item in prepared:
        if hashlib.sha256(_read_regular(item.target)).hexdigest() != (item.candidate.after_sha256):
            raise OSError("Adopted file hash is inconsistent.")


def _rollback(applied: tuple[_PreparedFile, ...]) -> bool:
    try:
        for item in reversed(applied):
            if hashlib.sha256(_read_regular(item.target)).hexdigest() != (
                item.candidate.after_sha256
            ):
                return False
            if item.before is None:
                item.target.unlink()
                _fsync_directory(item.target.parent)
            else:
                temp: Path | None = None
                try:
                    temp = _stage_content(item.target.parent, item.before, item.mode)
                    if hashlib.sha256(_read_regular(item.target)).hexdigest() != (
                        item.candidate.after_sha256
                    ):
                        return False
                    os.replace(temp, item.target)
                    temp = None
                    _fsync_directory(item.target.parent)
                finally:
                    if temp is not None:
                        with suppress(OSError):
                            temp.unlink()
        for item in applied:
            if item.before is None:
                if item.target.exists() or _is_link_or_reparse(item.target):
                    return False
            elif hashlib.sha256(_read_regular(item.target)).hexdigest() != (
                item.candidate.before_sha256
            ):
                return False
    except OSError:
        return False
    return True


def _classify_parent_files(
    workspace: WorkspaceBoundary,
    payload: VerifiedCandidate,
) -> tuple[Literal["before", "after", "unknown"], ...]:
    states: list[Literal["before", "after", "unknown"]] = []
    for item in payload.manifest.files:
        target = workspace.root.joinpath(*item.path.split("/"))
        if not target.exists() or _is_link_or_reparse(target):
            states.append("before" if item.before_sha256 is None else "unknown")
            continue
        try:
            digest = hashlib.sha256(_read_regular(target)).hexdigest()
        except OSError:
            states.append("unknown")
            continue
        if digest == item.after_sha256:
            states.append("after")
        elif item.before_sha256 is not None and digest == item.before_sha256:
            states.append("before")
        else:
            states.append("unknown")
    return tuple(states)


def _stage_content(parent: Path, content: bytes, mode: int) -> Path:
    descriptor = -1
    path: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=".mini-code-agent-adopt-",
            suffix=".tmp",
            dir=parent,
        )
        path = Path(raw_path)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(mode)
        return path
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        if path is not None:
            with suppress(OSError):
                path.unlink()
        raise


def _cleanup_temps(prepared: tuple[_PreparedFile, ...]) -> None:
    for item in prepared:
        if item.temp_path is not None:
            with suppress(OSError):
                item.temp_path.unlink()
            item.temp_path = None


def _read_regular(path: Path) -> bytes:
    if _is_link_or_reparse(path):
        raise OSError("Parent path is linked.")
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise OSError("Parent path is not a regular file.")
        content = stream.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024 or _is_link_or_reparse(path):
        raise OSError("Parent file exceeds its limit or changed type.")
    return content


def _decode_text(content: bytes) -> str:
    if b"\0" in content:
        raise _AdoptionConflict
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        raise _AdoptionConflict from None


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _adoption_result(
    manifest: CandidateManifest,
    status: AdoptionStatus,
) -> AdoptionResult:
    return AdoptionResult(
        candidate_id=manifest.candidate_id,
        status=status,
        changed_files=manifest.changed_files,
        manifest_sha256=manifest.manifest_sha256,
    )


def _parse_candidate_arguments(
    call: ToolCall,
    expected_name: str,
) -> _CandidateArguments:
    if call.name != expected_name:
        raise ValueError("Candidate Tool name is invalid.")
    try:
        arguments = _CandidateArguments.model_validate(
            dict(call.arguments),
            strict=True,
        )
    except ValidationError:
        raise ValueError("Candidate Tool arguments are invalid.") from None
    if "\0" in arguments.reason:
        raise ValueError("Candidate Tool arguments are invalid.")
    return arguments


def _candidate_preview(
    call: ToolCall,
    reason: str,
    manifest: CandidateManifest,
    verb: str,
) -> ActionPreview:
    combined_diff = "\n".join(item.diff for item in manifest.files)
    summary = (
        f"{verb} candidate {manifest.candidate_id} at base {manifest.base_sha} "
        f"with {manifest.changed_files} file(s) and "
        f"{manifest.after_content_bytes} after-content byte(s)."
    )
    return ActionPreview(
        tool_call_id=call.id,
        tool_name=call.name,
        side_effect=SideEffect.WRITE,
        risk=RiskLevel.HIGH,
        summary=summary,
        reason=reason,
        resources=(
            str(manifest.repository_root),
            *(item.path for item in manifest.files[:31]),
        ),
        diff=combined_diff[:32_768],
    )


def _candidate_result(
    call_id: str,
    result: AdoptionResult | DiscardResult,
) -> ToolResult:
    return ToolResult(
        tool_call_id=call_id,
        content=json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


def _candidate_error(call_id: str, code: str) -> ToolResult:
    return ToolResult(
        tool_call_id=call_id,
        content=json.dumps(
            {
                "error": {
                    "code": code,
                    "message": "Candidate operation could not be completed.",
                }
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
        is_error=True,
    )
