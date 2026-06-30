from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from mini_code_agent.policy.models import (
    ActionGuardResult,
    ActionPreview,
)
from mini_code_agent.repair.fingerprint import scope_sha256
from mini_code_agent.tools.base import SideEffect
from mini_code_agent.workspace.boundary import WorkspaceBoundary


class RepairScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    editable_paths: tuple[str, ...] = Field(min_length=1, max_length=32)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(
        cls,
        workspace: WorkspaceBoundary,
        paths: tuple[str, ...],
    ) -> Self:
        if not 1 <= len(paths) <= 32 or len(set(paths)) != len(paths):
            raise ValueError("Repair scope must contain 1-32 unique paths.")
        displays = tuple(workspace.relative_path(workspace.resolve_file(path)) for path in paths)
        if len(set(displays)) != len(displays):
            raise ValueError("Repair scope paths must have unique identities.")
        editable_paths = tuple(sorted(displays))
        return cls(
            editable_paths=editable_paths,
            sha256=scope_sha256(editable_paths),
        )


class RepairActionGuard:
    def __init__(self, scope: RepairScope) -> None:
        self._editable_paths = frozenset(scope.editable_paths)

    def evaluate(self, preview: ActionPreview) -> ActionGuardResult:
        if preview.side_effect is SideEffect.READ_ONLY:
            return ActionGuardResult(allowed=True)
        if preview.side_effect is SideEffect.WRITE:
            allowed = bool(preview.resources) and all(
                resource in self._editable_paths for resource in preview.resources
            )
            return ActionGuardResult(
                allowed=allowed,
                public_message=(
                    "Repair write is within the approved scope."
                    if allowed
                    else "Repair write is outside the approved scope."
                ),
            )
        return ActionGuardResult(
            allowed=False,
            public_message="Repair workers cannot execute commands or access networks.",
        )
