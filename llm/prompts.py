"""
Prompt System Centralizado - Claude Code Style (Abril 2026)
"""

DECOMPOSE_TASK_PROMPT = """
Descompón la consulta en 3-5 subtareas concretas y accionables.
Cada subtarea debe ser pequeña: un archivo o una responsabilidad clara.

{project_context}

Reglas:
- Si el proyecto YA tiene archivos, la primera subtarea debe LEERLOS y ANALIZARLOS
  antes de crear o modificar nada. Usa file_manager(read) para entender qué existe.
- Si el proyecto está VACÍO y la tarea pide crear, genera subtareas de creación
  con nombres de archivo específicos.
- Si la tarea mezcla análisis con creación/modificación, balancea: primero leer
  los archivos relevantes, luego crear o modificar basándote en lo leído.
- NUNCA asumas el contenido de un proyecto existente sin leer sus archivos.
- NUNCA uses subtareas genéricas como "analizar el proyecto" o "crear el código".
  Sé específico: nombres de archivo reales, acciones concretas.
- Si una subtarea crea un archivo (ej: calculadora.py), las subtareas posteriores
  deben MODIFICAR ese mismo archivo. NUNCA crees un archivo NUEVO con nombre
  alternativo para la misma funcionalidad (ej: NO calculadora_v2.py, NO
  calculadora_final.py, NO calculadora_nuevo.py — MODIFICA calculadora.py).

Formato de respuesta: SOLO el siguiente JSON, sin texto extra:
{{"subtasks": ["subtarea 1", "subtarea 2", ...]}}

Consulta: {query}
"""

DECOMPOSE_TASK_WITH_PHASES_PROMPT = """
Descompón la consulta en subtareas organizadas en fases lógicas (1-4 fases).
Cada fase tiene un propósito distinto y produce artefactos que consume la siguiente.

{project_context}

Tipos de fases:
- "analyze": Leer y analizar código/documentación existente
- "design": Diseño de arquitectura, esquemas, modelos
- "implement": Creación o modificación de archivos de código fuente
- "test": Escritura/ejecución de tests
- "verify": Verificación, linting, validación
- "docs": Documentación, README, reportes

Reglas:
- Si el proyecto YA tiene archivos, la primera fase debe ser "analyze" para
  leer y entender el código existente antes de crear o modificar.
- Si el proyecto está VACÍO, la primera fase puede ser "implement" o "design".
- Cada subtarea debe ser concreta y mencionar archivos específicos.
- Para tareas simples (≤2 archivos), usa 1 fase. Medianas: 2-3. Complejas: 3-4.
- Los nombres de archivo deben ser CONSISTENTES entre fases. Si la fase "design"
  crea main.py, la fase "implement" debe MODIFICAR main.py (no crear main_v2.py).

Formato: SOLO el siguiente JSON, sin texto extra:
{{
  "phases": [
    {{
      "phase": "analyze",
      "order": 1,
      "description": "Leer y entender el código existente",
      "subtasks": ["Leer README.md para entender el proyecto", "Leer sendorbit.sh para ver la estructura"]
    }}
  ],
  "strategy": "sequential"
}}

Consulta: {query}
"""

ANTI_FRUSTRATION_PROMPT = """
Eres Morphix, un asistente experto, útil y agradable.

Reglas anti-frustración (siempre aplicar):
- Sé directo, claro y conversacional.
- Ve al grano desde la primera frase.
- No uses listas largas ni formalismos innecesarios.
- Si la pregunta es simple, responde de forma natural como un amigo inteligente.
- Nunca repitas información que ya diste.
- Sé empático si el usuario parece impaciente.
- Siempre ofrece el máximo valor posible con el mínimo texto.
"""

PLAN_VERIFY_PROMPT = """
Revisa la tarea original y los archivos generados (se muestra su contenido).
Determina si la implementación cumple completamente con los requisitos.
Si falta algo, genera un plan JSON con las acciones necesarias para solucionarlo.

⚠️ SOLO puedes usar las herramientas 'file_manager' (acción 'write') y 'git_manager' (acciones 'init', 'add', 'commit').
NUNCA uses 'code_exec' ni ninguna otra herramienta.

Tarea: {task}

Archivos generados:
{files_content}

Formato de respuesta (solo el JSON, sin texto extra):
{{
  "is_correct": true,
  "explanation": "razonamiento breve"
}}

Si la implementación no es correcta, responde OBLIGATORIAMENTE con:
{{
  "is_correct": false,
  "explanation": "razonamiento breve",
  "fix_plan": {{
    "actions": [
      {{"tool": "file_manager", "action": "write", "params": {{"path": "...", "content": "..."}}}},
      ...
    ]
  }}
}}

⚠️ El array "actions" debe contener únicamente objetos con las claves "tool", "action" y "params".
⚠️ No incluyas texto adicional fuera del JSON.
"""
VERIFY_GLOBAL_PROMPT = """
Revisa la tarea original completa y TODOS los archivos generados en el proyecto.
También se proporciona un informe del LSP con problemas detectados
(archivos vacíos, errores de sintaxis, etc.).

Tarea original:
{task}

Archivos del proyecto (ruta y contenido):
{files_text}

Informe del LSP:
{lsp_report}

Si el proyecto cumple con todos los requisitos y el LSP no muestra problemas graves,
responde ÚNICAMENTE:
{{
  "is_correct": true,
  "explanation": "Explicación breve"
}}

Si falta algo o hay errores (especialmente archivos vacíos que deberían tener contenido,
o estructura incorrecta), responde OBLIGATORIAMENTE con un plan de corrección
SOLO para esos archivos específicos. NO reescribas archivos que ya son correctos.
Formato exacto:
{{
  "is_correct": false,
  "explanation": "Razonamiento de lo que falta o está mal",
  "fix_plan": {{
    "actions": [
      {{"tool": "file_manager", "action": "write", "params": {{"path": "ruta/archivo.py", "content": "contenido completo y correcto"}}}},
      ...
    ]
  }}
}}

⚠️ SOLO puedes usar 'file_manager' (action 'write') y 'git_manager' (init, add, commit). NUNCA uses 'code_exec'.
⚠️ Si un archivo está vacío y debería tener contenido, escribe su contenido completo.
⚠️ Si un archivo ya es correcto, NO lo incluyas en las correcciones.
⚠️ El array "actions" debe contener objetos con las claves "tool", "action" y "params".
⚠️ No incluyas texto adicional fuera del JSON.
"""


from typing import Any


def get_prompt(name: str, **kwargs: Any) -> str:
    """Función helper para formatear prompts de forma segura."""
    prompts = {
        "decompose": DECOMPOSE_TASK_PROMPT,
        "anti_frustration": ANTI_FRUSTRATION_PROMPT,
    }

    prompt = prompts.get(name)
    if prompt is None:
        raise KeyError(f"Prompt '{name}' no encontrado")

    return prompt.format(**kwargs) if kwargs else prompt
