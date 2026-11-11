# Daemon headless

El daemon permite que las **rutinas de Bot Mode** y el **wake de
`pending_turns`** sigan corriendo sin la GUI abierta. Es la versión v1 del
"plano persistente": lo que sobrevive al cierre de la app son las filas de la
BD (`routines.next_run_at`, `pending_turns`, `PausedSession`, conversaciones);
el daemon es el proceso que las ejecuta.

## Qué hace (v1)

- Tick del **scheduler de rutinas** (cada `bots.routines_interval_seconds`,
  default 30 s).
- **Wake** de `pending_turns` (cada `bots.wake_interval_seconds`, default 5 s).
- **Kairos/offline** vía `start_daemons()`.
- Un tick de cada loop con `--once` (para cron/systemd timer).
- Log propio en `logs/daemon.log` (nunca el terminal de la GUI).

## Qué NO hace (v2/v3)

- No hospeda una **cola de workflows autónomos** (no existe planificador de
  workflows: los presets se lanzan desde la GUI).
- No corre el **indexado de embeddings** como servicio.
- No hospeda el **MCP server**.
- No llama `switch_workspace`: trabaja en el workspace del bootstrap y usa
  `bound_schema` por run (cambiar el schema global desde un proceso de fondo
  corrompería runs concurrentes de la GUI).

## Uso

```bash
uv run python -m core.daemon --workspace main
uv run python -m core.daemon --once            # un tick y sale (cron)
uv run python -m core.daemon --dry-run         # bootstrap + lock, sin ticks
uv run python -m core.daemon --max-seconds 60  # parada automática (tests)
```

- **Instancia única**: advisory lock dedicado (917231002) tomado en una
  conexión que vive todo el proceso. Un segundo arranque sale con código 0 y un
  log `otro daemon ya corre`.
- **Parada**: SIGTERM/SIGINT cancela los loops, `stop_daemons()`, libera el
  lock y cierra el pool. Los runs DSL escriben journal parcial por step:
  un crash no pierde el progreso.
- **Coexistencia con la GUI**: el scheduler de rutinas usa un advisory lock
  (917231001) y el wake usa `SKIP LOCKED`; ambos procesos no duplican ticks.
  El rate limiter del LLM es por-proceso (documentado): con GUI+daemon el
  límite efectivo se duplica.

## systemd --user (opcional)

```ini
# ~/.config/systemd/user/morphix-daemon.service
[Unit]
Description=Morphix headless daemon
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/morphix
ExecStart=%h/morphix/.venv/bin/python -m core.daemon --workspace main
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now morphix-daemon
journalctl --user -u morphix-daemon -f
```
