# orchestration/result_types.py
"""Standardized workflow result types — Success, Failure, Timeout."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkflowResult:
    """Base result from any workflow execution."""

    success: bool
    content: str = ""
    error: str | None = None
    timeout: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


def success(content: str, **metadata: Any) -> WorkflowResult:
    return WorkflowResult(success=True, content=content, metadata=dict(metadata))


def failure(error: str, partial_content: str = "", **metadata: Any) -> WorkflowResult:
    return WorkflowResult(
        success=False, error=error, content=partial_content, metadata=dict(metadata)
    )


def timeout(partial_content: str = "", timeout_seconds: float = 0) -> WorkflowResult:
    return WorkflowResult(
        success=False,
        timeout=True,
        content=partial_content,
        error=f"Workflow timed out after {timeout_seconds}s",
        metadata={"timeout_seconds": timeout_seconds},
    )
