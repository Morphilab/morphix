# orchestration/dsl/cli.py
"""CLI del DSL: `morphix-workflow new|validate|list`.

Determinista y testeable: genera esqueletos válidos por tipo y valida
con el MISMO compilador/validador del runtime (sin LLM, sin DB).

Uso:
    morphix-workflow validate <archivo|nombre> [--workspace main]
    morphix-workflow new <nombre> --type=development [--workspace main]
    morphix-workflow list [--workspace main]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

# Esqueletos mínimos por tipo (mismo set que los presets globales).
_SKELETONS: dict[str, dict] = {
    "development": {
        "description": "Descompone, ejecuta en secuencia y agrega (development clásico)",
        "agents": ["developer", "analista"],
        "steps": [
            {"id": "descomponer", "kind": "decompose", "strategy": "flat", "output": "subtasks"},
            {
                "id": "ejecutar",
                "kind": "loop",
                "max_iter": 10,
                "over": "subtasks",
                "body": [
                    {
                        "id": "subtarea",
                        "kind": "agent",
                        "agent": "developer",
                        "goal": "Ejecuta la subtarea ($item) del objetivo: $query",
                        "retry_max": 2,
                    }
                ],
            },
            {"id": "agregar", "kind": "aggregate", "strategy": "confidence"},
        ],
    },
    "tdd": {
        "description": "Loop TDD: implementa/corrige hasta tests verdes",
        "agents": ["developer"],
        "steps": [
            {
                "id": "ciclo",
                "kind": "loop",
                "max_iter": 5,
                "until": {
                    "type": "tool",
                    "tool": "test_runner",
                    "check": "tests_all_pass",
                    "args": {"file_path": "."},
                },
                "body": [
                    {
                        "id": "implementar",
                        "kind": "agent",
                        "agent": "developer",
                        "goal": (
                            "Ciclo TDD ($iter/$max_iter) para: $query. "
                            "Resultado previo: $last_output — continúa, no repitas."
                        ),
                        "retry_max": 1,
                    }
                ],
            },
            {"id": "resumen", "kind": "aggregate", "strategy": "result"},
        ],
    },
    "collaborative": {
        "description": "Debate en rondas con síntesis del moderador",
        "agents": ["developer", "analista", "moderador"],
        "steps": [
            {
                "id": "rondas",
                "kind": "loop",
                "max_iter": 3,
                "body": [
                    {
                        "id": "opinion_developer",
                        "kind": "agent",
                        "agent": "developer",
                        "goal": "Opina sobre $query considerando: $last_output",
                    },
                    {
                        "id": "opinion_analista",
                        "kind": "agent",
                        "agent": "analista",
                        "goal": "Réplica a: $last_output",
                    },
                ],
            },
            {"id": "sintesis", "kind": "aggregate", "strategy": "moderator", "agent": "moderador"},
        ],
    },
    "coordinated": {
        "description": "Coordinación multi-agente: plan, ejecución, verificación, agregación",
        "agents": ["developer", "analista", "architect"],
        "steps": [
            {"id": "planificar", "kind": "decompose", "strategy": "flat", "output": "subtasks"},
            {
                "id": "ejecutar",
                "kind": "loop",
                "max_iter": 10,
                "over": "subtasks",
                "body": [
                    {
                        "id": "subtarea",
                        "kind": "agent",
                        "agent": "developer",
                        "goal": "Subtarea del plan ($query): $item",
                        "retry_max": 2,
                    }
                ],
            },
            {
                "id": "verificar",
                "kind": "agent",
                "agent": "analista",
                "goal": "Verifica la ejecución completa de: $query",
            },
            {"id": "agregar", "kind": "aggregate", "strategy": "confidence"},
        ],
    },
}

_DEFAULT_TOOLS = {
    "development": [
        "file_manager",
        "git_manager",
        "bash_manager",
        "test_runner",
        "diff_editor",
        "code_search",
    ],
    "tdd": ["file_manager", "diff_editor", "test_runner", "git_manager"],
    "collaborative": ["file_manager", "code_search", "web_search", "web_fetch"],
    "coordinated": [
        "file_manager",
        "git_manager",
        "bash_manager",
        "test_runner",
        "code_search",
        "diff_editor",
    ],
}

_NAME_RE = r"^[a-z][a-z0-9_]*$"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="morphix-workflow")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_new = sub.add_parser("new", help="Genera un esqueleto de workflow DSL")
    p_new.add_argument("name")
    p_new.add_argument(
        "--type",
        choices=list(_SKELETONS.keys()),
        default="development",
        help="tipo de esqueleto",
    )
    p_new.add_argument("--workspace", default="main")

    p_val = sub.add_parser("validate", help="Valida un archivo o preset DSL")
    p_val.add_argument("target", help="ruta a .yaml o nombre de preset")
    p_val.add_argument("--workspace", default=None)
    p_val.add_argument(
        "--conformance",
        action="store_true",
        help="además de validar, EJECUTA el preset contra el simulador "
        "(motor real + tools con firma real + lo difuso stubbed) — P3/RC2",
    )

    sub.add_parser("list", help="Lista workflows disponibles")
    return parser.parse_args(argv)


def _validate_one(raw: dict, catalog=None, errors: list[str] | None = None) -> bool:
    from orchestration.dsl.compiler import compile_workflow
    from orchestration.dsl.validator import validate_workflow

    bucket = errors if errors is not None else []
    try:
        cw = compile_workflow(raw, catalog=catalog)
        sem = validate_workflow(cw)
        if sem:
            bucket.extend(sem)
            return False
        return True
    except Exception as e:
        bucket.append(str(e))
        return False


def _run_conformance(raw: dict, workspace: str | None, out) -> int:
    """Ejecuta el preset contra el simulador y reporta."""
    import asyncio

    from orchestration.dsl.conformance import simulate_preset, structural_feedback_errors

    ws = workspace or "main"
    # guard estructural: loops ciegos sin feedback
    try:
        from orchestration.dsl.compiler import FileSystemCatalog
        from orchestration.dsl.compiler import compile_workflow as _cw

        errores_fb = structural_feedback_errors(_cw(raw, catalog=FileSystemCatalog(ws)).dsl)
    except Exception as e:  # ya reportado por la validación semántica
        errores_fb = [f"(no evaluable estructuralmente: {e})"]
    if errores_fb:
        out.write("⚠️  Feedback de iteración (RC2):\n")
        out.write("\n".join(f"  • {e}" for e in errores_fb) + "\n")

    report = asyncio.run(simulate_preset(raw, workspace=ws))
    out.write(report.summary() + "\n")
    return 0 if report.ok and not errores_fb else 1


def cmd_validate(target: str, workspace: str | None, out, conformance: bool = False) -> int:
    from orchestration.dsl.compiler import FileSystemCatalog

    if not Path(target).exists() or not target.endswith(".yaml"):
        # nombre de preset: resolver desde catálogo
        catalog = FileSystemCatalog(workspace)
        raw = catalog.load(target)
        if raw is None:
            out.write(f"❌ No existe el preset '{target}' (ni local, ni producto, ni global)\n")
            return 1
        errors: list[str] = []
        ok = _validate_one(raw, catalog=catalog, errors=errors)
        if not ok:
            out.write(f"❌ '{target}' inválido:\n" + "\n".join(f"  • {e}" for e in errors) + "\n")
            return 1
        if not conformance:
            out.write(f"✔ '{target}' válido\n")
            return 0
        return _run_conformance(raw, workspace, out)

    raw = yaml.safe_load(Path(target).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict) or "version" not in raw:
        out.write(f"❌ '{target}' no es un documento DSL (falta campo 'version')\n")
        return 1
    errors = []
    ok = _validate_one(raw, catalog=FileSystemCatalog(workspace), errors=errors)
    if not ok:
        out.write(f"❌ '{target}' inválido:\n" + "\n".join(f"  • {e}" for e in errors) + "\n")
        return 1
    if not conformance:
        out.write(f"✔ '{target}' válido\n")
        return 0
    return _run_conformance(raw, workspace, out)


def cmd_new(name: str, wf_type: str, workspace: str, out) -> int:
    import re

    from core.path_resolver import paths

    if not re.match(_NAME_RE, name):
        out.write(f"❌ Nombre inválido '{name}' (patrón {_NAME_RE})\n")
        return 1

    target_dir = paths.workspace_workflows_dir(workspace)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{name}.yaml"
    if target.exists():
        out.write(f"❌ '{target}' ya existe (no se sobreescribe)\n")
        return 1

    skeleton = _SKELETONS[wf_type]
    doc = {
        "version": 1,
        "name": name,
        "description": skeleton["description"],
        "agents": {"allowed": skeleton["agents"]},
        "tools": {"allowed": _DEFAULT_TOOLS[wf_type]},
        "project": {"required": False},
        "skills": wf_type in ("development", "tdd", "coordinated"),
        "steps": skeleton["steps"],
    }

    errors: list[str] = []
    if not _validate_one(doc, errors=errors):
        out.write("❌ El esqueleto generado no valida (bug interno):\n" + "\n".join(errors) + "\n")
        return 1

    target.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    out.write(f"✔ Generado: {target}\n")
    return 0


def cmd_list(workspace: str | None, out) -> int:
    from orchestration.loader import list_workflows

    names = list_workflows(workspace)
    out.write("\n".join(names) + ("\n" if names else "⚠️ sin workflows\n"))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.cmd == "new":
        return cmd_new(args.name, args.type, args.workspace, sys.stdout)
    if args.cmd == "validate":
        return cmd_validate(args.target, args.workspace, sys.stdout, conformance=args.conformance)
    return cmd_list(None, sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
