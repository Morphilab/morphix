---
name: systematic-debugging
description: Úsala ante cualquier bug, test fallido o comportamiento inesperado, ANTES de proponer fixes. Cuatro fases: raíz, patrón, hipótesis, fix.
---
# Systematic Debugging

## Fase 1 — Raíz
Lee SIEMPRE el error completo. Reproduce el fallo con el comando mínimo.
Nunca fixes sin entender por qué falla.

## Fase 2 — Patrón
¿Funciona en aislamiento y falla en suite? ¿Falla siempre o intermitente?
Compara con código similar que SÍ funciona.

## Fase 3 — Hipótesis
Formula LA causa concreta ("X retorna None porque Y") y verifica con logs
o un test mínimo ANTES de editar código de producción.

## Fase 4 — Fix
El fix mínimo que elimina la CAUSA. Luego: ¿este error puede ocurrir en otro
lado? Añade regresión. Corre la suite completa.
