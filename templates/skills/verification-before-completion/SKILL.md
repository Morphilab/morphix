---
name: verification-before-completion
description: Úsala antes de declarar algo terminado/arreglado/passing, antes de commitear o crear PRs. Evidencia antes de afirmaciones.
---
# Verification Before Completion

## Orden canónico (AGENTS.md)
```bash
poetry run ruff check .
poetry run black --check .
poetry run mypy core/ llm/ agents/ tools/ orchestration/ desktop/
poetry run pytest
```

## Reglas
- Corre TODOS los comandos y lee la salida real. "Debería pasar" no cuenta.
- Afirma solo lo que viste impreso: "pytest: 1240 passed".
- Si falla algo inesperado: systematic-debugging, no excuses.
- Diferencia entre "mi cambio funciona" y "el sistema sigue funcionando":
  necesitas ambas.
