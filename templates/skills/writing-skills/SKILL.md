---
name: writing-skills
description: Meta-skill — úsala para crear o editar skills procedimentales en formato Morphix (frontmatter mínimo + cuerpo markdown).
---
# Writing Skills

## Formato Morphix
```
templates/skills/<nombre>/SKILL.md
---
name: <nombre-kebab>
description: <criterio de disparo — cuándo usarla>
---
<cuerpo markdown libre>
```

## Reglas
- description = disparador: describe la SITUACIÓN que activa la skill,
  no lo que hace.
- Cuerpo accionable: pasos numerados, reglas explícitas, comandos reales
  del repo (`poetry run ...`).
- Adaptaciones locales: preguntas → ask_clarification; planes → dev/planes/;
  subtareas externas → flujo de subtareas del workflow activo.
- Prueba la skill: descubre con discover_skills y carga con load_skill
  antes de considerarla lista.
