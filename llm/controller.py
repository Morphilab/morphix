"""
ModelsController - Sistema centralizado de LLM (versión modular con LLMProvider)
- Retries inteligentes + backoff
- Timeouts configurables
- Fallback automático a Ollama vía LLMProvider
- Logging limpio y manejo de errores más específico
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

import httpx
from openai import APIError, AsyncOpenAI, OpenAI
from openai import APITimeoutError as OpenAITimeoutError

from llm.provider import LLMProvider


@dataclass
class _Message:
    content: str | None = None
    tool_calls: list[dict] | None = None


@dataclass
class _Choice:
    message: _Message
    finish_reason: str = "stop"


@dataclass
class _NormalizedResponse:
    choices: list[_Choice]


@dataclass
class StreamChunk:
    """Chunk unificado de streaming, independiente del proveedor."""

    text: str | None = None
    tool_name: str | None = None
    tool_arguments: str | None = None
    tool_call_id: str | None = None
    finish_reason: str | None = None
    reasoning_content: str | None = None
    usage: dict[str, int] | None = None
    is_done: bool = False


logger = logging.getLogger(__name__)


class ModelsController:
    """Controlador centralizado de llamadas LLM con retries y fallback.

    Acepta configuración por constructor (inyectable en tests/CLI).
    La instancia global 'models' usa defaults razonables con carga lazy desde Kairos.
    """

    def __init__(
        self,
        max_retries: int | None = None,
        timeout: int | None = None,
        backoff_factor: float | None = None,
    ):
        self._max_retries = max_retries
        self._timeout = timeout
        self._backoff_factor = backoff_factor
        self._config_loaded = max_retries is not None
        self._config_lock = threading.Lock()

    def _load_kairos_config(self):
        if not self._config_loaded:
            with self._config_lock:
                if not self._config_loaded:
                    from core.config import settings as app_settings

                    self._max_retries = (
                        app_settings.llm_max_retries
                        if self._max_retries is None
                        else self._max_retries
                    )
                    self._timeout = (
                        app_settings.llm_timeout_seconds if self._timeout is None else self._timeout
                    )
                    self._backoff_factor = (
                        app_settings.llm_backoff_factor
                        if self._backoff_factor is None
                        else self._backoff_factor
                    )
                    self._config_loaded = True

    @property
    def max_retries(self) -> int:
        self._load_kairos_config()
        return self._max_retries  # type: ignore[return-value]

    @property
    def timeout(self) -> int:
        self._load_kairos_config()
        return self._timeout  # type: ignore[return-value]

    @property
    def backoff_factor(self) -> float:
        self._load_kairos_config()
        return self._backoff_factor  # type: ignore[return-value]

    async def call(
        self,
        messages: list,
        role: str = "default",
        temperature: float | None = None,
        stream: bool = False,
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        **kwargs: Any,
    ) -> Any:
        """ÚNICO punto de entrada para todas las llamadas LLM.
        Soporta function-calling nativo vía tools= (OpenAI, DeepSeek, Grok).
        Ollama recibe tools como instrucciones textuales."""
        self._load_kairos_config()

        from core.rate_limiter import get_rate_limiter

        limiter = get_rate_limiter()
        if not await limiter.acquire():
            logger.warning("Rate limit alcanzado, esperando slot...")
            from core.metrics import metrics as m

            m.record_rate_limited()
            acquired = await limiter.wait_and_acquire(timeout=30)
            if not acquired:
                return self._create_error_response(
                    "Rate limit excedido. Intenta de nuevo en unos segundos."
                )

        from core.metrics import metrics as m

        m.record_llm_call()

        # ── Circuit breaker: get provider name for tracking ──
        from core.circuit_breaker import CircuitBreakerRegistry

        provider_name = LLMProvider.get_provider_name(role)
        cb = CircuitBreakerRegistry.get(provider_name)

        # ── Token budget check: compress if history exceeds 90% of max context ──
        from core.config import settings as app_settings
        from core.context_manager import ContextManager

        est = ContextManager.estimate_tokens(messages)
        budget = app_settings.max_context_tokens
        if est > budget * 0.9:
            logger.warning(
                "Context near limit (%d/%d tokens), compressing before LLM call",
                est,
                budget,
            )
            messages = ContextManager.compress_history(messages, max_tokens=int(budget * 0.7))

        client, model, temp = LLMProvider.get_client(role, temperature)

        for attempt in range(1, self.max_retries + 1):
            try:
                if isinstance(client, OpenAI):
                    call_kwargs: dict = {
                        "model": model,
                        "messages": messages,
                        "temperature": temp,
                        "stream": stream,
                    }
                    if tools:
                        call_kwargs["tools"] = tools
                        call_kwargs["tool_choice"] = tool_choice
                    call_kwargs.update(kwargs)
                    response = client.chat.completions.create(**call_kwargs)
                else:
                    response = client.chat(
                        model=model,
                        messages=messages,
                        stream=stream,
                        options={"temperature": temp},
                        **kwargs,
                    )

                # Track usage + cache metrics
                self._track_usage(response)

                cb.record_success()
                return self._normalize_response(response)

            except (OpenAITimeoutError, httpx.TimeoutException):
                logger.warning(
                    "⏳ Timeout en intento %d/%d (rol: %s)", attempt, self.max_retries, role
                )
            except APIError as e:
                logger.warning(f"⚠️ APIError en intento {attempt}/{self.max_retries}: {e}")
            except Exception as e:
                logger.error(f"Error inesperado en llamada LLM (rol: {role})", exc_info=True)

            if attempt < self.max_retries:
                delay = self.backoff_factor**attempt + 0.5
                await asyncio.sleep(delay)

        # Fallback final: forzar Ollama
        logger.info("🔄 Todos los intentos fallaron → fallback forzado a Ollama")
        cb.record_failure()
        ollama_cb = CircuitBreakerRegistry.get("ollama")
        client, model, temp = LLMProvider.get_client(role, temperature, force_ollama=True)
        try:
            # NB3 — Convert tool_calls arguments from string to dict for Ollama
            ollama_messages = []
            for msg in messages:
                msg_copy = dict(msg)
                if msg_copy.get("tool_calls"):
                    for tc in msg_copy["tool_calls"]:
                        if isinstance(tc.get("function", {}).get("arguments"), str):
                            try:
                                tc["function"]["arguments"] = json.loads(
                                    tc["function"]["arguments"]
                                )
                            except (json.JSONDecodeError, TypeError):
                                tc["function"]["arguments"] = {}
                ollama_messages.append(msg_copy)

            response = client.chat(
                model=model,
                messages=ollama_messages,
                options={"temperature": temp},
                **kwargs,
            )
            ollama_cb.record_success()
            return self._normalize_response(response)
        except Exception as e:
            logger.error(f"Error crítico en fallback Ollama: {e}", exc_info=True)
            ollama_cb.record_failure()
            return self._create_error_response("Ollama también falló. Verifica que esté corriendo.")

    async def call_stream(
        self,
        messages: list,
        role: str = "default",
        temperature: float | None = None,
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        **kwargs: Any,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Stream unified chunks from the LLM with retry support.

        Yields StreamChunk with text, tool calls, and completion signal.
        Supports OpenAI, DeepSeek, Grok (via SDK) and Ollama (via API).
        On streaming failure, retries up to llm_max_retries times.
        """
        self._load_kairos_config()
        max_retries = self._max_retries or 1
        last_error: Exception | None = None

        # ── Circuit breaker check ──
        from core.circuit_breaker import CircuitBreakerRegistry

        provider_name = LLMProvider.get_provider_name(role)
        cb = CircuitBreakerRegistry.get(provider_name)
        if not cb.allow_request():
            logger.warning("Circuit breaker OPEN for %s, blocking stream", provider_name)
            yield StreamChunk(
                text=f"\n⚠️ Provider {provider_name} unavailable (circuit breaker open)",
                is_done=True,
            )
            return

        # ── Token budget check before streaming ──
        from core.config import settings as _app_settings
        from core.context_manager import ContextManager as _CM

        _est = _CM.estimate_tokens(messages)
        _budget = _app_settings.max_context_tokens
        if _est > _budget * 0.9:
            logger.warning(
                "Stream context near limit (%d/%d tokens), compressing",
                _est,
                _budget,
            )
            messages = _CM.compress_history(messages, max_tokens=int(_budget * 0.7))

        for attempt in range(max_retries + 1):
            try:
                client, model, temp = LLMProvider.get_async_client(role, temperature)

                if isinstance(client, AsyncOpenAI):
                    async for chunk in self._stream_openai_async(
                        client, model, messages, temp, tools, tool_choice, **kwargs
                    ):
                        yield chunk
                else:
                    async for chunk in self._stream_ollama(client, model, messages, temp, **kwargs):
                        yield chunk
                cb.record_success()
                return  # Success — exit retry loop
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Streaming error (attempt {attempt + 1}/{max_retries + 1}, "
                    f"role: {role}): {e}"
                )
                if attempt < max_retries:
                    delay = 1.5**attempt + 0.5
                    await asyncio.sleep(delay)

        # All retries exhausted — fallback to non-streaming call
        cb.record_failure()
        logger.error(
            f"Streaming exhausted all retries (role: {role}), "
            f"falling back to non-streaming call. Error: {last_error}"
        )
        try:
            response = await self.call(
                messages=messages,
                role=role,
                temperature=temperature,
                tools=tools,
                tool_choice=tool_choice,
                **kwargs,
            )
            text = response.choices[0].message.content if response.choices else ""
            yield StreamChunk(text=text or "", is_done=True)
        except Exception as e2:
            logger.error(f"Non-streaming fallback also failed (role: {role}): {e2}")
            yield StreamChunk(text=f"\n❌ Streaming error: {e2}", is_done=True)

    async def _stream_openai_async(
        self, client, model, messages, temp, tools, tool_choice, **kwargs
    ) -> AsyncGenerator[StreamChunk, None]:
        """Streaming no bloqueante desde API OpenAI-compatible (AsyncOpenAI)."""
        call_kwargs: dict = {
            "model": model,
            "messages": messages,
            "temperature": temp,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            call_kwargs["tools"] = tools
            call_kwargs["tool_choice"] = tool_choice
        call_kwargs.update(kwargs)

        response = await client.chat.completions.create(**call_kwargs)
        # Accumulate streaming tool-call deltas keyed by their stable `index`.
        # Only the first delta of a tool call carries id+name; subsequent deltas
        # have id=None and only argument fragments — associate them by index, not
        # id, and re-emit every chunk with the call's real id so the downstream
        # accumulator concatenates the arguments under a single id.
        tool_acc: dict = {}

        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            finish = chunk.choices[0].finish_reason if chunk.choices else None

            if finish:
                usage_dict = None
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage_dict = {
                        "prompt_tokens": getattr(chunk_usage, "prompt_tokens", 0) or 0,
                        "completion_tokens": getattr(chunk_usage, "completion_tokens", 0) or 0,
                        "prompt_cache_hit_tokens": getattr(
                            chunk_usage, "prompt_cache_hit_tokens", 0
                        )
                        or 0,
                        "prompt_cache_miss_tokens": getattr(
                            chunk_usage, "prompt_cache_miss_tokens", 0
                        )
                        or 0,
                    }
                yield StreamChunk(finish_reason=finish, usage=usage_dict, is_done=True)
                break

            if delta is None:
                continue

            if delta.content:
                yield StreamChunk(text=delta.content)

            if getattr(delta, "reasoning_content", None):
                yield StreamChunk(reasoning_content=delta.reasoning_content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = getattr(tc, "index", None)
                    tc_id = getattr(tc, "id", None)
                    key = idx if idx is not None else tc_id
                    if key is None:
                        continue
                    if key not in tool_acc:
                        tool_acc[key] = {
                            "id": tc_id or f"call_{len(tool_acc)}",
                            "name": "",
                            "arguments": "",
                        }
                    entry = tool_acc[key]
                    if tc_id:
                        entry["id"] = tc_id
                    func = getattr(tc, "function", None)
                    if func:
                        if getattr(func, "name", None):
                            entry["name"] = func.name
                            yield StreamChunk(tool_name=func.name, tool_call_id=entry["id"])
                        if getattr(func, "arguments", None):
                            entry["arguments"] += func.arguments
                            yield StreamChunk(
                                tool_arguments=func.arguments, tool_call_id=entry["id"]
                            )

    async def _stream_ollama(
        self, client, model, messages, temp, **kwargs
    ) -> AsyncGenerator[StreamChunk, None]:
        """Streaming desde Ollama API con cola para streaming real.

        Usa una queue.Queue para comunicar el hilo bloqueante (Ollama sync)
        con el event loop asíncrono. Cada chunk se entrega tan pronto
        como llega, sin esperar a que terminen todos.
        """
        import asyncio as _asyncio
        import queue

        chunk_queue: queue.Queue = queue.Queue()

        def _produce_chunks():
            try:
                response = client.chat(
                    model=model,
                    messages=messages,
                    stream=True,
                    options={"temperature": temp},
                    **kwargs,
                )
                for chunk in response:
                    chunk_queue.put(chunk)
            except Exception:
                logger.exception("Error en streaming Ollama")
            finally:
                chunk_queue.put(None)  # Sentinel: fin del stream

        _asyncio.get_running_loop().run_in_executor(None, _produce_chunks)

        while True:
            chunk = await _asyncio.to_thread(chunk_queue.get)
            if chunk is None:
                break
            if isinstance(chunk, dict):
                if chunk.get("done"):
                    yield StreamChunk(
                        finish_reason=chunk.get("done_reason", "stop"),
                        is_done=True,
                    )
                    break
                msg = chunk.get("message", {})
                if msg.get("content"):
                    yield StreamChunk(text=msg["content"])
                if msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        func = tc.get("function", {})
                        yield StreamChunk(
                            tool_name=func.get("name"),
                            tool_arguments=(
                                json.dumps(func.get("arguments", {}))
                                if func.get("arguments")
                                else None
                            ),
                            tool_call_id=tc.get("id"),
                        )

    def _normalize_response(self, raw_response: Any) -> Any:
        if hasattr(raw_response, "choices"):
            return raw_response

        content = (
            raw_response.get("message", {}).get("content", "")
            if isinstance(raw_response, dict)
            else str(raw_response)
        )
        tool_calls = (
            raw_response.get("message", {}).get("tool_calls")
            if isinstance(raw_response, dict)
            else None
        )
        return _NormalizedResponse(
            choices=[
                _Choice(
                    message=_Message(content=content, tool_calls=tool_calls),
                    finish_reason=(
                        raw_response.get("done_reason", "stop")
                        if isinstance(raw_response, dict)
                        else "stop"
                    ),
                )
            ]
        )

    def _create_error_response(self, message: str):
        return _NormalizedResponse(choices=[_Choice(message=_Message(content=f"❌ {message}"))])

    @staticmethod
    def _track_usage(response: Any) -> None:
        """Extract token usage and cache metrics from LLM response."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return

        from core.cache_manager import cache_manager
        from core.metrics import metrics as m

        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        cache_hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
        cache_miss = getattr(usage, "prompt_cache_miss_tokens", 0) or 0

        m.record_llm_usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_hit_tokens=cache_hit,
            cache_miss_tokens=cache_miss,
        )

        cache_manager.track_usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_cache_hit_tokens=cache_hit,
            prompt_cache_miss_tokens=cache_miss,
        )


# Single global instance
models = ModelsController()
