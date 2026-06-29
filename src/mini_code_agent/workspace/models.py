from pydantic import BaseModel, ConfigDict, Field


class WorkspaceLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_file_bytes: int = Field(default=1024 * 1024, ge=1, le=16 * 1024 * 1024)
    max_path_chars: int = Field(default=1024, ge=1, le=1024)
    max_write_bytes: int = Field(default=1024 * 1024, ge=1, le=16 * 1024 * 1024)
    max_diff_chars: int = Field(default=32_768, ge=1, le=1024 * 1024)


class SearchLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_files: int = Field(default=10_000, ge=1, le=100_000)
    max_total_bytes: int = Field(
        default=64 * 1024 * 1024,
        ge=1,
        le=256 * 1024 * 1024,
    )
    max_results: int = Field(default=200, ge=1, le=10_000)
    max_depth: int = Field(default=32, ge=1, le=64)
    max_line_chars: int = Field(default=20_000, ge=1, le=100_000)
    max_preview_chars: int = Field(default=500, ge=1, le=2_000)


class WorkspaceTextFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=1024)
    text: str
    byte_count: int = Field(ge=0, le=16 * 1024 * 1024)
    line_count: int = Field(ge=0)


class MutationPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=1024)
    created: bool
    before_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    after_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0, le=16 * 1024 * 1024)
    line_count: int = Field(ge=0)
    diff: str = Field(max_length=1024 * 1024)


class MutationResult(MutationPreview):
    pass
