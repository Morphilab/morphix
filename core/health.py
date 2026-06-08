"""Health check — runtime connectivity probes.

Usage: poetry run python -m core.health
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HealthReport:
    """Structured health check result for all services."""

    timestamp: float = field(default_factory=time.time)
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    all_ok: bool = True

    def add(self, name: str, ok: bool, detail: str = "", **extra) -> None:
        self.checks[name] = {"ok": ok, "detail": detail, **extra}
        if not ok:
            self.all_ok = False

    def format(self) -> str:
        lines = ["═══ Morphix Health Check ═══"]
        for name, check in self.checks.items():
            icon = "✅" if check["ok"] else "❌"
            lines.append(f"  {icon} {name}: {check['detail']}")
        lines.append("════════════════════════════")
        lines.append(f"Overall: {'✅ ALL OK' if self.all_ok else '❌ ISSUES DETECTED'}")
        return "\n".join(lines)


async def check_database(report: HealthReport) -> None:
    """Probe PostgreSQL connectivity with async SELECT 1."""
    from core.config import settings

    if not settings.database_url:
        report.add("Database", False, "DATABASE_URL not configured")
        return

    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
        engine = create_async_engine(url, echo=False)
        async with engine.connect() as conn:
            start = time.monotonic()
            result = await conn.execute(text("SELECT 1"))
            result.fetchone()
            elapsed = time.monotonic() - start
        await engine.dispose()
        report.add("Database", True, f"OK ({elapsed * 1000:.0f}ms)")
    except Exception as e:
        report.add("Database", False, str(e)[:120])


async def check_llm(report: HealthReport) -> None:
    """Probe LLM provider reachability with a fast connectivity check."""
    import httpx

    from core.config import settings

    provider = "deepseek"
    role_config = settings.model_roles.get("default", {})
    provider_name = role_config.get("provider", "deepseek")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            start = time.monotonic()
            if provider_name == "deepseek":
                resp = await client.get("https://api.deepseek.com/v1/models")
            elif provider_name == "openai":
                resp = await client.get("https://api.openai.com/v1/models")
            else:
                resp = await client.get(f"https://api.{provider_name}.com")
            elapsed = time.monotonic() - start
            if resp.status_code in (200, 401, 403):
                report.add(
                    "LLM",
                    True,
                    f"{provider_name} reachable ({elapsed * 1000:.0f}ms)",
                )
            else:
                report.add("LLM", False, f"{provider_name} returned {resp.status_code}")
    except Exception as e:
        report.add("LLM", False, str(e)[:120])


async def check_redis(report: HealthReport) -> None:
    """Probe Redis connectivity if configured."""
    from core.config import settings

    if not settings.redis_url or settings.redis_url == "redis://localhost:6379/0":
        report.add("Redis", True, "not configured (default)")
        return

    try:
        import redis.asyncio as redis

        r = redis.from_url(settings.redis_url)
        start = time.monotonic()
        await r.ping()  # type: ignore[misc]
        elapsed = time.monotonic() - start
        await r.aclose()
        report.add("Redis", True, f"OK ({elapsed * 1000:.0f}ms)")
    except Exception as e:
        report.add("Redis", False, str(e)[:120])


def check_filesystem(report: HealthReport) -> None:
    """Probe critical directories and workspace integrity."""
    from core.path_resolver import MEMORY_BASE, TEMPLATES_DIR

    try:
        ok = MEMORY_BASE.exists()
        report.add("Memory Dir", ok, str(MEMORY_BASE) if ok else "missing")
    except Exception as e:
        report.add("Memory Dir", False, str(e)[:120])

    try:
        ok = TEMPLATES_DIR.exists()
        templates_count = len(list(TEMPLATES_DIR.glob("**/*.yaml"))) if ok else 0
        report.add(
            "Templates",
            ok,
            f"{templates_count} YAML files" if ok else "missing",
        )
    except Exception as e:
        report.add("Templates", False, str(e)[:120])


def check_workspace(report: HealthReport) -> None:
    """Probe current workspace integrity."""
    from core.workflow_state import get_active_workflow

    try:
        wf = get_active_workflow()
        report.add("Workspace", True, f"active workflow: {wf}")
    except Exception as e:
        report.add("Workspace", False, str(e)[:120])


async def run_health_check() -> HealthReport:
    """Run all health checks and return a structured report."""
    report = HealthReport()

    check_filesystem(report)
    check_workspace(report)
    await check_database(report)
    await check_llm(report)
    await check_redis(report)

    return report
