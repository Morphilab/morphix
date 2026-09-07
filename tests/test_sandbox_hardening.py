"""Sandbox hardened — ejecución en HIJO CONFINADO.

RestrictedPython (core/sandbox/runner.py) corre en un proceso separado del
host; el cap de memoria es child-scoped; el timeout mata al hijo con SIGKILL
(no hay threads zombis en el host).
Los tests son blackbox vía execute(): el contrato de resultados es el
mismo que el del executor in-process que reemplazó."""

import asyncio
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from core.sandbox.restricted_executor import RestrictedExecutor


@pytest.mark.asyncio
async def test_sqlite_load_extension_blocked():
    """La conexión sandbox NO expone enable_load_extension — no hay vía de RCE."""
    code = (
        "import sqlite3\n"
        "c = sqlite3.connect(':memory:')\n"
        "c.enable_load_extension(True)\n"
        "print('RCE posible')\n"
    )
    result = await RestrictedExecutor.execute(code)
    assert result.get("success") is False
    assert "RCE posible" not in str(result.get("text", ""))


@pytest.mark.asyncio
async def test_sqlite_normal_usage_still_works():
    """La API legítima (execute/fetch/commit) sigue funcionando."""
    code = (
        "import sqlite3\n"
        "c = sqlite3.connect(':memory:')\n"
        "cur = c.cursor()\n"
        "cur.execute('CREATE TABLE t (x INT)')\n"
        "cur.execute('INSERT INTO t VALUES (42)')\n"
        "c.commit()\n"
        "cur.execute('SELECT x FROM t')\n"
        "row = cur.fetchone()\n"
        "print(row[0])\n"
    )
    result = await RestrictedExecutor.execute(code)
    assert result.get("success") is True, result
    assert "42" in str(result.get("text", ""))


@pytest.mark.asyncio
async def test_pyplot_rcparams_blocked_but_plotting_works():
    """plt es proxy read-only — sin rcParams ni asignación hostil."""
    bad = await RestrictedExecutor.execute("plt.rcParams['axes.grid'] = True\n")
    assert bad.get("success") is False

    good = await RestrictedExecutor.execute(
        "plt.figure()\n"
        "plt.plot([1, 2, 3], [1, 4, 9])\n"
        "plt.title('test')\n"
        "print('plot ok')\n",
        timeout=60,
    )
    assert good.get("success") is True, good


@pytest.mark.asyncio
async def test_timeout_mata_al_hijo_sin_zombie_persistente():
    """El timeout force-kill al hijo (SIGKILL) y la métrica heredada
    se registran; NO queda nada ocupado — la siguiente ejecución sale limpia
    (el hilo excedido no debe sobrevivir al timeout como zombi ni retener
    recursos del host)."""
    from core.metrics import metrics

    slow_code = (
        "import datetime\n"
        "t0 = datetime.datetime.now()\n"
        "while (datetime.datetime.now() - t0).total_seconds() < 20:\n"
        "    pass\n"
    )
    zombies_before = getattr(metrics, "sandbox_zombie_threads", 0)
    t0 = time.monotonic()
    first = await RestrictedExecutor.execute(slow_code, timeout=2)
    elapsed = time.monotonic() - t0
    assert first.get("success") is False
    assert "time exceeded" in str(first.get("text", ""))
    assert elapsed < 10, f"el kill del hijo no fue oportuno ({elapsed:.2f}s)"
    assert (
        getattr(metrics, "sandbox_zombie_threads", 0) > zombies_before
    ), "métrica sandbox_zombie_threads no incrementada tras el kill"

    # Sin zombie persistente: la ejecución siguiente funciona de inmediato
    second = await RestrictedExecutor.execute("print('limpio')", timeout=30)
    assert second.get("success") is True, second
    assert "limpio" in str(second.get("text", ""))


@pytest.mark.asyncio
async def test_memory_limit_controlled_memoryerror(monkeypatch):
    """El cap de memoria aplica EN EL HIJO — una asignación
    fuera del cap da error controlado y el host permanece intacto.

    Cap pequeño fijo (32 MB) + asignación del doble (64 MB de punteros):
    excede SIEMPRE el cap por construcción."""
    cap_mb = 32
    n = (cap_mb * 2 * 1024 * 1024) // 8
    code = f"x = [0] * {n}\nprint(len(x))\n"
    with patch("core.config.settings.sandbox_memory_mb", cap_mb, create=True):
        result = await RestrictedExecutor.execute(code, timeout=60)
    assert result.get("success") is False
    assert (
        "MemoryError" in str(result.get("text", ""))
        or "memoria" in str(result.get("text", "")).lower()
    )


@pytest.mark.asyncio
async def test_numpy_ctypes_rce_blocked():
    """np.ctypeslib.ctypes inaccesible — payload clásico de RCE vía CDLL."""
    code = (
        "import numpy as np\n"
        "ct = np.ctypeslib.ctypes\n"
        "libc = ct.CDLL('libc.so.6')\n"
        "print('RCE confirmado:', libc.getpid())\n"
    )
    result = await RestrictedExecutor.execute(code)
    assert result.get("success") is False
    assert "RCE confirmado" not in str(result.get("text", ""))


@pytest.mark.asyncio
async def test_numpy_f2py_and_load_library_blocked():
    """np.f2py y np.ctypeslib.load_library también bloqueados."""
    r1 = await RestrictedExecutor.execute("import numpy as np\nnp.f2py\n")
    assert r1.get("success") is False

    r2 = await RestrictedExecutor.execute(
        "import numpy as np\nnp.ctypeslib.load_library('libc', '.')\n"
    )
    assert r2.get("success") is False


@pytest.mark.asyncio
async def test_numpy_math_subset_still_works():
    """El subconjunto matemático legítimo sigue operativo tras el proxy."""
    code = (
        "import numpy as np\n"
        "a = np.array([1.0, 2.0, 3.0, 4.0])\n"
        "m = np.mean(a)\n"
        "n = np.linalg.norm(a)\n"
        "r = np.random.randint(0, 10)\n"
        "print(round(m, 4), round(n, 4), 0 <= r < 10)\n"
    )
    result = await RestrictedExecutor.execute(code, timeout=60)
    assert result.get("success") is True, result
    text = str(result.get("text", ""))
    assert "2.5" in text and "5.4772" in text and "True" in text


@pytest.mark.asyncio
async def test_sandbox_output_capped_at_64k():
    """La salida capturada (print) se trunca a ~64KB con marcador visible."""
    code = 'print("A" * 300000)\n'
    result = await RestrictedExecutor.execute(code)
    assert result.get("success") is True, result
    out = str(result.get("text", ""))
    assert len(out) < 80_000
    assert "truncad" in out.lower()


@pytest.mark.asyncio
async def test_sandbox_repl_value_capped_at_64k():
    """El valor REPL (última expresión sin print) también se trunca."""
    code = '"A" * 300000\n'
    result = await RestrictedExecutor.execute(code)
    assert result.get("success") is True, result
    out = str(result.get("text", ""))
    assert len(out) < 80_000
    assert "truncad" in out.lower()


@pytest.mark.asyncio
async def test_sandbox_exception_message_capped_at_64k():
    """La ruta de excepciones TAMBIÉN respeta el cap de 64KB."""
    code = 'raise ValueError("E" * 300000)\n'
    result = await RestrictedExecutor.execute(code)
    assert result.get("success") is False
    out = str(result.get("text", ""))
    assert len(out) < 80_000
    assert "truncad" in out.lower()


def test_default_memory_limit_is_512mb():
    """RLIMIT_AS activo por defecto — sandbox_memory_mb=512.

    El cap aplica EN EL HIJO (child-scoped) — no hace falta el
    guard VmSize del host ni la serialización por lock. El default del
    MODELO se lee sin depender del .env local del desarrollador."""
    from core.config import Settings

    field = Settings.model_fields["sandbox_memory_mb"]
    default = getattr(field, "default", None)
    assert default == 512


def test_runner_no_importa_core():
    """El hijo jamás importa core.* (settings/.env/secretos del host
    no deben vivir en la memoria de un proceso que ejecuta código hostil).
    Guard estructural sobre el source del runner."""
    import inspect

    from core.sandbox import runner

    src = inspect.getsource(runner)
    prohibidos = ("from core", "import core", "core.config", "core.path_resolver")
    for p in prohibidos:
        assert p not in src, f"runner.py no debe referenciar '{p}'"


@pytest.mark.asyncio
async def test_concurrencia_maxima_de_hijos_aplica_backpressure():
    """Más ejecuciones simultáneas que MAX_CONCURRENT_CHILDREN ⇒ rechazo
    inmediato (backpressure), y al liberarse se vuelve a ejecutar."""
    sleep_code = (
        "import datetime\n"
        "t0 = datetime.datetime.now()\n"
        "while (datetime.datetime.now() - t0).total_seconds() < 8:\n"
        "    pass\n"
        "print('fin')\n"
    )
    from core.sandbox import restricted_executor as re_mod

    max_children = re_mod.MAX_CONCURRENT_CHILDREN
    tareas = [
        asyncio.create_task(RestrictedExecutor.execute(sleep_code, timeout=30))
        for _ in range(max_children)
    ]
    await asyncio.sleep(2.5)  # dar tiempo a que los hijos arranquen
    extra = await RestrictedExecutor.execute("print('x')", timeout=30)
    assert extra.get("success") is False
    assert "ocupado" in str(extra.get("text", "")).lower()
    resultados = await asyncio.gather(*tareas)
    assert all(r.get("success") is True for r in resultados), resultados

    tras = await RestrictedExecutor.execute("print('libre')", timeout=30)
    assert tras.get("success") is True, tras
    assert "libre" in str(tras.get("text", ""))


# ── savefig acotado a charts/ ──────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ruta",
    ["/tmp/morphix_audit_pwn.png", "../escape.png", "~/pwn.png"],
)
async def test_savefig_fuera_de_charts_bloqueado(ruta):
    """plt.savefig con ruta absoluta/traversal/home fuera de charts/ ⇒ bloqueado."""
    code = f"plt.savefig({ruta!r})"
    result = await RestrictedExecutor.execute(code, timeout=60)
    assert result.get("success") is False
    assert "charts" in str(result.get("text", ""))
    assert not Path(str(Path.home() / "pwn.png")).exists()
    assert not Path("/tmp/morphix_audit_pwn.png").exists()


@pytest.mark.asyncio
async def test_savefig_relativo_cae_en_charts():
    """El nombre relativo (caso legítimo) aterriza dentro de charts/."""
    from core.path_resolver import paths

    destino = paths.charts_dir() / "audit_savefig_fence_test.png"
    try:
        result = await RestrictedExecutor.execute(
            "plt.savefig('audit_savefig_fence_test.png')", timeout=60
        )
        assert result.get("success") is True
        assert destino.exists(), "el PNG debe crearse bajo charts/"
    finally:
        destino.unlink(missing_ok=True)
