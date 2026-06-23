"""
LSP Manager — Análisis de código con Jedi + Ruff diagnostics.
"""

import asyncio
import json as _json
import logging
import os
from pathlib import Path

import jedi

from agents.audit import log_operation

logger = logging.getLogger(__name__)


class LspManager:
    def __init__(self, root_path: str):
        self.root_path = Path(root_path).resolve()
        self.project = jedi.Project(str(self.root_path))

    async def _get_script(self, file: str, line: int, character: int):
        file_path = (self.root_path / file).resolve()
        try:
            file_path.relative_to(self.root_path)
        except ValueError:
            raise ValueError(f"Archivo fuera del workspace: {file}")
        if not file_path.exists():
            raise FileNotFoundError(f"Archivo no encontrado: {file_path}")
        code = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
        return jedi.Script(code, path=str(file_path), project=self.project)

    async def definition(self, file: str, line: int, character: int) -> str:
        try:
            script = await self._get_script(file, line, character)
            defs = script.goto(line + 1, character)
            if not defs:
                # Fallback: search the project for functions with that name near the requested line
                code = await asyncio.to_thread(
                    Path(self.root_path / file).read_text, encoding="utf-8"
                )
                lines = code.splitlines()
                if line < len(lines) and lines[line].strip():
                    possible_name = lines[line].strip().split("def ")[-1].split("(")[0].strip(":")
                    full_code = await asyncio.to_thread(Path(self.root_path / file).read_text)
                    script_all = jedi.Script(
                        full_code, path=str(self.root_path / file), project=self.project
                    )
                    names = script_all.get_names(all_scopes=True)
                    for name in names:
                        if name.name == possible_name and name.type == "function":
                            return f"📍 {name.module_path or 'built-in'} L{name.line} C{name.column}: {name.description}"
                return "No se encontró definición."
            return "\n".join(
                f"📍 {d.module_path or 'built-in'} L{d.line} C{d.column}: {d.description}"
                for d in defs
            )
        except Exception as e:
            logger.error(f"LSP definition error: {e}")
            return f"Error en LSP: {str(e)[:150]}"

    async def hover(self, file: str, line: int, character: int) -> str:
        try:
            script = await self._get_script(file, line, character)
            signatures = script.get_signatures()
            if signatures:
                sig = signatures[0]
                return (
                    f"```python\n{sig.to_string()}\n```\n\n{sig.docstring() or 'Sin documentación'}"
                )
            return "Sin información disponible."
        except Exception as e:
            logger.error(f"LSP hover error: {e}")
            return f"Error en LSP: {str(e)[:150]}"

    async def diagnostics(self, file: str | None = None) -> str:
        """Analiza archivos Python en busca de problemas (vacíos, sintaxis, etc.)."""

        def _check_all(root_path: str, file: str | None) -> list[str]:
            reports: list[str] = []
            root = Path(root_path)

            def _check_file(filepath: Path, relpath: str) -> None:
                if filepath.stat().st_size == 0:
                    reports.append(f"Archivo vacío: {relpath}")
                    return
                try:
                    code = filepath.read_text(encoding="utf-8")
                    script = jedi.Script(code, path=str(filepath), project=self.project)
                    diag = script.get_names(all_scopes=True, references=False, definitions=False)
                    if not diag:
                        reports.append(f"Sin símbolos detectados: {relpath}")
                except SyntaxError as e:
                    reports.append(f"Error de sintaxis en {relpath}: {e}")
                except Exception as e:
                    logger.debug(f"Error analizando {relpath}: {e}")

            if file:
                full = root / file
                if full.is_file():
                    _check_file(full, file)
                else:
                    reports.append(f"Archivo no encontrado: {file}")
            else:
                for py_file in root.rglob("*.py"):
                    parts = py_file.parts
                    if any(p.startswith(".") or p == "__pycache__" for p in parts):
                        continue
                    rel = str(py_file.relative_to(root))
                    _check_file(py_file, rel)

            return reports

        reports = await asyncio.to_thread(_check_all, str(self.root_path), file)
        if not reports:
            reports.append("No se detectaron problemas.")
        return "\n".join(reports)

    async def ruff_check(self, file: str | None = None, fix: bool = False) -> str:
        """Ejecuta ruff como linter real con salida estructurada.

        Args:
            file: Archivo específico a analizar (None = todo el proyecto).
            fix: Si True, aplica auto-correcciones (ruff --fix).

        Returns:
            Reporte estructurado de issues encontrados.
        """
        cmd = ["ruff", "check", "--output-format=json"]
        if fix:
            cmd.append("--fix")
        target = str(self.root_path / file) if file else str(self.root_path)
        cmd.append(target)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.root_path),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            result_stdout = stdout.decode()

            if not result_stdout.strip():
                return "✅ Ningún problema detectado por ruff."

            issues = _json.loads(result_stdout)
            if not issues or not isinstance(issues, list):
                return "✅ Ningún problema detectado por ruff."
            issues = [i for i in issues if i is not None and isinstance(i, dict)]
            if not issues:
                return "✅ Ningún problema detectado por ruff."

            lines = [f"🔍 Ruff encontró {len(issues)} problema(s):\n"]
            for issue in issues[:50]:  # cap at 50 issues
                location = issue.get("filename", "?")
                loc_data = issue.get("location") or {}
                line_no = loc_data.get("row", "?")
                col = loc_data.get("column", "?")
                code = issue.get("code", "?")
                message = issue.get("message", "")
                fixable = "🔧" if (issue.get("fix") or {}).get("applicability") else "  "
                rel_path = os.path.relpath(location, str(self.root_path))
                lines.append(f"{fixable} {rel_path}:{line_no}:{col}  [{code}] {message}")

            return "\n".join(lines)

        except TimeoutError:
            return "⏱️ Ruff excedió el tiempo límite (60s)."
        except FileNotFoundError:
            return "❌ Ruff no está instalado en el entorno."
        except Exception as e:
            logger.error(f"Ruff check error: {e}")
            return f"❌ Error ejecutando ruff: {e!s}"

    async def references(self, file: str, line: int, character: int) -> str:
        """Busca todas las referencias a un símbolo en el proyecto."""
        try:
            script = await self._get_script(file, line, character)
            refs = script.get_references()
            if not refs:
                return "No se encontraron referencias."
            return "\n".join(
                f"📍 {r.module_path or 'built-in'} L{r.line} C{r.column}: {r.description}"
                for r in refs
            )
        except Exception as e:
            logger.error(f"LSP references error: {e}")
            return f"Error en LSP references: {str(e)[:150]}"


# Herramienta registrada
from tools.registry import tools_registry


@tools_registry.register("lsp_manager")
async def lsp_manager_tool(
    action: str = "diagnostics",
    file: str = "",
    line: int = 0,
    character: int = 0,
    project_root: str | None = None,
    workspace: str = "main",
    **kwargs,
) -> str:
    """Herramienta LSP profesional - usa project_root si se proporciona."""
    if not action:
        return "❌ lsp_manager requiere un parámetro 'action' (diagnostics, definition, hover, references)"
    if not project_root:
        return (
            "❌ Se requiere 'project_root' para usar LSP. " "Especifica el directorio del proyecto."
        )

    from core.path_resolver import paths

    full_root = paths.code_projects_dir(workspace, project_root).resolve()
    if not full_root.exists():
        return f"❌ El proyecto {project_root} no existe aún."

    manager = LspManager(str(full_root))

    try:
        if action == "definition":
            result = await manager.definition(file, line, character)
            log_operation("lsp_definition", str(file)[:200], success=True)
            return result
        elif action == "hover":
            result = await manager.hover(file, line, character)
            log_operation("lsp_hover", str(file)[:200], success=True)
            return result
        elif action == "diagnostics":
            return await manager.diagnostics(file if file else None)
        elif action == "ruff_check":
            fix = kwargs.get("fix", False)
            return await manager.ruff_check(file if file else None, fix=fix)
        elif action == "references":
            return await manager.references(file, line, character)
        else:
            return f"Acción '{action}' no soportada."
    except Exception as e:
        logger.error(f"LSP error: {e}")
        return f"Error en LSP: {str(e)[:150]}"
