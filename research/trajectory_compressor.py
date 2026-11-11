# research/trajectory_compressor.py — compresión y muestreo de trayectorias
"""Comprime trayectorias JSONL del BatchRunner para entrenamiento/evaluación.

Contratos:

- **Muestreo determinista**: ``sample_percent`` con ``seed`` fija produce
  siempre el mismo subconjunto (reproducibilidad de evals).
- **Dry-run**: ``dry_run=True`` calcula y reporta sin escribir nada.
- **Fidelidad del input**: el ``prompt`` NUNCA se trunca — la entrada es la
  unidad de verdad del dataset; solo se acota el output (response/reasoning).
- **Presupuesto por trayectoria**: ``target_max_tokens`` (estimación
  chars/4 por defecto; inyectable un tokenizador real) reduce los campos
  largos hasta caber.

El formato de entrada/salida es JSONL: una fila por prompt, tal como la
escribe :mod:`research.batch_runner`.
"""

import json
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TRUNCATED_MARKER = "…[truncado por compresión]"
DEFAULT_MAX_CHARS_RESPONSE = 4000
DEFAULT_MAX_CHARS_REASONING = 2000


def _estimate_tokens(text: str) -> int:
    """Heurística chars/4 — documentada; un tokenizador real es inyectable."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = max(0, max_chars - len(TRUNCATED_MARKER))
    return text[:cut] + TRUNCATED_MARKER


def _fit_to_budget(text: str, token_budget: int, count: Callable[[str], int]) -> str:
    """Mayor prefijo (con marcador) cuyo costo en tokens cabe en el presupuesto.

    Búsqueda binaria sobre la longitud: el contador puede ser un tokenizador
    real (tiktoken) o la heurística — el presupuesto se respeta con el MISMO
    contador que inyecte la config, sin conversiones chars/tokens adivinadas.
    """
    if token_budget <= 0 or not text:
        return ""
    lo, hi, best = 0, len(text), 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if count(_truncate(text, mid)) <= token_budget:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return _truncate(text, best)


@dataclass
class CompressionConfig:
    """Parámetros de compresión."""

    target_max_tokens: int | None = None
    sample_percent: float | None = None
    seed: int = 42
    dry_run: bool = False
    max_chars_response: int = DEFAULT_MAX_CHARS_RESPONSE
    max_chars_reasoning: int = DEFAULT_MAX_CHARS_REASONING
    tokenizer: Callable[[str], int] | None = None

    def __post_init__(self) -> None:
        if self.sample_percent is not None and not 0 < self.sample_percent <= 100:
            raise ValueError("sample_percent debe estar en (0, 100]")
        if self.target_max_tokens is not None and self.target_max_tokens < 1:
            raise ValueError("target_max_tokens debe ser >= 1")
        if self.max_chars_response < 1 or self.max_chars_reasoning < 1:
            raise ValueError("los topes de chars deben ser >= 1")

    def count_tokens(self, text: str) -> int:
        if self.tokenizer is not None:
            return int(self.tokenizer(text))
        return _estimate_tokens(text)


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Carga un JSONL; las líneas ilegibles se saltan con warning (on_error)."""
    entries: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("%s:%d ilegible, se salta", path, lineno)
                continue
            if isinstance(row, dict):
                entries.append(row)
    return entries


def write_jsonl(path: str | Path, entries: list[dict[str, Any]]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for row in entries:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def sample_entries(
    entries: list[dict[str, Any]], sample_percent: float, seed: int
) -> list[dict[str, Any]]:
    """Submuestreo determinista — random.Random(seed) nunca toca el global."""
    count = max(1, round(len(entries) * sample_percent / 100))
    rng = random.Random(seed)
    return rng.sample(entries, k=min(count, len(entries)))


def compress_entry(entry: dict[str, Any], config: CompressionConfig) -> dict[str, Any]:
    """Copia comprimida de una trayectoria; el prompt queda intacto."""
    out = dict(entry)
    response = str(entry.get("response") or "")
    reasoning = str(entry.get("reasoning") or "")
    count = config.count_tokens

    if config.target_max_tokens is not None:
        # Presupuesto: reducir primero reasoning (diagnóstico), luego response.
        prompt_cost = count(str(entry.get("prompt") or ""))
        budget = max(1, config.target_max_tokens - prompt_cost)
        if count(reasoning) + count(response) > budget:
            if reasoning:
                reason_budget = min(count(reasoning), max(0, budget // 2))
                reasoning = _fit_to_budget(reasoning, reason_budget, count)
            resp_budget = max(1, budget - count(reasoning))
            response = _fit_to_budget(response, resp_budget, count)
        # Topes de chars también aplican en modo presupuesto (mínimo de ambos).
        reasoning = _truncate(reasoning, config.max_chars_reasoning)
        response = _truncate(response, config.max_chars_response)
    else:
        reasoning = _truncate(reasoning, config.max_chars_reasoning)
        response = _truncate(response, config.max_chars_response)

    if reasoning:
        out["reasoning"] = reasoning
    else:
        out.pop("reasoning", None)
    out["response"] = response
    out["compressed"] = True
    return out


def compress_file(
    input_path: str | Path,
    output_path: str | Path | None = None,
    config: CompressionConfig | None = None,
) -> dict[str, Any]:
    """Comprime (y opcionalmente muestrea) un JSONL de trayectorias.

    Devuelve un resumen: entradas leídas, muestreadas y escritas (o que se
    habrían escrito en dry-run).
    """
    cfg = config or CompressionConfig()
    entries = load_jsonl(input_path)
    if cfg.sample_percent is not None:
        entries = sample_entries(entries, cfg.sample_percent, cfg.seed)
    compressed = [compress_entry(e, cfg) for e in entries]

    summary: dict[str, Any] = {
        "input": str(input_path),
        "entries": len(entries),
        "compressed": len(compressed),
        "dry_run": cfg.dry_run,
    }
    if cfg.dry_run:
        logger.info("[dry-run] se escribirían %d entradas en %s", len(compressed), output_path)
        return summary

    if output_path is None:
        src = Path(input_path)
        output_path = src.with_suffix(".compressed.jsonl")
    write_jsonl(output_path, compressed)
    summary["output"] = str(output_path)
    return summary
