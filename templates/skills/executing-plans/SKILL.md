---
name: executing-plans
description: Úsala cuando existe un plan escrito por ejecutar tarea por tarea, con checkpoints de revisión.
---
# Executing Plans

## Flujo
1. Lee el plan completo; revisa críticamente: ¿hay huecos o contradicciones?
   Si los hay, detente y pregunta con `ask_clarification` antes de empezar.
2. Ejecuta UNA tarea a la vez: marca en progreso, sigue los pasos exactos,
   corre las verificaciones indicadas, marca completada.
3. Nunca saltes verificaciones ni "confíes" en que pasarán.
4. Si te bloqueas (test rojo persistente, instrucción ambigua): STOP y pregunta;
   no adivines.
5. Al terminar todo: suite completa + lint + tipos antes de declarar listo.
