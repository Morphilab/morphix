"""
Ejecuta cualquier agente especializado definido en los perfiles (globales o del workspace).
La configuración (prompt, longitud, temperatura, clave de memoria, etc.) se toma del perfil
registrado en AgentsRegistry.
"""

import logging

from core.memory.manager import memory as memory_manager
from core.security.frustration_detector import frustration_detector
from core.security.undercover_mode import undercover
from core.utils import clean_llm_response
from llm import models
from llm.prompts import get_prompt

logger = logging.getLogger(__name__)


async def _execute_specialized_agent(
    agent_type: str,
    task: str,
    history: list,
    pdf_text: str = "",
    tools_output: str = "",
    extra_tool_instructions: str = "",
    on_stream_chunk=None,
) -> str:
    """Ejecuta un agente especializado usando su perfil (global o de workspace)."""
    # Get the profile from the unified registry
    from agents.registry import agents_registry

    profile = agents_registry.get_profile(agent_type)
    if not profile:
        return f"❌ Agente '{agent_type}' no encontrado."

    try:
        # ── 1. User profile (with workspace-isolated memory) ──
        user_profile = memory_manager.get_user_profile()
        extra_context = ""
        if user_profile and any(user_profile.values()):
            summary = memory_manager.get_user_summary()
            if summary:
                extra_context += f"\n[PERFIL DEL USUARIO]:\n{summary}\n"

        # ── 2. Last result memory (defined in profile) ──
        last_key = profile.get("last_memory_key")
        if last_key:
            last = memory_manager.read(last_key)
            if last:
                extra_context += f"\n[Resultado previo]: {str(last)[:300]}..."

        # ── 3. Build system prompt from profile ──

        system_prompt = (
            f"{profile['system_prompt']}\n\n"
            f"{profile.get('length_guidance', 'Sé conciso.')}\n\n"
            f"{get_prompt('anti_frustration')}"
        )
        if extra_tool_instructions:
            system_prompt += f"\n\n{extra_tool_instructions}"

        # ── 4. Prepare messages with token-aware compression ──
        from core.config import settings as app_settings
        from core.context_manager import ContextManager

        budget = int(app_settings.max_context_tokens * 0.6)
        messages = ContextManager.compress_history(history, max_tokens=budget)
        if not messages:
            messages = history.copy()
        messages = undercover.inject_identity_prompt(messages)

        # Check for frustration patterns and inject calming prompt
        is_frustrated, reason = frustration_detector.check(task)
        if is_frustrated:
            calming = frustration_detector.get_calming_prompt()
            if calming:
                system_prompt += calming

        if not messages or messages[0].get("role") != "system":
            messages.insert(0, {"role": "system", "content": system_prompt})
        else:
            messages[0]["content"] = system_prompt + "\n\n" + messages[0]["content"]

        user_content = task
        if extra_context:
            user_content += f"\n\n{extra_context}"
        if pdf_text:
            user_content += f"\n\n[Contexto PDF]: {pdf_text[:800]}"
        if tools_output:
            user_content += f"\n\n[Salida de herramientas]: {tools_output[:500]}"

        messages.append({"role": "user", "content": user_content})

        # ── 5. Call the agent (using profile temperature) ──
        temp = profile.get("temperature", 0.4)
        overrides = profile.get("model_override", {}) or {}
        temp = overrides.get("temperature", temp)

        if on_stream_chunk:
            role = profile.get("model_role", "agent")
            stream = models.call_stream(messages=messages, role=role, temperature=temp)
            full_text = ""
            async for chunk in stream:
                if chunk.text:
                    full_text += chunk.text
                    try:
                        await on_stream_chunk(chunk.text)
                    except Exception:
                        pass
                if chunk.is_done and not full_text:
                    full_text = chunk.finish_reason or ""
            initial_result = full_text.strip()
        else:
            role = profile.get("model_role", "agent")
            response = await models.call(messages=messages, role=role, temperature=temp)
            initial_result = response.choices[0].message.content.strip()

        # ── 6. Self-reflection (controlled by feature flag) ──
        final_result = initial_result
        if app_settings.agent_self_reflection:
            try:
                critique = await models.call(
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                f"Revisa esta respuesta del agente {agent_type} y mejórala "
                                "manteniendo fidelidad total a los hechos y al perfil del usuario:\n"
                                f"Tarea: {task}\n"
                                f"Respuesta: {initial_result}\n"
                                "Devuelve solo la versión final mejorada."
                            ),
                        }
                    ],
                    role="critique",
                    temperature=0.3,
                )
                improved = clean_llm_response(critique)
                final_result = improved or critique.choices[0].message.content.strip()
            except Exception as e:
                logger.warning(
                    f"Self-reflection falló para el agente '{agent_type}': {e}. "
                    "Se usará la respuesta original."
                )

        # ── 7. Save to memory (workspace-isolated) ──
        if last_key:
            await memory_manager.write(
                last_key, final_result, validated=True, content_hint="analytical"
            )

        # Apply anti-distillation protection to agent output
        final_result = undercover.get_safe_response(final_result)

        return final_result

    except Exception as e:
        logger.error(f"Error en agente '{agent_type}': {e}", exc_info=True)
        return f"❌ Error en el agente {agent_type}: {str(e)[:200]}"


# ====================== AUTO REGISTRATION FROM PROFILES ======================
from agents.profiles import AGENT_PROFILES
from agents.registry import agents_registry

for _profile in AGENT_PROFILES:
    name: str = _profile["name"]  # type: ignore[assignment]

    def make_agent(agent_name: str):
        async def agent_func(
            task: str, history: list, pdf_text: str = "", tools_output: str = ""
        ) -> str:
            return await _execute_specialized_agent(
                agent_name, task, history, pdf_text, tools_output
            )

        return agent_func

    agents_registry.register_global(name, _profile)(make_agent(name))

logger.info(f"✅ {len(AGENT_PROFILES)} agentes registrados desde perfiles")
