# core/mcp/protocol.py
"""JSON-RPC 2.0 framing over asyncio streams.

MCP uses JSON-RPC 2.0 with newline-delimited JSON over stdio.
Messages are one JSON object per line (no pretty-print, no embedded newlines).
"""

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2024-11-05"


async def read_message(stream: asyncio.StreamReader) -> dict[str, Any]:
    """Read one newline-delimited JSON message from a stream."""
    line = await stream.readline()
    if not line:
        raise ConnectionError("MCP stream closed by remote")
    try:
        return json.loads(line.decode("utf-8"))
    except json.JSONDecodeError as e:
        logger.warning(f"MCP JSON parse error: {e} | raw: {line[:200]!r}")
        raise


async def write_message(stream: asyncio.StreamWriter, data: dict[str, Any]) -> None:
    """Write one JSON message as a single line to a stream."""
    encoded = json.dumps(data, ensure_ascii=False).encode("utf-8") + b"\n"
    stream.write(encoded)
    await stream.drain()


def build_request(msg_id: int | str, method: str, params: dict | None = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": method,
        "params": params or {},
    }


def build_notification(method: str, params: dict | None = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
    }


def build_response(msg_id: int | str, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def build_error(msg_id: int | str | None, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }


def is_notification(msg: dict) -> bool:
    return "id" not in msg


def is_response(msg: dict) -> bool:
    return "result" in msg or "error" in msg


def get_id(msg: dict) -> int | str | None:
    return msg.get("id")


def validate_request_id(msg: dict) -> dict | None:
    """Error -32600 si el request trae id ausente/null (JSON-RPC lo prohíbe).

    Retorna None para requests válidos. Evita responder con id 0 fabricado.
    """
    if msg.get("id") is None:
        return build_error(None, -32600, "Invalid Request: missing id")
    return None
