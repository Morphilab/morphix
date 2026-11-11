---
name: verification-before-completion
description: Úsala antes de declarar algo terminado/arreglado/passing, antes de commitear o crear PRs. Evidencia antes de afirmaciones.
---
# Verification Before Completion

## Orden canónico
```bash
uv run ruff check .
uv run black --check .
uv run mypy core/ llm/ agents/ tools/ orchestration/ desktop/ viewer/
uv run pytest
```

## Reglas
- Corre TODOS los comandos y lee la salida real. "Debería pasar" no cuenta.
- Afirma solo lo que viste impreso: "pytest: 1240 passed".
- Si falla algo inesperado: systematic-debugging, no excuses.
- Diferencia entre "mi cambio funciona" y "el sistema sigue funcionando":
  necesitas ambas.
