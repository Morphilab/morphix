# Bot Mode en Morphix

> Implementación del contrato bot-mode de Morphix: cada bot es un agente con identidad
> conversacional propia, un chat eterno canónico por bot, transportes aislados (DM,
> salas, rutinas) y persistencia por workspace. Fuera de alcance v1: peers cross-machine
> y gateways externos.

## Qué es

Cada **bot** es una fila `bots` del schema de SU workspace ("isla", C9) con identidad
conversacional eterna por NOMBRE: `(bot_id, conversación titulada exactamente "Bot Chat")`,
garantizada por el índice único parcial `uq_conversation_bot_chat` declarado EN
`__table_args__` de `Conversation` (los schemas workspace no reciben índices de migraciones).

## Contratos vivos

| # | Contrato | Dónde vive aquí |
|---|----------|-----------------|
| C1 | Identidad = nombre + índice parcial ≤1 fila | `core/models.py`, `tests/test_bots_identity.py` |
| C2 | Cero punteros session-id | flows de `core/bots_chat`; tripwire `test_bots_e2e` |
| C3 | adopt-before-mint fail-closed | `core/bots_chat.ensure_open/resolve_canonical` |
| C4 | Eternos SIEMPRE ocultos + sweep | `sweep_hidden_bot_chats`, filtro repo |
| C5 | preview == click, recencia jamás gana | `bots_service.roster_with_previews` |
| C6 | Prompt caching sagrado + epoch ×1 | `core/bots_epoch.refresh_if_stale` |
| C7 | send_to_bot inyectado NO registrado, re-gate | `core/bots_gate` + loop |
| C8 | Fire-and-forget idle-first sin splices | `orchestration/bots_wake` |
| C9 | Islas por workspace | todo el subsistema (schema activo) |
| C10 | ui_meta CAS ≤64KiB revisiones sobreviven | `core/bots.set_ui_meta/meta_history` |
| C11 | Rutinas machine-local | `core/bots_routines.enqueue_bot_chat_delivery` |
| C12 | Sin splices: turnos user-role reales | wake/clock/drive usan `WorkflowContext` |

## Flags (`kairos`)

- `BOT_MODE` (default true) — master.
- `BOT_MODE_PROTOCOL` (default true) — DMs entre bots + sección de protocolo.
- `bots.wake_interval_seconds` (5s) / `bots.routines_interval_seconds` (30s).

## Daemon processes

`desktop/main_window.init_backend` registra:
1. `orchestration.bots_wake.bots_wake_loop` — drena `pending_turns` FIFO
   (claim SKIP LOCKED), single-flight por bot, TTL de hops anti-bucle,
   persiste el intercambio en el chat eterno.
2. `orchestration.bots_clock.routines_loop` — tick de rutinas vencidas bajo
   advisory-lock PG; entrega dual `history` (conv "⏰ <nombre>") /
   `bot-chat` (cola con atribución `[Rutina]`).

## Grupos

Sala = `group_rooms` (roomId inmutable `r<unix>-<rand>`); log ordenado único
`group_messages.seq`; caps 2..6 miembros · ≤10 msgs · silencio 180s '(pass)' ·
cap sesión 20 min. Cada respuesta corre DESDE la sesión oculta
`Group:<roomId>` del bot propio; menciones deterministas por orden de aparición;
disband conserva sesiones miembro.

## Schedules soportados (rutinas)

`30m` · `every 2h` · cron5 `0 9 * * 1` · ISO one-shot. Catchup clamp 120s–2h
(mitad del período); oneshot se autodeshabilita al disparar.

## Testing

```
poetry run pytest tests/test_bots_identity.py tests/test_bots_lifecycle.py \
    tests/test_bots_chat.py tests/test_bots_epoch.py tests/test_bots_messaging.py \
    tests/test_bots_wake.py tests/test_bots_routines.py tests/test_bots_groups.py \
    tests/test_bots_e2e.py tests/test_bots_tab_smoke.py \
    tests/test_bots_gate.py tests/test_bots_simple_route.py \
    tests/test_bots_untrusted.py tests/test_bots_routine_limits.py \
    tests/test_bots_delivery_lifecycle.py
```

Requiere `DATABASE_URL` (PG real): los contratos de unicidad/CAS/skip-locked no
se prueban contra fakes. Recuerda exportarlo (pytest NO carga `.env`):
`export DATABASE_URL=$(grep '^DATABASE_URL=' .env | cut -d= -f2-)`.
