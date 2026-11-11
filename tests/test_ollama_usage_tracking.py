# tests/test_ollama_usage_tracking.py — usage de Ollama en el camino no-streaming
"""La forma Ollama (prompt_eval_count/eval_count) debe alimentar el
presupuesto y las métricas igual que la forma OpenAI (.usage): el
early-return silencioso dejaba bots/rutinas/headless con presupuesto ciego."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from llm.controller import ModelsController, _response_tokens


def _ollama_response(prompt: int = 2000, completion: int = 1000) -> dict:
    """Forma real del ChatResponse no-streaming de ollama (Mapping con .get)."""
    return {"prompt_eval_count": prompt, "eval_count": completion}


class TestOllamaNoStreaming:
    def test_track_budget_cuenta_la_forma_ollama(self):
        with patch("tools.orchestrator.add_llm_token_usage") as mock_add:
            ModelsController._track_budget(_ollama_response())
        mock_add.assert_called_once_with(3000)

    def test_track_usage_registra_prompt_y_completion(self):
        m = MagicMock()
        with patch("core.metrics.metrics", m):
            ModelsController._track_usage(_ollama_response(), provider="ollama")
        m.record_llm_usage.assert_called_once()
        kwargs = m.record_llm_usage.call_args.kwargs
        assert kwargs["prompt_tokens"] == 2000
        assert kwargs["completion_tokens"] == 1000

    def test_response_tokens_forma_ollama(self):
        assert _response_tokens(_ollama_response(500, 100)) == 600

    def test_forma_openai_intacta(self):
        resp = SimpleNamespace(
            usage=SimpleNamespace(
                total_tokens=1000,
                prompt_tokens=1000,
                completion_tokens=50,
                prompt_cache_hit_tokens=800,
                prompt_cache_miss_tokens=200,
            )
        )
        with patch("tools.orchestrator.add_llm_token_usage") as mock_add:
            ModelsController._track_budget(resp)
        # total preferente (1000) − cache hits (800) = 200
        mock_add.assert_called_once_with(200)

    def test_sin_usage_no_inventa(self):
        with patch("tools.orchestrator.add_llm_token_usage") as mock_add:
            ModelsController._track_budget({"done": True})
        mock_add.assert_not_called()

    def test_cache_details_openai_sigue_respetado(self):
        resp = SimpleNamespace(
            usage=SimpleNamespace(
                total_tokens=1000,
                prompt_tokens=1000,
                completion_tokens=0,
                prompt_cache_hit_tokens=0,
                prompt_cache_miss_tokens=0,
                prompt_tokens_details=SimpleNamespace(cached_tokens=700),
            )
        )
        with patch("tools.orchestrator.add_llm_token_usage") as mock_add:
            ModelsController._track_budget(resp)
        mock_add.assert_called_once_with(300)
