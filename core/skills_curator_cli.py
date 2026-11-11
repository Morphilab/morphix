# core/skills_curator_cli.py — CLI del curator de skills
"""Comandos operativos del curator.

Uso: ``uv run python -m core.skills_curator_cli -w <workspace> <comando>``
(tool.uv package=false: los [project.scripts] no se instalan — misma
convención que morphix-mcp / morphix-workflow).

- ``status`` — snapshot del sidecar + clasificación por provenance.
- ``run`` — walk determinista (stale/archive por edad); ``--review`` añade
  el pase LLM (canal fast) que puede vetar archivos.
- ``pin``/``unpin`` — exenta/retorna una skill de toda transición.
- ``archive``/``restore`` — acciones manuales (restaurables, jamás delete).
- ``pause``/``resume`` — congela/descongela el scheduler.
"""

import asyncio
import json
import sys

import click

from core import skills_curator as curator


@click.group()
@click.option("-w", "--workspace", default="main", show_default=True, help="Workspace.")
@click.pass_context
def main(ctx: click.Context, workspace: str) -> None:
    ctx.ensure_object(dict)
    ctx.obj["workspace"] = workspace


@main.command("status")
@click.pass_context
def status(ctx: click.Context) -> None:
    click.echo(json.dumps(curator.curator_status(ctx.obj["workspace"]), indent=1))


@main.command("run")
@click.option("--dry-run", is_flag=True, help="Reporta sin mover nada.")
@click.option("--review", is_flag=True, help="Pase LLM (canal fast) para vetar archivos.")
@click.pass_context
def run(ctx: click.Context, dry_run: bool, review: bool) -> None:
    reviewer = curator.default_reviewer if review else None

    async def _run() -> dict[str, list[str]]:
        return await curator.apply_automatic_transitions(
            ctx.obj["workspace"], reviewer=reviewer, dry_run=dry_run
        )

    click.echo(json.dumps(asyncio.run(_run()), indent=1))


@main.command("pin")
@click.argument("name")
@click.pass_context
def pin(ctx: click.Context, name: str) -> None:
    curator.set_pinned(ctx.obj["workspace"], name, True)
    click.echo(f"📌 {name} pinned")


@main.command("unpin")
@click.argument("name")
@click.pass_context
def unpin(ctx: click.Context, name: str) -> None:
    curator.set_pinned(ctx.obj["workspace"], name, False)
    click.echo(f"📌 {name} liberada")


@main.command("archive")
@click.argument("name")
@click.pass_context
def archive(ctx: click.Context, name: str) -> None:
    try:
        dest = curator.archive_skill(ctx.obj["workspace"], name)
    except (PermissionError, KeyError) as exc:
        click.echo(f"❌ {exc}", err=True)
        sys.exit(1)
    click.echo(f"📦 {name} → {dest}")


@main.command("restore")
@click.argument("name")
@click.pass_context
def restore(ctx: click.Context, name: str) -> None:
    try:
        dest = curator.restore_skill(ctx.obj["workspace"], name)
    except (FileNotFoundError, FileExistsError) as exc:
        click.echo(f"❌ {exc}", err=True)
        sys.exit(1)
    click.echo(f"♻️  {name} → {dest}")


@main.command("pause")
@click.pass_context
def pause(ctx: click.Context) -> None:
    curator.set_paused(ctx.obj["workspace"], True)
    click.echo("⏸️  curator pausado")


@main.command("resume")
@click.pass_context
def resume(ctx: click.Context) -> None:
    curator.set_paused(ctx.obj["workspace"], False)
    click.echo("▶️  curator reanudado")


if __name__ == "__main__":
    main()
