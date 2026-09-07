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
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from functools import partial
from typing import Any

import httpx
from openai import APIError, AsyncOpenAI, OpenAI
from openai import APITimeoutError as OpenAITimeoutError

from core.constants import TOOL_CALL_TIMEOUT_SECONDS
from llm.provider import LLMProvider
from llm.tool_calls import log_raw_tool_args, normalize_arguments, sanitize_messages_for_ollama


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
    reset: bool = False  # reiniciar acumulador (retry tras fallo parcial)


logger = logging.getLogger(__name__)

# Deadline per non-streaming LLM call (seconds). Reasoning models can
# generate for minutes when their chain of thought grows; without a
# deadline a single call can stall a whole workflow silently.
_LLM_CALL_DEADLINE = float(TOOL_CALL_TIMEOUT_SECONDS)

# OpenAI-style kwargs the Ollama SDK does not accept (client.chat would
# raise TypeError). They are translated/ignored in the Ollama path.
_OLLAMA_UNSUPPORTED_KWARGS = {
    "max_tokens",
    "max_completion_tokens",
    "tool_choice",
    "response_format",
    "reasoning_effort",
    "stream_options",
}


def _response_tokens(response: Any) -> int | None:
    """Best-effort token usage extraction from an LLM response."""
    try:
        usage = getattr(response, "usage", None)
        if usage is not None and getattr(usage, "total_tokens", None) is not None:
            return int(usage.total_tokens)
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        pe = response.get("prompt_eval_count")
        ec = response.get("eval_count")
        if pe is not None or ec is not None:
            return int(pe or 0) + int(ec or 0)
    except (AttributeError, TypeError, ValueError):
        pass
    return None


def _cache_hit_tokens(usage: Any) -> int:
    """Tokens de prompt servidos desde caché del proveedor (0 si no reporta).

    DeepSeek: ``prompt_cache_hit_tokens``; OpenAI-compat:
    ``prompt_tokens_details.cached_tokens``. El presupuesto del workflow
    los trata como coste cero: un prefijo cacheado no debe quemarlo.
    """
    if usage is None:
        return 0

    def _get(key: str) -> int:
        try:
            v = usage.get(key) if hasattr(usage, "get") else getattr(usage, key, None)
        except (AttributeError, TypeError):
            v = None
        return int(v) if v else 0

    hit = _get("prompt_cache_hit_tokens")
    if hit == 0:
        details = (
            usage.get("prompt_tokens_details")
            if hasattr(usage, "get")
            else getattr(usage, "prompt_tokens_details", None)
        )
        if details is not None:
            hit = int(getattr(details, "cached_tokens", 0) or 0)
    return hit


def _disjoint_usage(usage: Any) -> dict[str, int]:
    """Translate wire token usage into disjoint buckets (token-meter rule).

    DeepSeek reports ``prompt_tokens`` INCLUDING cache hits
    (``prompt_tokens = prompt_cache_hit_tokens + prompt_cache_miss_tokens``).
    This maps the wire shape (attr- or dict-based, OpenAI-compatible or Ollama)
    into disjoint buckets:

    - ``input_tokens``       — uncached input only (``prompt_tokens − hits``)
    - ``output_tokens``      — completion tokens
    - ``cache_read_tokens``  — cache hits (what the provider charged at hit rate)
    - ``cache_write_tokens`` — cache writes (misses that populated the cache)

    Billed input = input + cache_read + cache_write. Counts are clamped to >= 0.
    """
    if usage is None:
        return {}

    def _get(key: str) -> int:
        try:
            v = usage.get(key) if hasattr(usage, "get") else getattr(usage, key, None)
        except (AttributeError, TypeError):
            v = None
        return int(v) if v else 0

    hit = _get("prompt_cache_hit_tokens")
    miss = _get("prompt_cache_miss_tokens")
    if hit == 0 and miss == 0:
        # OpenAI-compat alternate spelling: prompt_tokens_details.cached_tokens
        details = (
            usage.get("prompt_tokens_details")
            if hasattr(usage, "get")
            else getattr(usage, "prompt_tokens_details", None)
        )
        if details is not None:
            hit = int(getattr(details, "cached_tokens", 0) or 0)

    wire_prompt = _get("prompt_tokens")
    completion = _get("completion_tokens")

    uncached = max(0, wire_prompt - hit)
    billed_input = uncached + hit + miss
    return {
        "input_tokens": uncached,
        "output_tokens": completion,
        "cache_read_tokens": hit,
        "cache_write_tokens": miss,
        "billed_input_tokens": billed_input,
        "billed_total_tokens": billed_input + completion,
    }


def _is_reasoning_starved(response: Any) -> bool:
    """Detect responses cut by the token cap before emitting content.

    Thinking models consume the max_tokens budget in reasoning_content.
    When the budget runs out mid-reasoning, the API returns
    finish_reason='length' with empty content and no tool calls.
    """
    # OpenAI-compatible shape
    try:
        choice = response.choices[0]
        finish = getattr(choice, "finish_reason", None)
        if finish == "length":
            msg = getattr(choice, "message", None)
            if msg is not None:
                content = getattr(msg, "content", None)
                tool_calls = getattr(msg, "tool_calls", None)
                if not content and not tool_calls:
                    return True
    except (AttributeError, IndexError, TypeError):
        pass
    # Ollama shape
    try:
        if response.get("done_reason") == "length":
            msg = response.get("message", {})
            if not msg.get("content") and not msg.get("tool_calls"):
                return True
    except (AttributeError, TypeError):
        pass
    return False


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
        # la carga debe dispararse si CUALQUIER campo pendiente es None,
        # no solo si nunca se cargó — un caller con max_retries explícito y
        # backoff None crasheaba con None**attempt en el primer retry.
        if (
            not self._config_loaded
            or self._max_retries is None
            or self._timeout is None
            or self._backoff_factor is None
        ):
            with self._config_lock:
                if (
                    self._max_retries is None
                    or self._timeout is None
                    or self._backoff_factor is None
                ):
                    from core.config import settings as app_settings

                    self._max_retries = (
                        app_settings.llm_max_retries
                        if self._max_retries is None
                        else self._max_retries
                    )
                    self._timeout = (
                        app_settings.llm_timeout if self._timeout is None else self._timeout
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
        max_retries: int | None = None,
        workspace: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """ÚNICO punto de entrada para todas las llamadas LLM.
        Soporta function-calling nativo vía tools= (OpenAI, DeepSeek, Grok, Ollama)."""
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

        client, model, temp, provider_effective = LLMProvider.get_client_with_provider(
            role, temperature
        )
        # el breaker se atribuye al servicio que REALMENTE atenderá la llamada
        cb = CircuitBreakerRegistry.get(provider_effective)

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
            # Cache-first: preserva el prefijo cacheable del transcript
            from core.cache_manager import cache_manager

            messages = cache_manager.cache_friendly_compress(messages, max_tokens=int(budget * 0.7))

        from core.config import settings as _settings

        role_config = _settings.model_roles.get(role, _settings.model_roles["default"])
        max_tokens = role_config.get("max_tokens")
        # Caller-provided max_tokens override (OpenAI style) — absorbed into
        # effective_max_tokens so the starvation retry doubles THIS value,
        # not the role default (kwargs.update re-aplicaba el
        # override del caller y el reintento nunca crecía el presupuesto).
        caller_max_tokens = kwargs.get("max_tokens") or kwargs.get("max_completion_tokens")
        effective_max_tokens: int | None = caller_max_tokens or max_tokens
        global_enabled = _settings.tool_calling_global
        effective_tools = (
            tools if role_config.get("tool_calling", True) and global_enabled else None
        )
        ollama_safe_kwargs = {
            k: v for k, v in kwargs.items() if k not in _OLLAMA_UNSUPPORTED_KWARGS
        }

        if stream:
            # la rama stream de call() usaba cliente SYNC bloqueante y
            # _normalize_response no soporta Stream — contrato público es
            # call_stream(). Se fuerza non-stream en vez de colgar el loop.
            logger.warning(
                "models.call(stream=True) no está soportado (usar call_stream) — "
                "forzando llamada non-streaming."
            )
            stream = False

        # ── Retry durable: presupuesto reducido por intentos ya gastados ──
        from core.config import settings as _rl_settings

        retry_ledger = None
        retry_key = ""
        if getattr(_rl_settings, "llm_retry_durable", True):
            from core.retry_ledger import RetryLedger

            # Use workspace-specific ledger if workspace provided, else global.
            # la instancia DEBE quedar en retry_ledger — antes se
            # guardaba solo en esta local (para el read inicial) y toda la
            # mitad escritora (bump/clear) era código muerto tras
            # `if retry_ledger is not None`.
            retry_ledger = rl = (
                RetryLedger.for_workspace(workspace) if workspace else RetryLedger.default()
            )
            retry_key = RetryLedger.key_for(role, messages)
            rl_used = rl.used(retry_key)
            if max_retries is not None:
                effective_attempt_cap = max(0, max_retries - rl_used)
            else:
                self._load_kairos_config()
                effective_attempt_cap = max(0, self.max_retries - rl_used)
            if rl_used and effective_attempt_cap == 0:
                logger.warning(
                    "Retry durable: %d/%d intentos ya gastados (persistidos) para %s "
                    "— saltando directo al fallback",
                    rl_used,
                    rl_used,
                    retry_key[:24],
                )
        else:
            effective_attempt_cap = max_retries if max_retries is not None else self.max_retries

        for attempt in range(1, effective_attempt_cap + 1):
            _max = max_retries if max_retries is not None else self.max_retries
            try:
                _start = time.monotonic()
                if isinstance(client, OpenAI):
                    call_kwargs: dict = {
                        "model": model,
                        "messages": messages,
                        "temperature": temp,
                        "stream": stream,
                    }
                    if effective_max_tokens:
                        call_kwargs["max_tokens"] = effective_max_tokens
                    if effective_tools:
                        call_kwargs["tools"] = effective_tools
                        call_kwargs["tool_choice"] = tool_choice
                    call_kwargs.update(kwargs)
                    if effective_max_tokens:
                        call_kwargs["max_tokens"] = effective_max_tokens
                    if stream:
                        response = client.chat.completions.create(**call_kwargs)
                    else:
                        response = await asyncio.wait_for(
                            asyncio.to_thread(
                                partial(client.chat.completions.create, **call_kwargs)
                            ),
                            timeout=_LLM_CALL_DEADLINE,
                        )
                else:
                    ollama_options: dict[str, Any] = {"temperature": temp}
                    if effective_max_tokens:
                        ollama_options["num_predict"] = effective_max_tokens
                    ollama_kwargs: dict[str, Any] = {
                        "model": model,
                        "messages": sanitize_messages_for_ollama(messages),
                        "stream": stream,
                        "options": ollama_options,
                    }
                    if effective_tools:
                        ollama_kwargs["tools"] = effective_tools
                    ollama_kwargs.update(ollama_safe_kwargs)
                    if stream:
                        response = client.chat(**ollama_kwargs)
                    else:
                        response = await asyncio.wait_for(
                            asyncio.to_thread(client.chat, **ollama_kwargs),
                            timeout=_LLM_CALL_DEADLINE,
                        )

                duration = time.monotonic() - _start

                # Reasoning-starvation retry: thinking models can burn the
                # whole token budget in reasoning_content, leaving an empty
                # answer. Retry with a larger budget instead of returning
                # a useless response.
                if _is_reasoning_starved(response) and attempt < _max:
                    effective_max_tokens = max(2048, (effective_max_tokens or 1024) * 2)
                    logger.warning(
                        "Respuesta vacía por agotamiento de tokens (reasoning) "
                        "en intento %d/%d (rol: %s) — reintentando con max_tokens=%d",
                        attempt,
                        _max,
                        role,
                        effective_max_tokens,
                    )
                    continue

                # Track usage + cache metrics (+ epoch del call-config)
                self._track_usage(
                    response,
                    workspace=workspace,
                    provider=provider_effective,
                    model=str(model) if model else None,
                    max_tokens=effective_max_tokens,
                )
                # Track tokens in workflow budget
                self._track_budget(response)

                cb.record_success()
                # Retry durable: éxito limpia el presupuesto persistido
                if retry_ledger is not None and retry_key:
                    retry_ledger.clear(retry_key)
                from core.metrics import metrics as _metrics

                _metrics.record_llm_latency(provider_effective, duration)
                _tokens = _response_tokens(response)
                logger.info(
                    "LLM call OK (rol=%s, provider=%s, duración=%.1fs%s)",
                    role,
                    provider_effective,
                    duration,
                    f", tokens={_tokens}" if _tokens is not None else "",
                )
                return self._normalize_response(response)

            except TimeoutError:
                logger.warning(
                    "⏳ Deadline (%gs) superado en intento %d/%d (rol: %s)",
                    _LLM_CALL_DEADLINE,
                    attempt,
                    _max,
                    role,
                )
                if retry_ledger is not None and retry_key:
                    retry_ledger.bump(retry_key)
            except (OpenAITimeoutError, httpx.TimeoutException):
                logger.warning("⏳ Timeout en intento %d/%d (rol: %s)", attempt, _max, role)
                if retry_ledger is not None and retry_key:
                    retry_ledger.bump(retry_key)
            except APIError as e:
                logger.warning(f"⚠️ APIError en intento {attempt}/{_max}: {e}")
                if retry_ledger is not None and retry_key:
                    retry_ledger.bump(retry_key)
            except Exception as e:
                logger.error(f"Error inesperado en llamada LLM (rol: {role})", exc_info=True)
                if retry_ledger is not None and retry_key:
                    retry_ledger.bump(retry_key)

            if attempt < _max:
                delay = self.backoff_factor**attempt + 0.5
                await asyncio.sleep(delay)

        # Fallback final: forzar Ollama
        logger.info("🔄 Todos los intentos fallaron → fallback forzado a Ollama")
        cb.record_failure()
        ollama_cb = CircuitBreakerRegistry.get("ollama")
        client, model, temp = LLMProvider.get_client(role, temperature, force_ollama=True)
        try:
            # Sanitize messages for Ollama SDK (string→dict args conversion)
            ollama_messages = sanitize_messages_for_ollama(messages)
            fallback_options: dict[str, Any] = {"temperature": temp}
            if caller_max_tokens:
                fallback_options["num_predict"] = caller_max_tokens

            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.chat,
                    model=model,
                    messages=ollama_messages,
                    options=fallback_options,
                    **({"tools": effective_tools} if effective_tools else {}),
                    **ollama_safe_kwargs,
                ),
                timeout=_LLM_CALL_DEADLINE,
            )
            self._track_budget(response)
            self._track_usage(
                response,
                workspace=workspace,
                provider="ollama",
                model=str(model) if model else None,
                max_tokens=caller_max_tokens,
            )
            ollama_cb.record_success()
            return self._normalize_response(response)
        except TimeoutError:
            logger.error(
                f"Fallback Ollama superó el deadline ({_LLM_CALL_DEADLINE:.0f}s)", exc_info=True
            )
            ollama_cb.record_failure()
            return self._create_error_response(
                f"Ollama tardó más de {_LLM_CALL_DEADLINE:.0f}s en responder."
            )
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
        workspace: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Stream unified chunks from the LLM with retry support.

        Yields StreamChunk with text, tool calls, and completion signal.
        Supports OpenAI, DeepSeek, Grok (via SDK) and Ollama (via API).
        On streaming failure, retries up to llm_max_retries times.
        """
        self._load_kairos_config()
        # `or 1` convertía un 0 explícito en 1 — respetar la config
        max_retries = self._max_retries if self._max_retries is not None else 1
        last_error: Exception | None = None

        # ── Circuit breaker check ──
        from core.circuit_breaker import CircuitBreakerRegistry

        # igual que call() — el breaker se atribuye al proveedor EFECTIVO
        # y NO se bloquea el stream: get_async_client ya cae a Ollama si el
        # primario está OPEN (política unificada call/call_stream).
        s_client, s_model, s_temp, stream_provider_effective = (
            LLMProvider.get_async_client_with_provider(role, temperature)
        )
        cb = CircuitBreakerRegistry.get(stream_provider_effective)

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
            # Cache-first: preserva el prefijo cacheable del transcript
            from core.cache_manager import cache_manager as _cache_mgr

            messages = _cache_mgr.cache_friendly_compress(messages, max_tokens=int(_budget * 0.7))

        _role_config = _app_settings.model_roles.get(role, _app_settings.model_roles["default"])
        _max_tokens = _role_config.get("max_tokens")
        _global_enabled = _app_settings.tool_calling_global
        _effective_tools = (
            tools if _role_config.get("tool_calling", True) and _global_enabled else None
        )

        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    # el reintento re-emite desde byte 0 — avisar al
                    # consumidor para truncar la salida parcial ya emitida.
                    yield StreamChunk(reset=True)
                client, model, temp = s_client, s_model, s_temp

                if isinstance(client, AsyncOpenAI):
                    stream_usage = None
                    async for chunk in self._stream_openai_async(
                        client,
                        model,
                        messages,
                        temp,
                        _effective_tools,
                        tool_choice,
                        max_tokens=_max_tokens,
                        **kwargs,
                    ):
                        if chunk.usage:
                            stream_usage = chunk.usage
                        yield chunk
                else:
                    stream_usage = None
                    async for chunk in self._stream_ollama(
                        client,
                        model,
                        messages,
                        temp,
                        max_tokens=_max_tokens,
                        tools=_effective_tools,
                        **kwargs,
                    ):
                        if chunk.usage:
                            stream_usage = chunk.usage
                        yield chunk
                # Track streaming tokens (budget + wire totals + disjoint buckets)
                if stream_usage:
                    self._track_stream_usage(
                        stream_usage,
                        workspace=workspace,
                        provider=stream_provider_effective,
                        model=str(s_model) if s_model else None,
                        max_tokens=_max_tokens,
                    )
                cb.record_success()
                return  # Success — exit retry loop
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Streaming error (attempt {attempt + 1}/{max_retries + 1}, role: {role}): {e}"
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
                tools=_effective_tools,
                tool_choice=tool_choice,
                max_retries=0,
                workspace=workspace,
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

        def _usage_to_dict(u) -> dict:
            return {
                "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
                "prompt_cache_hit_tokens": getattr(u, "prompt_cache_hit_tokens", 0) or 0,
                "prompt_cache_miss_tokens": getattr(u, "prompt_cache_miss_tokens", 0) or 0,
            }

        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            finish = chunk.choices[0].finish_reason if chunk.choices else None

            if finish:
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    # Provider incluyó usage en el propio chunk de finish
                    yield StreamChunk(
                        finish_reason=finish,
                        usage=_usage_to_dict(chunk_usage),
                        is_done=True,
                    )
                    break
                # con include_usage el chunk de usage llega DESPUÉS (choices=[])
                # NO hacer break; seguir consumiendo hasta recibirlo.
                yield StreamChunk(finish_reason=finish, is_done=True)
                continue

            # chunk final de usage (choices vacío) — contabilizar y terminar.
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                yield StreamChunk(usage=_usage_to_dict(chunk_usage), is_done=True)
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
        self, client, model, messages, temp, max_tokens=None, tools=None, **kwargs
    ) -> AsyncGenerator[StreamChunk, None]:
        """Streaming desde Ollama API con cola para streaming real.

        Usa una queue.Queue para comunicar el hilo bloqueante (Ollama sync)
        con el event loop asíncrono. Cada chunk se entrega tan pronto
        como llega, sin esperar a que terminen todos.
        """
        import queue

        # Sanitize messages for Ollama SDK (string→dict args conversion)
        messages = sanitize_messages_for_ollama(messages)

        chunk_queue: queue.Queue = queue.Queue()

        def _produce_chunks():
            try:
                ollama_options: dict[str, Any] = {"temperature": temp}
                if max_tokens:
                    ollama_options["num_predict"] = max_tokens
                response = client.chat(
                    model=model,
                    messages=messages,
                    stream=True,
                    options=ollama_options,
                    **({"tools": tools} if tools else {}),
                    **kwargs,
                )
                for chunk in response:
                    chunk_queue.put(chunk)
            except Exception as e:
                logger.exception("Error en streaming Ollama")
                chunk_queue.put(e)  # propagate so retry/circuit-breaker works
            finally:
                chunk_queue.put(None)  # Sentinel: fin del stream

        asyncio.get_running_loop().run_in_executor(None, _produce_chunks)

        while True:
            chunk = await asyncio.to_thread(chunk_queue.get)
            if isinstance(chunk, BaseException):
                raise chunk
            if chunk is None:
                break
            # ChatResponse (ollama ≥0.6) no es dict pero soporta .get()
            if chunk.get("done"):
                usage = None
                pe = chunk.get("prompt_eval_count")
                ec = chunk.get("eval_count")
                if pe is not None or ec is not None:
                    usage = {
                        "prompt_tokens": pe or 0,
                        "completion_tokens": ec or 0,
                    }
                yield StreamChunk(
                    finish_reason=chunk.get("done_reason", "stop"),
                    usage=usage,
                    is_done=True,
                )
                break
            msg = chunk.get("message", {})
            if msg.get("content"):
                yield StreamChunk(text=msg["content"])
            if msg.get("tool_calls"):
                for idx, tc in enumerate(msg["tool_calls"]):
                    func = tc.get("function", {})
                    raw_args = func.get("arguments")
                    log_raw_tool_args(f"stream_ollama:{func.get('name')}", raw_args)
                    # Normalise args to dict before serialising (future-proof)
                    if isinstance(raw_args, dict):
                        # is not None: {} vacío debe serializarse como "{}"
                        # para que el accumulador/repair loop lo procese
                        # (bug: `if raw_args` trataba {} como falsy).
                        args_str = json.dumps(raw_args) if raw_args is not None else None
                    elif isinstance(raw_args, str):
                        args_str = json.dumps(normalize_arguments(raw_args))
                    else:
                        args_str = None
                    logger.debug(
                        "Ollama stream tool_call[%d]: name=%s args_type=%s",
                        idx,
                        func.get("name"),
                        type(raw_args).__name__,
                    )
                    yield StreamChunk(
                        tool_name=func.get("name"),
                        tool_arguments=args_str,
                        tool_call_id=tc.get("id") or f"call_{idx}",
                    )

    def _normalize_response(self, raw_response: Any) -> Any:
        if hasattr(raw_response, "choices"):
            return raw_response

        has_get = hasattr(raw_response, "get")
        content = (
            raw_response.get("message", {}).get("content", "") if has_get else str(raw_response)
        )
        tool_calls = raw_response.get("message", {}).get("tool_calls") if has_get else None
        return _NormalizedResponse(
            choices=[
                _Choice(
                    message=_Message(content=content, tool_calls=tool_calls),
                    finish_reason=(raw_response.get("done_reason", "stop") if has_get else "stop"),
                )
            ]
        )

    def _create_error_response(self, message: str):
        return _NormalizedResponse(choices=[_Choice(message=_Message(content=f"❌ {message}"))])

    @staticmethod
    def _track_usage(
        response: Any,
        workspace: str | None = None,
        provider: str = "",
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """Extract token usage and cache metrics from LLM response."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return

        from core.cache_manager import cache_manager
        from core.metrics import metrics as m

        # Epoch del call-config (conservador: provider|model|max_tokens)
        if provider or model:
            cache_manager.note_epoch(workspace or "main", provider, model, max_tokens)

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

        # Disjoint token-meter buckets (DeepSeek rule: prompt_tokens includes hits)
        # UNA sola llamada a track_usage por respuesta — antes se
        # invocaba dos veces (sin buckets + con buckets) y llm_calls/tokens
        # quedaban duplicados en CacheManager.
        d = _disjoint_usage(usage)
        if d:
            m.record_disjoint_usage(
                input_tokens=d["input_tokens"],
                output_tokens=d["output_tokens"],
                cache_read_tokens=d["cache_read_tokens"],
                cache_write_tokens=d["cache_write_tokens"],
            )
        cache_manager.track_usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_cache_hit_tokens=cache_hit,
            prompt_cache_miss_tokens=cache_miss,
            workspace=workspace or "main",
            uncached_input_tokens=d["input_tokens"] if d else None,
            cache_write_tokens=d["cache_write_tokens"] if d else None,
        )

    @staticmethod
    def _track_stream_usage(
        usage: dict | None,
        workspace: str | None = None,
        provider: str = "",
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """Track streaming usage: workflow budget + wire totals + disjoint buckets."""
        if not usage:
            return
        from core.cache_manager import cache_manager
        from core.metrics import metrics as m

        # Epoch del call-config (conservador: provider|model|max_tokens)
        if provider or model:
            cache_manager.note_epoch(workspace or "main", provider, model, max_tokens)

        prompt_tokens = usage.get("prompt_tokens", 0) or 0
        completion_tokens = usage.get("completion_tokens", 0) or 0
        cache_hit = usage.get("prompt_cache_hit_tokens", 0) or 0
        cache_miss = usage.get("prompt_cache_miss_tokens", 0) or 0

        # al budget solo llega el coste real: los cache hits no lo queman
        # (misma regla que _track_budget)
        total = max(0, prompt_tokens - cache_hit) + completion_tokens
        if total > 0:
            from tools.orchestrator import add_llm_token_usage

            add_llm_token_usage(total)

        m.record_llm_usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_hit_tokens=cache_hit,
            cache_miss_tokens=cache_miss,
        )
        d = _disjoint_usage(usage)
        if d:
            m.record_disjoint_usage(
                input_tokens=d["input_tokens"],
                output_tokens=d["output_tokens"],
                cache_read_tokens=d["cache_read_tokens"],
                cache_write_tokens=d["cache_write_tokens"],
            )
        # una sola llamada (ver _track_usage)
        cache_manager.track_usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_cache_hit_tokens=cache_hit,
            prompt_cache_miss_tokens=cache_miss,
            workspace=workspace or "main",
            uncached_input_tokens=d["input_tokens"] if d else None,
            cache_write_tokens=d["cache_write_tokens"] if d else None,
        )

    @staticmethod
    def _track_budget(response: Any) -> None:
        """Track actual LLM API tokens in the workflow token budget.

        Los cache hits no queman presupuesto: el prefijo estable del system
        prompt se sirve cacheado (barato/gratis) y contar lo cobraría dos
        veces el mismo contexto re-enviado por llamada.
        """
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        total_tokens = getattr(usage, "total_tokens", 0) or 0
        if total_tokens <= 0:
            return
        billed = max(0, total_tokens - _cache_hit_tokens(usage))
        if billed <= 0:
            return
        from tools.orchestrator import add_llm_token_usage

        add_llm_token_usage(billed)


# Single global instance
models = ModelsController()
