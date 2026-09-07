---
name: finishing-a-development-branch
description: Úsala al completar implementación con tests verdes, para decidir integración del trabajo (merge, PR, cleanup).
---
# Finishing a Development Branch

## Reglas de oro (dev/docinterno/flujo_git.md)
- `main` es privada: JAMÁS se pushea. El trabajo nace en ramas desde `main`
  y se mergea con --no-ff.
- `public` es la única rama que se pushea (→ origin/main), con commits
  agrupados/saneados.
- TODO push/fetch autenticado/gh auth lo EJECUTA EL USUARIO: el agente
  prepara los comandos exactos y espera.

## Flujo
1. Verificación completa (verification-before-completion).
2. Presenta opciones: merge local --no-ff / dejar la rama / continuar después.
3. Merge solo con consentimiento explícito del usuario. Nunca decidas tú.
