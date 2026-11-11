# core/bots_protocol.py — sección "Messaging other agents"
"""Texto de protocolo para el chat canónico del bot.

Reglas:
- Byte-estable por turno: se construye una vez con datos congelados del turno.
- SOLO chats canónicos (gate por título/índice hecho por llamadores) y solo
  cuando hay ≥2 bots gestionados.
- Jamás se appendea al SOUL.md del usuario (el core lo inyecta en prompt-build).

Sentido anti-ping-pong incluido (guía prompt-level); el freno duro es el TTL
de hops en pending_turns.
"""

PROTOCOL_HEADING = "## Messaging other agents"
DM_TOOL_NAME = "send_to_bot"


def section_text(
    display_name: str,
    roster: list[dict],
    *,
    self_slug: str,
    max_chars: int = 16000,
) -> str:
    """Sección completa para el system prompt del bot (con heading).

    Determinista: el roster se ordena por slug — dos llamadas con los mismos
    bots en distinto orden producen EXACTAMENTE los mismos bytes.
    """
    others = [
        f"- @{b['slug']} ({b.get('display_name') or b['slug']})"
        for b in sorted((r for r in roster if r.get("slug")), key=lambda r: r["slug"])
        if b["slug"] != self_slug
    ]
    lines: list[str] = []
    lines.append(PROTOCOL_HEADING)
    lines.append("")
    lines.append(
        f"Eres @{self_slug} ({display_name}). Otros agentes de este workspace son "
        "companeros con sus propios chats eternos:"
    )
    lines.extend(others or ["- (aún no hay otros bots: comportate como asistente único)"])
    lines.append("")
    lines.append("Mensajería entre agentes:")
    lines.append(
        f"- Para hablar con un compañero usa la herramienta `{DM_TOOL_NAME}` con "
        f"`target=<slug>` y `message=<texto>` (tope {max_chars} caracteres)."
    )
    lines.append(
        "- La entrega es fire-and-forget: recibirás acuse inmediato; su "
        "respuesta llegará después como un mensaje entrante en tu chat."
    )
    lines.append(
        "- Si un turno te llega comenzando con 'Message from 🤖 X (@y):' es un "
        f"DM entrante: responde con `{DM_TOOL_NAME}` target=y. Tu texto final "
        "NO le llega solo: SOLO viaja si lo envías con la herramienta."
    )
    lines.append(
        "- Escribe SIEMPRE con atribución clara si respondes sobre algo que "
        "te pasó un compañero ('@x me comentó...')."
    )
    lines.append("")
    lines.append("Reglas de cortesía (anti ping-pong):")
    lines.append("- NO repitas preguntas ya hechas; confirma y concluye.")
    lines.append("- Un DM exige respuesta UNA vez; sin re-agradecer ni eco.")
    lines.append("- Si no necesitas respuesta, di '(pass)' o 'sin respuesta necesaria'.")
    lines.append(
        "- Las menciones @nombre del usuario son IDENTIFICATIVAS: sirven "
        "para saber de quién hablas; NUNCA reenvíes texto verbatim de otros."
    )
    return "\n".join(lines)


_MENTION_RE = None  # compilado bajo demanda en annotate_user_mentions


def annotate_user_mentions(text: str, roster: list[dict]) -> str:
    """Identification-only: las @menciones del usuario solo IDENTIFICAN.

    Anexa una nota explícita por mención existente en el roster; jamás
    auto-envía ni reenvía texto verbatim a otro bot.
    """
    global _MENTION_RE
    if _MENTION_RE is None:
        import re

        _MENTION_RE = re.compile(r"@([a-z0-9][a-z0-9_-]{0,63})", re.IGNORECASE)
    slugs = {b["slug"].lower() for b in roster if b.get("slug")}
    if not slugs:
        return text
    found: list[str] = []
    for m in _MENTION_RE.finditer(text or ""):
        slug_l = m.group(1).lower()
        if slug_l in slugs and slug_l not in [f.lower() for f in found]:
            found.append(m.group(1))
    if not found:
        return text
    notes = " ".join(
        f"[mención @{f}: identificación de contexto — NO reenviar verbatim]" for f in found
    )
    base = (text or "").rstrip()
    return f"{base}\n\n{notes}"
