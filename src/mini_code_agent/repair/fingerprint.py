from __future__ import annotations

import hashlib
import json

from mini_code_agent.testing.models import PytestRunResult


def scope_sha256(paths: tuple[str, ...]) -> str:
    return _sha256(
        {
            "editable_paths": sorted(paths),
            "version": 1,
        }
    )


def failure_sha256(result: PytestRunResult) -> str:
    diagnostics = sorted(
        (
            {
                "class_name": item.class_name,
                "file": item.file,
                "line": item.line,
                "message": item.message,
                "outcome": item.outcome.value,
                "test_name": item.test_name,
            }
            for item in result.diagnostics
        ),
        key=lambda item: (
            item["outcome"],
            item["file"] or "",
            item["line"] if item["line"] is not None else -1,
            item["class_name"] or "",
            item["test_name"],
            item["message"],
        ),
    )
    return _sha256(
        {
            "counts": result.counts.model_dump(mode="json"),
            "diagnostics": diagnostics,
            "report_status": result.report_status.value,
            "status": result.status.value,
            "version": 1,
        }
    )


def _sha256(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
