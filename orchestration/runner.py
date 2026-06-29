# orchestration/runner.py
"""WorkflowRunner — timeout, cancellation, and safe execution wrapper."""

import asyncio
import logging
import time
from collections.abc import Coroutine
from typing import Any, TypeVar

from orchestration.result_types import WorkflowResult, failure, success, timeout

logger = logging.getLogger(__name__)
T = TypeVar("T")


class WorkflowTimeoutError(asyncio.TimeoutError):
    """Raised when a workflow phase exceeds its time limit."""


class WorkflowCancelledError(asyncio.CancelledError):
    """Raised when a workflow is cancelled by the user."""


class CircuitBreakerOpenError(Exception):
    """Raised when the circuit breaker is open for a provider."""


class WorkflowRunner:
    """Wraps a Session to provide timeout, cancellation, and safe execution.

    Usage:
        runner = WorkflowRunner(session)
        result = await runner.with_timeout(phase_coro, 30, phase="decompose")
    """

    def __init__(self, session):
        self.session = session
        self._phase_times: dict[str, float] = {}

    @property
    def cancelled(self) -> bool:
        return self.session.is_cancelled

    def check_cancelled(self) -> None:
        """Raise if the workflow has been cancelled."""
        if self.cancelled:
            raise WorkflowCancelledError("Workflow cancelled by user")

    async def with_timeout(
        self,
        coro: Coroutine[Any, Any, T],
        timeout_seconds: float,
        *,
        phase: str = "unknown",
        fallback: str = "",
    ) -> WorkflowResult:
        """Execute a coroutine with a timeout. Returns WorkflowResult."""
        self.check_cancelled()

        phase_start = time.monotonic()
        try:
            result = await asyncio.wait_for(coro, timeout=timeout_seconds)
            elapsed = time.monotonic() - phase_start
            self._phase_times[phase] = elapsed
            logger.debug(f"Phase '{phase}' completed in {elapsed:.1f}s")

            if isinstance(result, WorkflowResult):
                return result
            content = str(result) if result is not None else ""
            return success(content, phase=phase, elapsed=elapsed)

        except TimeoutError:
            logger.warning(f"Phase '{phase}' timed out after {timeout_seconds}s")
            return timeout(partial_content=fallback, timeout_seconds=timeout_seconds)

        except WorkflowCancelledError:
            raise

        except Exception as e:
            elapsed = time.monotonic() - phase_start
            logger.error(f"Phase '{phase}' failed: {e}")
            return failure(str(e), partial_content=fallback, phase=phase, elapsed=elapsed)

    async def safe_call(
        self,
        coro: Coroutine[Any, Any, T],
        *,
        fallback: T | None = None,
        error_tag: str = "",
    ) -> T | None:
        """Execute a coroutine, return fallback on error. Does NOT timeout."""
        try:
            return await coro
        except WorkflowCancelledError:
            raise
        except Exception as e:
            tag = f" [{error_tag}]" if error_tag else ""
            logger.warning(f"safe_call failed{tag}: {e}")
            return fallback

    def elapsed(self) -> float:
        """Total wall-clock time across all phases."""
        return sum(self._phase_times.values())

    def phase_stats(self) -> dict[str, float]:
        return dict(self._phase_times)
