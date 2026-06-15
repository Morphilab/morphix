# core/mcp/protocol.py
"""JSON-RPC 2.0 framing over asyncio streams.

MCP uses JSON-RPC 2.0 with newline-delimited JSON over stdio.
Messages are one JSON object per line (no pretty-print, no embedded newlines).
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2024-11-05"


@dataclass
class JSONRPCRequest:
    jsonrpc: str = "2.0"
    id: int | str = 0
    method: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class JSONRPCNotification:
    jsonrpc: str = "2.0"
    method: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class JSONRPCResponse:
    jsonrpc: str = "2.0"
    id: int | str = 0
    result: Any = None


@dataclass
class JSONRPCError:
    jsonrpc: str = "2.0"
    id: int | str | None = None
    error: dict[str, Any] = field(default_factory=dict)


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
