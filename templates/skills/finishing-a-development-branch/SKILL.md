---
name: finishing-a-development-branch
description: Úsala al completar implementación con tests verdes, para decidir integración del trabajo (merge, PR, cleanup).
---
# Finishing a Development Branch

## Reglas de oro
- No hagas push sin autorización explícita del usuario.
- Mantén los commits atómicos y con mensajes claros.
- TODO push/fetch autenticado/gh auth lo EJECUTA EL USUARIO: el agente
  prepara los comandos exactos y espera.

## Flujo
1. Verificación completa (verification-before-completion).
2. Presenta opciones: merge local --no-ff / dejar la rama / continuar después.
3. Merge solo con consentimiento explícito del usuario. Nunca decidas tú.
