---
name: test-driven-development
description: Úsala al implementar cualquier feature o bugfix, ANTES de escribir el código de producción. Ciclo rojo-verde estricto.
---
# Test-Driven Development

## Ciclo (sin excepciones)
1. ROJO: escribe el test más pequeño que falle por la razón correcta.
   Corre `poetry run pytest <archivo> -v` y CONFIRMA el fallo esperado.
2. VERDE: implementa el mínimo código que hace pasar el test. Sin extras.
3. REFACTOR: limpia manteniendo verde. Corre los tests otra vez.

## Reglas
- Si el test pasa de inmediato, no estaba probando nada nuevo.
- Un bug = un test de regresión que reproduce el bug exacto primero.
- Nota: para flujos de desarrollo usa además el workflow nativo `tdd`,
  que orquesta este ciclo entre agentes.
- Suite completa antes de commit: los tests se rompen entre sí más de lo
  que crees.
