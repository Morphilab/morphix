---
name: writing-plans
description: Úsala cuando tienes requisitos/spec para una tarea multi-paso, ANTES de tocar código. Produce un plan ejecutable tarea por tarea con verificación.
---
# Writing Plans

## Estructura del plan (guárdalo en `dev/planes/`)
- **Goal** y contexto en ≤5 líneas.
- Tareas numeradas, cada una con: archivos a tocar, pasos exactos,
  comando de verificación (`poetry run pytest ...`), commit message.
- Orden por dependencias; tareas independientes agrupadas al final.
- Criterios de aceptación globales: ruff → black --check → mypy → pytest.

## Reglas
- Cada paso debe ser ejecutable por otro agente sin re-leer esta conversación.
- Nada de placeholders: nombres de variables/archivos reales (verifica contra el código).
- Tests primero en cada tarea de código (ver test-driven-development).
