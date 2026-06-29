# orchestration/workflows/blackboard.py
"""Shared Blackboard — multi-phase inter-agent communication channel.

Provides an async-safe, phase-scoped key-value store that agents in
coordinated and development workflows can read from and write to.
Supports cross-phase context sharing, snapshot/restore for pause/resume,
and PostgreSQL persistence.

Usage:
    blackboard = SharedBlackboard()
    await blackboard.write("schema", data, phase="design")
    ctx = await blackboard.get_cross_phase_context(exclude_phase="implement")
    snapshot = blackboard.snapshot()
    blackboard.restore(snapshot)
    await blackboard.sync_to_db("session_123")
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


class SharedBlackboard:
    """Async-safe multi-phase shared workspace for agent coordination."""

    def __init__(self):
        self._phases: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._write_count = 0

    # ── Core write/read ────────────────────────────────────────────

    async def write(self, key: str, value: Any, phase: str = "default") -> None:
        """Write a value to a phase-scoped namespace."""
        async with self._lock:
            if phase not in self._phases:
                self._phases[phase] = {}
            self._phases[phase][key] = value
            self._write_count += 1
            logger.debug(f"Blackboard write: [{phase}] '{key}' (#{self._write_count})")

    async def read(self, key: str, phase: str | None = None) -> Any:
        """Read a value. If phase is None, searches all phases."""
        async with self._lock:
            if phase:
                return self._phases.get(phase, {}).get(key)
            for pdata in self._phases.values():
                if key in pdata:
                    return pdata[key]
            return None

    async def read_phase(self, phase: str) -> dict[str, Any]:
        """Return all entries from a specific phase."""
        async with self._lock:
            return dict(self._phases.get(phase, {}))

    async def list_phases(self) -> list[str]:
        """Return all phase names in insertion order."""
        async with self._lock:
            return list(self._phases.keys())

    async def list_keys(self, phase: str | None = None) -> list[str]:
        """Return keys. If phase is None, returns keys from all phases."""
        async with self._lock:
            if phase:
                return list(self._phases.get(phase, {}).keys())
            all_keys: list[str] = []
            for pdata in self._phases.values():
                all_keys.extend(pdata.keys())
            return all_keys

    async def delete(self, key: str, phase: str | None = None) -> bool:
        """Remove a key. If phase is None, removes from all phases."""
        async with self._lock:
            if phase:
                data = self._phases.get(phase, {})
                if key in data:
                    del data[key]
                    return True
                return False
            found = False
            for pdata in self._phases.values():
                if key in pdata:
                    del pdata[key]
                    found = True
            return found

    async def clear_phase(self, phase: str) -> None:
        """Clear all entries from a specific phase."""
        async with self._lock:
            self._phases.pop(phase, None)

    async def clear(self) -> None:
        """Clear all phases and entries."""
        async with self._lock:
            self._phases.clear()
            self._write_count = 0

    # ── Context generators ─────────────────────────────────────────

    async def get_cross_phase_context(self, exclude_phase: str) -> str:
        """Build context text from all phases EXCEPT the given one.

        Used to inject "what other agents already did" into the current phase.
        """
        async with self._lock:
            lines: list[str] = []
            for pname, pdata in self._phases.items():
                if pname == exclude_phase or not pdata:
                    continue
                lines.append(f"\n[Shared Context — Phase: {pname}]")
                for key, value in list(pdata.items())[:8]:
                    value_str = str(value)[:300]
                    lines.append(f"  - {key}: {value_str}")
            return "\n".join(lines)

    async def get_phase_context(self, phase: str, max_keys: int = 10) -> str:
        """Build context text from a specific phase."""
        async with self._lock:
            pdata = self._phases.get(phase, {})
            if not pdata:
                return ""
            lines = [f"\n[Phase Context — {phase}]"]
            for key, value in list(pdata.items())[:max_keys]:
                value_str = str(value)[:300]
                lines.append(f"  - {key}: {value_str}")
            return "\n".join(lines)

    def get_context_snapshot(self, max_keys: int = 10) -> str:
        """Best-effort non-locked snapshot (backward compat)."""
        return _build_snapshot_text(self._phases, max_keys)

    async def get_agent_context(self, relevant_keys: list[str] | None = None) -> str:
        """Async-locked context snapshot (backward compat)."""
        async with self._lock:
            all_entries: list[tuple[str, Any]] = []
            for pdata in self._phases.values():
                for key, value in pdata.items():
                    if relevant_keys is None or key in relevant_keys:
                        all_entries.append((key, value))

            if not all_entries:
                return ""

            lines = ["[Shared Context from other agents]"]
            for key, value in all_entries[:10]:
                lines.append(f"  - {key}: {str(value)[:200]}")
            return "\n".join(lines)

    # ── Snapshot / Restore ──────────────────────────────────────────

    def snapshot(self) -> dict:
        """Serialize the entire blackboard to a JSON-safe dict."""
        snapshot_data: dict[str, dict[str, Any]] = {}
        for phase, pdata in self._phases.items():
            snapshot_data[phase] = dict(pdata)
        return {"phases": snapshot_data, "write_count": self._write_count}

    def restore(self, data: dict) -> None:
        """Restore blackboard state from a snapshot dict."""
        if not data:
            return
        self._phases = data.get("phases", {})
        self._write_count = data.get("write_count", 0)
        logger.info(
            f"Blackboard restored: {self._write_count} writes across " f"{len(self._phases)} phases"
        )

    # ── Database persistence ───────────────────────────────────────

    async def sync_to_db(self, session_id: str) -> None:
        """Persist all blackboard entries to PostgreSQL for the given session."""
        try:
            from core.database import get_async_session
            from core.models import BlackboardEntry

            async with get_async_session() as db_session:
                from sqlalchemy import delete as sa_delete

                samples = BlackboardEntry  # avoid re-import
                await db_session.execute(
                    sa_delete(samples).where(samples.session_id == session_id)  # type: ignore[arg-type]
                )
                for phase, pdata in self._phases.items():
                    for key, value in pdata.items():
                        entry = BlackboardEntry(
                            session_id=session_id,
                            phase=phase,
                            key=key,
                            value=json.dumps(value, ensure_ascii=False, default=str),
                            created_at=datetime.now(UTC).replace(tzinfo=None),
                        )
                        db_session.add(entry)
                await db_session.commit()
            logger.debug(f"Blackboard synced to DB: {session_id} ({self._write_count} writes)")
        except Exception as e:
            logger.warning(f"Blackboard sync_to_db failed (non-fatal): {e}")

    async def sync_from_db(self, session_id: str) -> bool:
        """Load blackboard entries from PostgreSQL. Returns True if entries found."""
        try:
            from sqlalchemy import select as sa_select

            from core.database import get_async_session
            from core.models import BlackboardEntry

            async with get_async_session() as db_session:
                result = await db_session.execute(
                    sa_select(BlackboardEntry).where(
                        BlackboardEntry.session_id == session_id  # type: ignore[arg-type]
                    )
                )
                entries = result.scalars().all()
                if not entries:
                    return False

                for entry in entries:
                    phase_data = self._phases.setdefault(entry.phase, {})
                    try:
                        phase_data[entry.key] = json.loads(entry.value)
                    except (json.JSONDecodeError, TypeError):
                        phase_data[entry.key] = entry.value
                    self._write_count += 1

            logger.info(
                f"Blackboard loaded from DB: {session_id} "
                f"({len(entries)} entries across {len(self._phases)} phases)"
            )
            return True
        except Exception as e:
            logger.warning(f"Blackboard sync_from_db failed (non-fatal): {e}")
            return False

    # ── Properties ─────────────────────────────────────────────────

    @property
    def entry_count(self) -> int:
        return self._write_count

    @property
    def phase_count(self) -> int:
        return len(self._phases)


# ── helpers ─────────────────────────────────────────────────────────


def _build_snapshot_text(phases: dict[str, dict[str, Any]], max_keys: int = 10) -> str:
    """Build text summary from phases dict (non-locked)."""
    if not phases:
        return ""
    lines: list[str] = ["[Shared Context from other agents]"]
    count = 0
    for pname, pdata in phases.items():
        for key, value in pdata.items():
            if count >= max_keys:
                break
            lines.append(f"  - [{pname}] {key}: {str(value)[:200]}")
            count += 1
    return "\n".join(lines)
