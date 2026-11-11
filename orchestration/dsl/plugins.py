# orchestration/dsl/plugins.py
"""Primitivas plugin built-in del motor.

Se importan por efecto (registro) desde el runtime adapter — los presets
pueden usarlas vía ``kind: plugin``. Cada plugin valida sus params
fail-loud; nunca asume determinismo del modelo (solo transforma estado).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from orchestration.dsl.engine import EngineError
from orchestration.dsl.registry import register_primitive

logger = logging.getLogger(__name__)


def _mark_degraded(runtime: Any, label: str, path: str) -> None:
    """Registra una degradación de plugin con path no recuperable.

    El adapter guarda label+path; las degradaciones de plugins/tools no las
    recupera un veredicto de ciclo (solo los stalls de agentes), así que el
    path lleva el prefijo ``plugin:``.
    """
    mark = getattr(runtime, "mark_degraded", None)
    if callable(mark):
        mark(label, path)
        return
    degraded = getattr(runtime, "_degraded_steps", None)
    if degraded is not None:
        degraded.append(label)


@register_primitive("cap_text")
async def _cap_text(step: Any, state: Any, runtime: Any, qpath: str) -> None:
    """Acota una var de texto en frontera de palabra (dieta de prompt).

    params: ``{var, out, max_chars}`` — el preset declara cuánto del output
    de un paso viaja en los prompts siguientes; la var ORIGINAL queda
    íntegra para los consumidores que necesiten el texto completo.
    """

    var = step.params.get("var")
    out_name = step.params.get("out")
    if not var or not out_name:
        raise EngineError(f"{qpath}: cap_text requiere params var y out")
    try:
        max_chars = int(step.params.get("max_chars") or 0)
    except (TypeError, ValueError):
        raise EngineError(f"{qpath}: cap_text: max_chars no numérico") from None
    if max_chars <= 0:
        raise EngineError(f"{qpath}: cap_text: max_chars debe ser > 0")
    raw = state.vars.get(var)
    if raw is None:
        raise EngineError(f"{qpath}: cap_text: variable '{var}' sin valor")
    from core.utils import truncar_en_palabra

    state.vars[out_name] = truncar_en_palabra(str(raw), max_chars)
    logger.info("cap_text %s: %s → %s (≤%d chars)", qpath, var, out_name, max_chars)


def _split_items(text: str) -> list[str]:
    """Divide texto en ítems: líneas, viñetas markdown o enumeraciones."""
    text = text.strip()
    if not text:
        return []
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", line).strip()
        if line:
            lines.append(line)
    return lines


@register_primitive("text_to_list")
async def _text_to_list(step: Any, state: Any, runtime: Any, qpath: str) -> None:
    """Convierte una variable texto (multilínea/viñetas) en lista.

    params: {var: "nombre_var_texto", out: "nombre_var_lista", marker?}.

    Con ``marker``, solo se divide el texto POSTERIOR a su última aparición:
    el agente entrega prosa + una lista final bajo un encabezado conocido, y
    la prosa NO se cuela como ítems (sin esto, cada línea del documento —
    títulos incluidos — era una "subtarea" y el loop quemaba su tope
    iterando sobre ruido).
    """
    var = step.params.get("var")
    out_name = step.params.get("out")
    marker = step.params.get("marker")
    max_items = step.params.get("max_items")
    if not var or not out_name:
        raise EngineError(f"{qpath}: text_to_list requiere params var y out")
    raw = state.vars.get(var)
    if raw is None:
        raise EngineError(f"{qpath}: text_to_list: variable '{var}' sin valor")
    text = str(raw)
    marker_ausente = False
    if marker:
        idx = text.rfind(str(marker))
        if idx != -1:
            text = text[idx + len(str(marker)) :]
        else:
            # el modelo omitió el formato pedido: dividir el texto completo
            # convertiría la prosa en ítems (el ruido que el marker existe
            # para evitar) — contrato violado ⇒ lista vacía + degradada.
            marker_ausente = True
            text = ""
    items = _split_items(text)
    truncated = False
    if max_items and len(items) > int(max_items):
        # cap declarado por el preset: truncar ANTES de ejecutar — el corte
        # a mitad de loop ya quemó el presupuesto de los ítems que sí corrieron.
        dropped = len(items) - int(max_items)
        items = items[: int(max_items)]
        truncated = True
        logger.warning(
            "text_to_list %s: lista truncada al cap declarado (%d) — %d ítems "
            "descartados ANTES de ejecutar",
            qpath,
            len(items),
            dropped,
        )
    state.vars[out_name] = items
    if marker and (not items or marker_ausente):
        # el loop over NO ejecutaría ningún ciclo — sin este aviso el run
        # terminaba "completed/Alta" sin haber hecho nada.
        logger.warning(
            "text_to_list %s: %s — el loop NO ejecutará ciclos",
            qpath,
            (
                "marker ausente en la salida del agente"
                if marker_ausente
                else "0 ítems tras el marker"
            ),
        )
        _mark_degraded(runtime, f"plugin:text_to_list:{out_name}", f"plugin:{qpath}")
    elif truncated:
        _mark_degraded(runtime, f"plugin:text_to_list:{out_name}:truncada", f"plugin:{qpath}")
    elif marker:
        logger.info("text_to_list %s: %d ítems tras el marker", qpath, len(items))
    else:
        logger.debug("text_to_list %s: %s → %d items", qpath, var, len(items))


@register_primitive("filtrar_items")
async def _filtrar_items(step: Any, state: Any, runtime: Any, qpath: str) -> None:
    """Descarta ítems que matchean patrones prohibidos ANTES del loop.

    params: {var, out, patrones: [regex], contrato?: bool} (case-insensitive).
    Un plan con subtareas de análisis o documentación no se ejecuta: la
    prohibición en el query del decompose es soft (el modelo la ignora), este
    filtro es determinista. Si se descartó algo, el plugin marca degradación —
    un plan recortado en silencio mentiría. `contrato: true` declara que los
    descartes son POR CONTRATO del preset (p.ej. sdd escribe los tests en el
    body): el plan sigue mostrando los saltados, pero no degradan el proceso.
    La var ORIGINAL queda intacta.
    """
    var = step.params.get("var")
    out_name = step.params.get("out")
    patrones = step.params.get("patrones")
    contrato = bool(step.params.get("contrato", False))
    if not var or not out_name:
        raise EngineError(f"{qpath}: filtrar_items requiere params var y out")
    if not patrones:
        raise EngineError(f"{qpath}: filtrar_items: patrones vacío (declara qué descartar)")
    raw = state.vars.get(var)
    if raw is None:
        raise EngineError(f"{qpath}: filtrar_items: variable '{var}' sin valor")
    items = list(raw) if isinstance(raw, list) else _split_items(str(raw))
    try:
        regexes = [re.compile(str(p), re.IGNORECASE) for p in patrones]
    except re.error as e:
        raise EngineError(f"{qpath}: filtrar_items: patrón inválido: {e}") from None
    kept = [it for it in items if not any(r.search(str(it)) for r in regexes)]
    state.vars[out_name] = kept
    dropped = len(items) - len(kept)
    if dropped:
        # el goal descartado va al log (truncado): sin él, el "N descartados"
        # del scorecard no es auditable y no se puede distinguir un falso
        # positivo del filtro de un descarte correcto.
        descartados = [str(it)[:100] for it in items if any(r.search(str(it)) for r in regexes)]
        logger.warning(
            "filtrar_items %s: %d ítem(s) descartados de %d: %s",
            qpath,
            dropped,
            len(items),
            " | ".join(descartados),
        )
        # Los descartados REALES viajan al estado: el plan mapea por índice y
        # sin ellos el ítem saltado mostraba el goal de un ítem implementado.
        state.vars[f"_dropped_{out_name}"] = descartados
        state.vars[f"_dropped_contrato_{out_name}"] = contrato
        if not contrato:
            _mark_degraded(runtime, f"plugin:filtrar_items:{out_name}", f"plugin:{qpath}")
    else:
        logger.debug("filtrar_items %s: %d → %d ítems", qpath, len(items), len(kept))


_MD_DECOR = "`*_~->"


def _strip_md_decor(text: str) -> str:
    """Quita la decoración markdown que ENVUELVE una línea (backticks,
    negritas, blockquote, viñeta): el modelo escribe el marcador con
    formato ('- `TEST_PATH: x.py`') y el prefijo deja de matchear.
    """
    s = text.strip()
    while s and s[0] in _MD_DECOR:
        s = s[1:].lstrip()
    while s and s[-1] in _MD_DECOR:
        s = s[:-1].rstrip()
    return s.strip()


@register_primitive("pick_line")
async def _pick_line(step: Any, state: Any, runtime: Any, qpath: str) -> None:
    """Extrae la primera línea con un prefijo dado hacia una var escalar.

    params: {var, out, prefix, fallback_glob?} — p.ej. el agente cierra su
    respuesta con "TEST_PATH: tests/test_x.py" y el until del loop verifica
    SOLO ese archivo (la ruta del test es una variable de runtime, decidida
    por el modelo pero consumida por el motor).

    Sin línea coincidente: warning con el head de la salida (diagnóstico) y,
    si ``fallback_glob`` está declarado, se INFIERE la ruta más reciente que
    matchee el glob dentro del proyecto — determinista y marcada como
    degradada (ruta inferida). El fallback convierte un contrato violado en
    un run degradado-pero-productivo en vez de un fallo del loop entero.
    Sin fallback la var queda "" (fail-soft): la verificación con ruta
    vacía falla loud en la tool y queda marcada como degradada.
    """
    var = step.params.get("var")
    out_name = step.params.get("out")
    prefix = str(step.params.get("prefix") or "")
    fallback_glob = str(step.params.get("fallback_glob") or "")
    if not var or not out_name or not prefix:
        raise EngineError(f"{qpath}: pick_line requiere params var, out y prefix")
    raw = state.vars.get(var)
    if raw is None:
        raise EngineError(f"{qpath}: pick_line: variable '{var}' sin valor")
    value = ""
    for line in str(raw).splitlines():
        cleaned = _strip_md_decor(line)
        if cleaned.lower().startswith(prefix.lower()):
            value = _strip_md_decor(cleaned[len(prefix) :])
            break
    if not value:
        logger.warning(
            "pick_line %s: ninguna línea con prefijo %r en %s — head: %r",
            qpath,
            prefix,
            var,
            str(raw)[:200],
        )
        # rutas ya consumidas por subtareas previas del mismo loop: la
        # inferencia no debe repetirlas (todas las subtareas verificando el
        # MISMO archivo = verificación débil)
        usadas = set(state.vars.get("_test_paths_usados") or ())
        inferred = _infer_latest_glob_match(runtime, fallback_glob, excluir=usadas)
        if inferred:
            value = inferred
            _mark_degraded(runtime, f"pick_line:{out_name}:ruta-inferida", f"plugin:{qpath}")
            logger.warning(
                "pick_line %s: ruta INFERIDA por fallback (%s → %s) — cuenta como degradada",
                qpath,
                fallback_glob,
                value,
            )
    if value:
        # registrar IN PLACE la lista compartida (los overlays de loops
        # paralelos copian la referencia: rebindear perdería merges)
        usados = state.vars.get("_test_paths_usados")
        if not isinstance(usados, list):
            usados = []
            state.vars["_test_paths_usados"] = usados
        if value not in usados:
            usados.append(value)
    state.vars[out_name] = value


def _infer_latest_glob_match(
    runtime: Any, fallback_glob: str, excluir: set[str] | None = None
) -> str:
    """La ruta MÁS RECIENTE que matchea el glob dentro del proyecto real.

    La reescritura de un archivo actualiza su mtime: el candidato más
    reciente es el que el paso actual acaba de escribir. ``project_root``
    llega RELATIVO (p. ej. 'code_projects/edd') y se resuelve contra la
    base del workspace — exactamente como lo hacen las tools. Las rutas en
    ``excluir`` (ya consumidas por subtareas previas) se saltan. Sin
    proyecto real (None/'.') no se toca el filesystem — el fallback no
    aplica en
    runs conversacionales ni en el simulador de conformance.
    """
    if not fallback_glob:
        return ""
    root = getattr(runtime, "project_root", None)
    if not root or str(root).strip() in ("", "."):
        return ""
    import glob as _glob
    import os

    if os.path.isabs(str(root)):
        base = str(root)
    else:
        from core.constants import PROJECTS_DIR_NAME
        from core.path_resolver import paths

        workspace = str(getattr(runtime, "workspace", "") or "main")
        normalized = str(root)
        if not normalized.startswith(f"{PROJECTS_DIR_NAME}/"):
            normalized = f"{PROJECTS_DIR_NAME}/{normalized}"
        base = str(paths.memory_dir(workspace) / normalized)

    excluir_norm = {str(e).replace(os.sep, "/").strip("/") for e in (excluir or set())}
    candidatas = sorted(
        _glob.glob(os.path.join(base, fallback_glob)),
        key=lambda p: os.path.getmtime(p),
        reverse=True,
    )
    for cand in candidatas:
        rel = os.path.relpath(cand, base).replace(os.sep, "/")
        if rel.strip("/") not in excluir_norm:
            return rel
    return ""


def _slugify(texto: str, max_len: int) -> str:
    """texto → slug ascii ``[a-z0-9_]`` acotado (corte en separador)."""
    import unicodedata

    plano = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", plano.lower()).strip("_")
    if len(slug) <= max_len:
        return slug
    corte = slug[:max_len].rsplit("_", 1)[0]
    return corte or slug[:max_len]


@register_primitive("slug_path")
async def _slug_path(step: Any, state: Any, runtime: Any, qpath: str) -> None:
    """Deriva una ruta de archivo DETERMINISTA a partir de una var de texto.

    params: ``{var, out, prefix?, suffix?, max_len?}`` — p.ej. la ruta del test
    derivada de la query (``tests/test_<slug>.py``) en vez de depender de que
    el modelo emita ``TEST_PATH:``. Una ruta declarada por el preset no puede
    quedar sin resolver, y el goal puede nombrarla para que el agente escriba
    ahí — el mismo contrato que los presets guiados por lista ya usan con
    índice de ciclo. ``pick_line`` + ``fallback_glob`` siguen existiendo como
    mecanismo para quien prefiera que el modelo nombre el archivo.
    """
    var = step.params.get("var")
    out_name = step.params.get("out")
    if not var or not out_name:
        raise EngineError(f"{qpath}: slug_path requiere params var y out")
    raw = state.vars.get(var)
    if raw is None:
        raise EngineError(f"{qpath}: slug_path: variable '{var}' sin valor")
    prefix = str(step.params.get("prefix") or "")
    suffix = str(step.params.get("suffix") or "")
    try:
        max_len = int(step.params.get("max_len") or 40)
    except (TypeError, ValueError):
        raise EngineError(f"{qpath}: slug_path: max_len no numérico") from None
    ruta = f"{prefix}{_slugify(str(raw), max_len)}{suffix}"
    state.vars[out_name] = ruta
    logger.info("slug_path %s: %s → %s", qpath, var, ruta)


def _glob_a_regex(patron: str) -> re.Pattern[str]:
    """Glob de rutas → regex (** = cualquier profundidad, * sin cruzar /)."""
    partes: list[str] = []
    i = 0
    while i < len(patron):
        if patron[i : i + 3] == "**/":
            partes.append("(?:[^/]+/)*")
            i += 3
        elif patron[i : i + 2] == "**":
            partes.append(".*")
            i += 2
        elif patron[i] == "*":
            partes.append("[^/]*")
            i += 1
        elif patron[i] == "?":
            partes.append("[^/]")
            i += 1
        else:
            partes.append(re.escape(patron[i]))
            i += 1
    return re.compile("^" + "".join(partes) + "$")


@register_primitive("files_del_run")
async def _files_del_run(step: Any, state: Any, runtime: Any, qpath: str) -> None:
    """Los archivos que ESTE run escribió y que matchean un glob.

    params: ``{out, glob}`` — p.ej. la verificación del agregado recolecta
    ``tests/**/*.py`` de entre los archivos escritos por el run, de modo que
    verifica SOLO lo que el run tocó: una suite preexistente en rojo no
    penaliza la entrega (y lo que el run rompió sin tocar, tampoco se ve —
    documentado como trade-off del preset).

    Sin runtime con ``files_written`` (fakes) o sin coincidencias la var queda
    lista vacía: el consumidor (p.ej. test_runner con file_paths) fail-fast por
    sin-medición en vez de heredar una verificación global.
    """
    out_name = step.params.get("out")
    patron = str(step.params.get("glob") or "")
    if not out_name or not patron:
        raise EngineError(f"{qpath}: files_del_run requiere params out y glob")
    escritos = getattr(runtime, "files_written", None)
    if callable(escritos):  # method o property: ambos estilos de runtime
        escritos = escritos()
    archivos = [str(f) for f in (escritos or []) if str(f).strip()]
    regex = _glob_a_regex(patron)
    seleccion = [f for f in archivos if regex.match(f)]
    state.vars[out_name] = seleccion
    state.vars["last_output"] = ", ".join(seleccion)
    logger.info(
        "files_del_run %s: %d/%d artefactos del run matchean %s",
        qpath,
        len(seleccion),
        len(archivos),
        patron,
    )


@register_primitive("dag_to_levels")
async def _dag_to_levels(step: Any, state: Any, runtime: Any, qpath: str) -> None:
    """Aplana un dag de descomposición en niveles topológicos consumibles.

    params: {var: "var_con_resultado_decompose", out: "var_niveles"}.

    Escribe en ``out`` una lista de niveles (cada nivel = lista de títulos
    de task sin dependencias pendientes): el workflow la consume con un
    loop externo sobre niveles y un loop interno sobre ``item`` (cada
    nivel). Ciclos o ids rotos ya fueron validados por el decomposer.
    """
    var = step.params.get("var")
    out_name = step.params.get("out")
    if not var or not out_name:
        raise EngineError(f"{qpath}: dag_to_levels requiere params var y out")
    raw = state.vars.get(var)
    if not isinstance(raw, dict) or not isinstance(raw.get("levels"), list):
        raise EngineError(f"{qpath}: dag_to_levels: '{var}' no es un resultado de decompose dag")
    niveles: list[list[str]] = []
    for nivel in raw["levels"]:
        títulos = [
            str(t.get("title") or t.get("id") or "").strip() for t in nivel if isinstance(t, dict)
        ]
        niveles.append([t for t in títulos if t])
    state.vars[out_name] = niveles
    state.vars["last_output"] = str(raw.get("tasks") or "")
    logger.info(
        "dag_to_levels: %d niveles (%s)",
        len(niveles),
        ", ".join(str(len(n)) for n in niveles),
    )
