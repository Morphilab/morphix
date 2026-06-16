"""
LLM Response Parser — Robust JSON extraction from LLM responses.
Unifies 5 duplicate implementations of the same pattern in one place.
"""

import ast
import json
import logging
import re

logger = logging.getLogger(__name__)


from typing import Any


def parse_json_from_llm(text: str, default: Any = None) -> dict:
    """Intenta parsear JSON de una respuesta LLM con múltiples estrategias.

    Estrategias (en orden):
    1. json.loads() sobre el texto completo
    2. Extraer bloque ```json ... ``` o ``` ... ```
    3. Extraer primer { ... } balanceado con raw_decode
    4. ast.literal_eval
    5. Retornar default

    Args:
        text: Texto crudo de la respuesta del LLM.
        default: Valor a retornar si todo falla.

    Returns:
        dict parseado o default.
    """
    if not text or not isinstance(text, str):
        return default if default is not None else {}

    text = text.strip()

    # 1. json.loads directo
    result, _ = try_parse_json(text)
    if result is not None:
        return result

    # 2. Extraer de bloque markdown ```json ... ```
    block = extract_json_block(text)
    if block:
        result, _ = try_parse_json(block)
        if result is not None:
            return result

    # 3. Extract first balanced JSON object with json.JSONDecoder
    try:
        decoder = json.JSONDecoder()
        result, _ = decoder.raw_decode(text)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # 4. ast.literal_eval
    try:
        result = ast.literal_eval(text)
        if isinstance(result, dict):
            return result
    except (ValueError, SyntaxError):
        pass

    # 5. Fallback: find any { ... } with lazy regex
    try:
        match = re.search(r"\{[^{}]*\}", text)
        if match:
            result, _ = try_parse_json(match.group())
            if result is not None:
                return result
    except re.error:
        pass

    return default if default is not None else {}


def extract_json_block(text: str) -> str | None:
    """Extrae contenido entre ```json y ``` o entre ``` y ```."""
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def try_parse_json(text: str) -> tuple[dict | None, str | None]:
    """Intenta json.loads puro. Retorna (parsed_dict | None, error_message | None)."""
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        return None, str(e)


def parse_plan_json(text: str) -> dict | None:
    """Parseo específico para planes JSON del LLM.
    Wrapper de parse_json_from_llm con type check estricto.
    Retorna dict o None si no es un dict válido.
    """
    data = parse_json_from_llm(text)
    return data if isinstance(data, dict) else None


def tool_calls_from_response(response) -> list[dict] | None:
    """Extrae tool_calls de una respuesta LLM normalizada (OpenAI u Ollama)."""
    try:
        choice = response.choices[0]
    except (AttributeError, IndexError, TypeError):
        return None
    msg = choice.message
    if hasattr(msg, "tool_calls") and msg.tool_calls:
        return msg.tool_calls
    return None
