# Analytics Tab

The Analytics tab displays real-time metrics about Morphix's performance, resource usage, and API consumption. All metrics refresh every 5 seconds.

## Layout

The Analytics tab has two metric groups:

1. **Métricas en Tiempo Real** (Real-Time Metrics) — 7 key performance indicators
2. **Rate Limiter** — per-minute and per-hour API quota usage

## Real-Time Metrics

| Metric | Description | Source |
|--------|-------------|--------|
| **Uptime** | Seconds since Morphix started. Format: `12345s` | `core.metrics.uptime_seconds` |
| **Total tokens** | Cumulative tokens used across all LLM calls (prompt + completion). Format: `123456` | `core.metrics.total_tokens` |
| **Workflows** | Completed workflows / Total workflows started, with success rate label. Format: `8/10 (80.00%)` | `core.metrics.completed_workflows` / `total_workflows` |
| **Success rate** | Percentage of workflows that completed successfully. Format: `80.00%` | `core.metrics.success_rate` |
| **Llm calls** | Total number of LLM API calls made (streaming + non-streaming). Format: `456` | `core.metrics.llm_calls` |
| **Tool calls** | Total number of tool executions across all agents. Format: `1230` | `core.metrics.tool_calls` |
| **Rate limited** | Number of requests rejected by the rate limiter. Format: `3` | `core.metrics.rate_limited` |

## Rate Limiter Status

The Rate Limiter enforces per-minute and per-hour quotas to prevent API abuse and manage costs:

| Metric | Description |
|--------|-------------|
| **Minute used** | Number of API calls made in the current minute window |
| **Minute max** | Maximum API calls allowed per minute |
| **Hour used** | Number of API calls made in the current hour window |
| **Hour max** | Maximum API calls allowed per hour |

The rate limiter uses a sliding window algorithm (`core/rate_limiter.py`). When a quota is exceeded, calls are rejected until the window slides forward. Rejected calls increment the "Rate limited" counter.

!!! note "Rate limiter configuration"
    Quota values are configured in `core/rate_limiter.py` and can be adjusted in `.env`:
    - `MAX_REQUESTS_PER_MINUTE` — max calls per 60-second window
    - `MAX_REQUESTS_PER_HOUR` — max calls per 3600-second window

## Interpreting the Metrics

### Healthy System

- **Success rate** above 80%
- **Rate limited** at 0 or close to 0
- **Tool calls** proportional to LLM calls (roughly 3-5 tool calls per LLM call is normal for orchestrated workflows)

### Warning Signs

- **Success rate** dropping below 50% — agents may be failing to complete tasks; check the Log tab in Maestro for error details
- **Rate limited** increasing — you're hitting API quotas; consider reducing workflow complexity or increasing quotas
- **Tool calls** abnormally high relative to LLM calls — agents may be stuck in retry loops; check the subtask list for repeating tasks

### Cost Estimation

Multiply **Total tokens** by your provider's per-token rate to estimate API costs:

- DeepSeek: ~$0.14/1M input tokens, ~$0.28/1M output tokens (prices vary by model)
- OpenAI: varies by model (GPT-4o, GPT-4o-mini, etc.)
- Ollama: **free** (runs locally on your hardware)

## Auto-Refresh

The Analytics tab refreshes every 5 seconds via a QTimer. There is no manual refresh button — the data updates automatically. The refresh calls `core.metrics.to_dict()` and `core.rate_limiter.get_rate_limiter()` to get current values.

## Related Monitoring

For more detailed monitoring during workflows:

- **Maestro → Log tab**: Detailed execution log with timestamps and agent transitions
- **Maestro → Execution panel**: Per-subtask status (✅ completed, 🔵 running, ❌ failed, ⏳ pending)
- **Config → Sistema tab**: Live CPU and memory usage
- **Dashboard**: High-level session statistics (tokens, workflows, uptime)
