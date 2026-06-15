# MEMORY.md - Morphix Self-Healing Memory

## Reglas estrictas
- Solo escribir después de `_llm_critique` (strict write discipline).
- `user_profile` es **protegido permanentemente** (nunca se borra).
- Self-healing cada 60s vía Kairos Daemon.

## Archivos clave
- `user_profile.md` → nombre, país, preferencias (persistente)
- `workflow_subtask_*.md` → trazabilidad
- `kairos_daemon_heartbeat.md` → estado del daemon
- `security_private.md` → intentos de distillation (oculto)

## Capas
Capa 0: In-memory + FAISS
Capa 1: Archivos .md temáticos
Capa 2: Búsqueda semántica
