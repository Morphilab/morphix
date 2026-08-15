#!/usr/bin/env python3
"""Smoke test: verifica el formato real de tool_calls del SDK de ollama 0.6.1.

Ejecuta chat real (no-streaming y streaming) contra un modelo local con tools.
Imprime el formato RAW de tool_calls para diagnóstico y validación pre-fix.

Uso:
    poetry run python scripts/smoke_ollama_tools.py [--model qwen2.5-coder:7b]
"""

import argparse
import json
import sys

import ollama

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "file_manager",
            "description": "Read/write/append/delete files in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["write", "read", "append", "delete"],
                        "description": "Operation to perform.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Relative file path.",
                    },
                    "content": {
                        "type": "string",
                        "description": "File content (for write/append).",
                    },
                },
                "required": ["action", "path"],
            },
        },
    }
]

PROMPT = [
    {
        "role": "system",
        "content": (
            "Eres un asistente de desarrollo. Debes usar la herramienta file_manager "
            'con action="write" y path="hola.py" para crear el archivo. '
            'El contenido debe ser: print("hello world"). '
            "DEBES llamar a file_manager. NO respondas con texto."
        ),
    },
    {"role": "user", "content": "Crea el archivo hola.py"},
]


def test_non_streaming(client: ollama.Client, model: str) -> bool:
    """Non-streaming chat with tools. Returns True if tool_calls detected."""
    print("=== NON-STREAMING ===")

    try:
        response = client.chat(
            model=model,
            messages=list(PROMPT),
            tools=TOOL_DEFS,
            stream=False,
        )
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
        return False

    has_get = hasattr(response, "get")
    msg = response.get("message", {}) if has_get else {"content": ""}
    content = msg.get("content", "") if has_get else str(response)
    tool_calls = msg.get("tool_calls") if has_get else None

    print(f"  response type: {type(response).__name__}")
    print(f"  has .get(): {has_get}")
    print(f"  message content: {repr(content)[:100]}")
    print(f"  tool_calls present: {tool_calls is not None and len(tool_calls or []) > 0}")

    if tool_calls:
        for i, tc in enumerate(tool_calls):
            tc_type = type(tc).__name__
            func = tc.function if hasattr(tc, "function") else tc.get("function", {})
            name = func.name if hasattr(func, "name") else func.get("name", "?")
            args = func.arguments if hasattr(func, "arguments") else func.get("arguments", None)
            has_id = bool(
                getattr(tc, "id", None) or tc.get("id", None) if hasattr(tc, "get") else False
            )

            print(f"  tc[{i}]:")
            print(f"    type: {tc_type}")
            print(f"    has id: {has_id}")
            print(f"    function.name: {name}")
            print(f"    function.arguments type: {type(args).__name__}")
            args_preview = repr(args)
            if len(str(args_preview)) > 300:
                args_preview = str(args_preview)[:300] + "..."
            print(f"    function.arguments value: {args_preview}")

            # THE BUG TEST — simulate what loop.py:798-806 does
            print("    json.loads(args) → ", end="")
            try:
                result = json.loads(args)
                print(f"SUCCESS: {result}")
            except TypeError as e:
                print(f"TypeError: {e}")
                print("    ⚠️ THIS IS THE BUG: args is dict, json.loads() destroys it")

        return True

    print("  ⚠️ No tool_calls in response — model responded in text")
    print(f"  Full message content: {repr(content)[:200]}")
    return False


def test_streaming(client: ollama.Client, model: str) -> bool:
    """Streaming chat with tools. Returns True if tool_calls detected."""
    print("\n=== STREAMING ===")

    tool_call_seen = False
    try:
        stream = client.chat(
            model=model,
            messages=list(PROMPT),
            tools=TOOL_DEFS,
            stream=True,
        )
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
        return False

    chunk_count = 0
    for chunk in stream:
        chunk_count += 1
        msg = chunk.get("message", {}) if hasattr(chunk, "get") else {}
        tool_calls = msg.get("tool_calls")

        if chunk_count <= 2 and tool_calls:
            for i, tc in enumerate(tool_calls):
                func = (
                    tc.get("function", {})
                    if isinstance(tc, dict)
                    else getattr(tc, "function", None)
                )
                if func:
                    name = (
                        func.get("name") if isinstance(func, dict) else getattr(func, "name", "?")
                    )
                    args = (
                        func.get("arguments")
                        if isinstance(func, dict)
                        else getattr(func, "arguments", None)
                    )
                    has_id = bool(tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None))

                    print(f"  tc[{i}]:")
                    print(f"    type: {type(tc).__name__}")
                    print(f"    has id: {has_id}")
                    print(f"    function.name: {name}")
                    print(f"    function.arguments type: {type(args).__name__}")
                    print(f"    function.arguments value: {repr(args)[:200]}")
                    tool_call_seen = True

    print(f"  chunks received: {chunk_count}")
    print(f"  tool_calls seen: {tool_call_seen}")

    if not tool_call_seen:
        print(
            "  ⚠️ No tool_calls in streaming chunks — either model didn't emit, or chunk format differs"
        )
        print(f"  Total chunks: {chunk_count}")
    return tool_call_seen


def main():
    parser = argparse.ArgumentParser(description="Smoke test Ollama tool calling format")
    parser.add_argument(
        "--model",
        default="qwen2.5-coder:7b",
        help="Ollama model to test (default: qwen2.5-coder:7b)",
    )
    parser.add_argument(
        "--host",
        default="http://localhost:11434",
        help="Ollama host (default: http://localhost:11434)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=90,
        help="Request timeout in seconds (default: 90)",
    )
    args = parser.parse_args()

    print(f"Ollama host: {args.host}")
    print(f"Model: {args.model}")
    print(f"Tool: {TOOL_DEFS[0]['function']['name']}")
    print()

    client = ollama.Client(host=args.host, timeout=args.timeout)

    # Verify model exists
    try:
        models = client.list()
        model_names = [m.model for m in models.models]
        if args.model not in model_names:
            print(f"⚠️  Model '{args.model}' not found in local models: {model_names}")
            print("   Attempting to use anyway...")
    except Exception as e:
        print(f"⚠️  Could not list models: {e}")

    print()
    ns_ok = test_non_streaming(client, args.model)
    st_ok = test_streaming(client, args.model)

    print()
    print("=" * 60)
    print(
        f"RESULT: non-streaming={'PASS' if ns_ok else 'FAIL (no tool_calls)'}, "
        f"streaming={'PASS' if st_ok else 'FAIL (no tool_calls)'}"
    )

    if not ns_ok or not st_ok:
        print()
        print("⚠️  Tool calls not detected. Possible causes:")
        print("   1. Model doesn't support native tool calling")
        print("   2. Model wasn't instructed well enough in the prompt")
        print("   3. Ollama server version incompatible with SDK")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
