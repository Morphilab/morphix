# core/embedding_provider.py
import hashlib
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# campaña de carga con reintentos y backoff exponencial
_MAX_LOAD_ATTEMPTS = 3
_LOAD_BACKOFF_SECONDS = 2.0

# TTL del probe de disponibilidad de Ollama (segundos)
_OLLAMA_PROBE_TTL = 30.0
_OLLAMA_PROBE_CACHE: dict[str, float] = {}

# Registro de perfiles por familia — los prefijos query/passage son
# ESPECÍFICOS de e5; aplicarlos a otras familias degrada la calidad.
# dim = dimensión esperada del modelo (validador del stamp de huella).
MODEL_PROFILES: dict[str, dict] = {
    "e5": {
        "match": ("e5",),
        "query_prefix": "query: ",
        "passage_prefix": "passage: ",
        "dim": 1024,
        "normalize": True,
    },
    "bge": {"match": ("bge",), "dim": 1024, "normalize": True},
    "nomic": {"match": ("nomic",), "dim": 768, "normalize": True},
    "mxbai": {"match": ("mxbai",), "dim": 1024, "normalize": True},
    # dim medida contra integrate.api.nvidia.com: nemotron-3-embed-1b
    # responde 2048.
    "nemotron": {"match": ("nemotron",), "dim": 2048, "normalize": True},
    # Qwen3-Embedding-8B vía DeepInfra: dim publicada 4096 (verificada contra
    # el endpoint). Qwen3 es
    # asimétrico con instrucción: SOLO las queries de retrieval llevan
    # prefijo; los documentos van limpios ("No need to add instruction for
    # retrieval documents", model card de Qwen3). encode_texts lo aplica
    # por kind. input_type NO se envía para este modelo: el parámetro del
    # endpoint solo afecta a modelos VL multimodales.
    "qwen3-embedding": {
        "match": ("qwen3-embedding",),
        "query_prefix": (
            "Instruct: Given a web search query, retrieve relevant passages "
            "that answer the query\nQuery: "
        ),
        "dim": 4096,
        "normalize": True,
    },
    "openai": {"match": ("text-embedding",), "dim": None, "normalize": False},
    "plain": {"match": (), "dim": None, "normalize": True},
}


def profile_for(model_name: str) -> tuple[str, dict]:
    """Retorna (familia, perfil) para un nombre de modelo HF/ruta local."""
    lowered = (model_name or "").lower()
    for family, prof in MODEL_PROFILES.items():
        if family != "plain" and any(tok in lowered for tok in prof["match"]):
            return family, prof
    return "plain", MODEL_PROFILES["plain"]


def _dim_of(obj) -> int:
    """Dimensión declarada por un backend/embedder (0 si desconocida).

    Acepta property (int), método (callable) o ausencia total — los fakes de
    tests y los embedders legacy no siempre exponen `dimension`.
    """
    d = getattr(obj, "dimension", 0)
    try:
        if callable(d) and not isinstance(d, int):
            d = d()
        return int(d)
    except (TypeError, ValueError):
        return 0


class OllamaEmbedBackend:
    """Embeddings vía POST {base}/api/embed — 100% local."""

    def __init__(self, model: str):
        self.model = model

    @property
    def dimension(self) -> int:
        return int(profile_for(self.model)[1].get("dim") or 0)

    @property
    def fingerprint(self) -> str:
        # Legible (no sha): los stamps de remotos conviene poder leerlos.
        from core.config import settings

        return f"ollama:{self.model}:norm={settings.embed_normalize}"

    def available(self) -> bool:
        import httpx

        from core.config import settings

        base = str(settings.ollama_base_url).rstrip("/")
        now = time.monotonic()
        cached = _OLLAMA_PROBE_CACHE.get(base)
        if cached is not None and now - cached < _OLLAMA_PROBE_TTL:
            return cached > 0
        try:
            r = httpx.get(f"{base}/api/version", timeout=1.5)
            ok = r.status_code == 200
        except Exception:  # noqa: BLE001 — probe barato, cualquier fallo ⇒ re-probe
            return False
        _OLLAMA_PROBE_CACHE[base] = now  # solo los éxitos se cachean
        return ok

    def encode_texts(self, texts: list[str], kind: str = "passage") -> np.ndarray:
        """(N, dim) normalizado L2 según perfil de familia."""

        import httpx

        from core.config import settings

        _, prof = profile_for(self.model)
        prefix = prof.get("query_prefix", "") if kind == "query" else prof.get("passage_prefix", "")
        payload_in = [f"{prefix}{t}" if prefix else t for t in texts]
        base = str(settings.ollama_base_url).rstrip("/")
        r = httpx.post(
            f"{base}/api/embed",
            json={"model": self.model, "input": payload_in},
            timeout=60.0,
        )
        r.raise_for_status()
        rows = r.json().get("embeddings") or []
        arr = np.asarray(rows, dtype="float32")
        if settings.embed_normalize and arr.size:
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            arr = arr / norms
        return arr.astype("float32")


class OpenAIEmbedBackend:
    """Backend online OpenAI-compatible — text-embedding-3-* o cualquier
    endpoint /v1/embeddings (p. ej. NVIDIA integrate con
    nvidia/nemotron-3-embed-1b)."""

    BASE = "https://api.openai.com/v1"

    def __init__(self, model: str):
        self.model = model

    @property
    def base_url(self) -> str:
        from core.config import settings

        return str(settings.embed_openai_base_url or self.BASE).rstrip("/")

    @property
    def api_key(self) -> str:
        """Clave DEDICADA de embeddings primero; retrocompatibilidad con la
        del chat de OpenAI si el usuario no configuró la propia."""
        from core.config import settings

        return str(settings.embed_openai_api_key or settings.openai_api_key or "")

    @property
    def dimension(self) -> int:
        # 3-small=1536, 3-large=3072; derivado del perfil si se conoce
        name = self.model.lower()
        if "large" in name:
            return 3072
        if "small" in name:
            return 1536
        return int(profile_for(self.model)[1].get("dim") or 0)

    @property
    def fingerprint(self) -> str:
        from urllib.parse import urlparse

        from core.config import settings

        host = urlparse(self.base_url).netloc or "unknown"
        return f"openai:{self.model}@{host}:norm={settings.embed_normalize}"

    def available(self) -> bool:
        return bool(self.api_key)

    def encode_texts(self, texts: list[str], kind: str = "passage") -> np.ndarray:
        import httpx

        from core.config import settings

        _, prof = profile_for(self.model)
        normalize = bool(prof.get("normalize", settings.embed_normalize))
        prefix = prof.get("query_prefix", "") if kind == "query" else prof.get("passage_prefix", "")
        payload_in = [f"{prefix}{t}" if prefix else t for t in texts]
        payload: dict = {"model": self.model, "input": payload_in}
        if bool(settings.embed_openai_input_type):
            # Modelos instruidos/asimétricos (nemotron): el kind del pipeline
            # mapea 1:1 al input_type de la API. OpenAI real lo rechaza — opt-in.
            payload["input_type"] = kind
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                r = httpx.post(
                    f"{self.base_url}/embeddings",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=60.0,
                )
                r.raise_for_status()
                last_error = None
                break
            except httpx.HTTPStatusError as e:
                # El rate-limit transitorio del endpoint no debe perder el
                # vector: reintento con espera breve antes de fallar loud.
                last_error = e
                if e.response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(float(attempt + 1))
                    continue
                raise
        if last_error is not None:
            raise last_error
        data = sorted(r.json().get("data", []), key=lambda d: d.get("index", 0))
        arr = np.asarray([d["embedding"] for d in data], dtype="float32")
        if normalize and arr.size:
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            arr = arr / norms
        return arr.astype("float32")


class EmbeddingProvider:
    """Provider lazy de embeddings — carga en background con reintentos.

    - Campaña de hasta _MAX_LOAD_ATTEMPTS intentos con backoff exponencial.
    - wait_until_ready() falla rápido si la campaña ya está agotada.
    - `finally` garantiza reset de _loading → una nueva campaña puede lanzarse
      en el próximo get_instance() tras agotar la anterior.
    - LRU en-proceso de vectores calculados — sin ella cada encode() se
      re-computaría SIEMPRE. Clave (fingerprint, kind, sha1(texto)) ⇒ el
      cambio de backend/modelo invalida sola. Devuelve COPIAS para que el
      llamador no corrompa la caché mutando el array.
    """

    _model = None
    _loading = False
    _ready = threading.Event()
    _lock = threading.Lock()
    _load_attempts = 0
    load_error: str | None = None

    # LRU de embeddings (vector → copia por llamada)
    _EMBED_CACHE_MAX = 2048
    _cache: "OrderedDict[tuple, Any]" = OrderedDict()
    _cache_lock = threading.Lock()

    @classmethod
    def _cache_get(cls, key: tuple):
        with cls._cache_lock:
            if key in cls._cache:
                cls._cache.move_to_end(key)
                return cls._cache[key]
        return None

    @classmethod
    def _cache_put(cls, key: tuple, arr) -> None:
        with cls._cache_lock:
            cls._cache[key] = arr
            while len(cls._cache) > cls._EMBED_CACHE_MAX:
                cls._cache.popitem(last=False)

    @classmethod
    def _cache_key(cls, text: str, kind: str) -> tuple:
        digest = hashlib.sha1((text or "").encode("utf-8", "ignore")).digest()
        try:
            fp = cls.fingerprint()
        except (
            Exception
        ):  # noqa: BLE001 — fakes/embedders sin fingerprint: la caché jamás rompe el encode
            fp = "no-fp"
        return (fp, kind, digest)

    @classmethod
    def cache_stats(cls) -> dict:
        """Telemetría de la caché (hits/misses/size) para debugging."""
        with cls._cache_lock:
            return {"size": len(cls._cache), "hits": cls._cache_hits, "misses": cls._cache_misses}

    _cache_hits = 0
    _cache_misses = 0

    @classmethod
    def _model_name(cls) -> str:
        from core.config import settings

        return settings.embed_model

    # ── Selección multi-backend ───────────────────────────────────────────

    @classmethod
    def _remote_for(cls, kind: str):
        if kind == "ollama":
            from core.config import settings

            return OllamaEmbedBackend(settings.embed_ollama_model)
        if kind == "openai":
            from core.config import settings

            return OpenAIEmbedBackend(settings.embed_openai_model)
        return None

    @classmethod
    def _resolve_backend(cls) -> "tuple[str, Any]":
        """(kind, backend|None) según EMBED_BACKEND: local|ollama|openai|auto.

        auto: OFFLINE_MODE ⇒ local; online ⇒ openai (si hay key) → ollama
        (si probe ok) → local. El caller hace fallback a local ante fallo.

        OFFLINE_MODE gobierna también los embeddings: con el modo activo
        (forzado por el usuario o sin conexión detectada) ningún byte sale a
        un endpoint remoto — un backend remoto resuelto se sustituye por
        ollama local (nomic-embed-text por defecto). La resolución no está
        cacheada y el fingerprint cambia con ella, así que el stamp
        auto-rebuild purga y reconstruye el índice con la dim nueva al
        togglear. Si ollama no responde, el fallback dim-coherente del
        caller hace el resto.
        """
        from core.config import settings

        chosen = str(settings.embed_backend or "local").lower()
        if chosen in ("local", "st", "sentence-transformers"):
            return ("local", None)
        if chosen != "ollama":
            try:
                from llm.offline import OfflineManager

                offline = OfflineManager().is_offline()
            except Exception:  # noqa: BLE001 — sin manager, tratar como online
                offline = False
            if offline:
                ol = cls._remote_for("ollama")
                if ol is not None and ol.available():
                    return ("ollama", ol)
                if chosen != "openai":
                    # auto (o valor desconocido) en offline sin ollama: local
                    return ("local", None)
                # openai elegido + offline + ollama caído: cae al fallback
                # local del caller tras el fallo del remoto (comportamiento
                # previo), no adivinamos conectividad aquí.
        if chosen in ("ollama", "openai"):
            return (chosen, cls._remote_for(chosen))
        # auto (online)
        oa = cls._remote_for("openai")
        if oa is not None and oa.available():
            return ("openai", oa)
        ol = cls._remote_for("ollama")
        if ol is not None and ol.available():
            return ("ollama", ol)
        return ("local", None)

    @classmethod
    def fingerprint(cls) -> str:
        """Huella del backend EFECTIVO.

        La fórmula local se conserva EXACTA — los stamps ya persistidos no
        deben invalidarse por añadir backends.
        """
        kind, remote = cls._resolve_backend()
        if remote is not None:
            return str(remote.fingerprint)

        from core.config import settings

        raw = (
            f"{settings.embed_backend}:{cls._model_name()}:"
            f"norm={settings.embed_normalize}:{settings.embed_device}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:8]

    @classmethod
    def dimension(cls) -> int:
        """Dimensión esperada: backend remoto manda si seleccionado; luego el
        perfil por familia; si hay modelo local cargado, el real.

        Remoto seleccionado con dim DESCONOCIDA ⇒ 0 (honesto): heredar la dim
        del local construiría un índice que rechaza todos los chunks del
        backend efectivo. Los consumidores tratan 0 con fallback explícito.
        """
        kind, remote = cls._resolve_backend()
        if remote is not None:
            return _dim_of(remote)
        model = cls._model
        if model is not None:
            try:
                d = model.get_sentence_embedding_dimension()
                if d:
                    return int(d)
            except Exception:  # noqa: BLE001
                pass
        _, prof = profile_for(cls._model_name())
        return int(prof.get("dim") or 0)

    @classmethod
    def _local_dimension(cls) -> int:
        """Dimensión EFECTIVA del modelo local (0 si no se puede saber)."""
        model = cls._model
        if model is not None:
            try:
                d = model.get_sentence_embedding_dimension()
                if d:
                    return int(d)
            except Exception:  # noqa: BLE001
                pass
        _, prof = profile_for(cls._model_name())
        return int(prof.get("dim") or 0)

    @classmethod
    def _fallback_local_dim_coherent(cls, kind_sel: str, remote) -> bool:
        """¿Puede el fallback a local tras fallo del remoto?

        Un fallback a un modelo de OTRA dimensión escribiría vectores que el
        índice (construido con la dim del remoto) rechaza uno a uno, o mezclaría
        escalas si casualmente coinciden. Dims distintas ⇒ NO caer (None +
        warning loud); iguales ⇒ caer con warning de calidad.
        """
        try:
            remote_dim = _dim_of(remote)
            local_dim = cls._local_dimension()
        except Exception:  # noqa: BLE001
            return True  # dims desconocidas: no bloquear el fallback legado
        if remote_dim and local_dim and remote_dim != local_dim:
            logger.warning(
                "Fallback a local DESCARTADO: dim local %d ≠ dim del backend %s (%d) "
                "— coherencia del índice por encima de la disponibilidad",
                local_dim,
                kind_sel,
                remote_dim,
            )
            return False
        if remote_dim and local_dim:
            logger.warning(
                "Backend %s falló — fallback a embeddings locales (misma dim %d, "
                "modelo DISTINTO: calidad mezclada en este lote)",
                kind_sel,
                local_dim,
            )
        return True

    @classmethod
    def needs_local_model(cls) -> bool:
        """¿El backend efectivo requiere el modelo local cargado?

        Los caminos que históricamente gateaban en wait_until_ready (search,
        memoria) con un backend REMOTO seleccionado esperarían 60s y fallarían
        aunque el remoto responda — el local es solo fallback. True si no se
        puede determinar (fail-closed al comportamiento histórico).
        """
        try:
            return cls._resolve_backend()[0] == "local"
        except Exception:  # noqa: BLE001 — settings rotos: no bloquear el gate
            return True

    @classmethod
    def get_instance(cls):
        """Retorna el modelo si está listo. Si no, inicia campaña de carga en background.

        Retorna None hasta que el modelo esté completamente cargado.
        """
        if cls._model is not None:
            return cls._model

        with cls._lock:
            if cls._model is not None:
                return cls._model
            if not cls._loading and cls._load_attempts < _MAX_LOAD_ATTEMPTS:
                cls._loading = True
                logger.info(f"Iniciando carga en background: {cls._model_name()}")
                t = threading.Thread(target=cls._load_model, daemon=True)
                t.start()

        return None

    @classmethod
    def encode(cls, text: str, kind: str = "passage"):
        """Wrapper con fallback + convención e5 multi-backend.

        e5 exige prefijos 'query:' / 'passage:' y vectores normalizados L2
        para que las similitudes tengan la escala de los umbrales calibrados.
        Con backend remoto seleccionado lo intenta primero; ante fallo cae a
        la ruta local sin romper al llamador. Caché LRU delante de ambos.
        """
        key = cls._cache_key(text, kind)
        cached = cls._cache_get(key)
        if cached is not None:
            cls._cache_hits += 1
            return cached.copy()
        cls._cache_misses += 1

        kind_sel, remote = cls._resolve_backend()
        arr = None
        if remote is not None:
            try:
                arr = np.asarray(remote.encode_texts([text], kind=kind)[0], dtype="float32")
            except Exception as e:  # noqa: BLE001 — degradación a local
                logger.warning("Backend %s falló (%s)", kind_sel, e)
                if not cls._fallback_local_dim_coherent(kind_sel, remote):
                    return None
        if arr is None:
            arr = cls._encode_single_local(text, kind)
        if arr is not None:
            cls._cache_put(key, arr.copy())
        return arr

    @classmethod
    def _encode_single_local(cls, text: str, kind: str = "passage"):
        from core.config import settings

        model = cls.get_instance()
        if model is None:
            return None
        _, prof = profile_for(cls._model_name())
        prefix = ""
        if kind == "query":
            prefix = prof.get("query_prefix", "")
        else:
            prefix = prof.get("passage_prefix", "")
        emb = model.encode(f"{prefix}{text}" if prefix else text)
        if getattr(settings, "embed_normalize", True) and emb is not None:
            arr = np.asarray(emb, dtype="float32")
            norm = float(np.linalg.norm(arr))
            if norm > 0:
                return (arr / norm).astype("float32")
        return emb

    @classmethod
    def encode_batch(cls, texts: list[str], kind: str = "passage") -> list:
        """Batch-first multi-backend con fallback local.

        Retorna lista alineada con texts (None donde no haya modelo listo).
        Caché LRU per-item — solo los misses llegan a backend/modelo.
        """
        if not texts:
            return []
        keys = [cls._cache_key(t, kind) for t in texts]
        out: list = [None] * len(texts)
        miss_idx: list[int] = []
        for i, k in enumerate(keys):
            cached = cls._cache_get(k)
            if cached is not None:
                cls._cache_hits += 1
                out[i] = cached.copy()
            else:
                cls._cache_misses += 1
                miss_idx.append(i)
        if not miss_idx:
            return out

        miss_texts = [texts[i] for i in miss_idx]
        computed: list = []
        kind_sel, remote = cls._resolve_backend()
        if remote is not None:
            try:
                computed = [
                    np.asarray(row, dtype="float32")
                    for row in remote.encode_texts(miss_texts, kind=kind)
                ]
            except Exception as e:  # noqa: BLE001 — degradación a local
                logger.warning("Backend %s falló (%s)", kind_sel, e)
                if not cls._fallback_local_dim_coherent(kind_sel, remote):
                    return out  # dims incoherentes: None en los misses, caché intacta
                computed = []
        if not computed:
            computed = cls._encode_batch_local(miss_texts, kind)
        for i, arr in zip(miss_idx, computed, strict=False):
            if arr is not None:
                cls._cache_put(keys[i], arr.copy())
                out[i] = arr
        return out

    @classmethod
    def _encode_batch_local(cls, texts: list[str], kind: str = "passage") -> list:
        import numpy as _np  # noqa: F401 — paridad de firma con el test

        from core.config import settings

        model = cls.get_instance()
        if model is None:
            return [None] * len(texts)

        _, prof = profile_for(cls._model_name())
        prefix = prof.get("query_prefix", "") if kind == "query" else prof.get("passage_prefix", "")
        prefixed = [f"{prefix}{x}" if prefix else x for x in texts]
        batch_size = int(getattr(settings, "embed_batch_size", 32) or 32)
        raw = model.encode(prefixed, batch_size=batch_size)
        normalize = bool(getattr(settings, "embed_normalize", True))
        out: list = []
        for e in raw:
            arr = np.asarray(e, dtype="float32")
            if normalize:
                norm = float(np.linalg.norm(arr))
                if norm > 0:
                    arr = (arr / norm).astype("float32")
            out.append(arr)
        return out

    @classmethod
    def wait_until_ready(cls, timeout: float = 60) -> bool:
        """Espera hasta que el modelo esté cargado. Retorna True si listo.

        Si la campaña de carga ya se agotó, falla rápido en vez de
        esperar el timeout completo sin esperanza.
        """
        cls.get_instance()  # starts loading if not active
        if (
            not cls._ready.is_set()
            and cls._load_attempts >= _MAX_LOAD_ATTEMPTS
            and not cls._loading
        ):
            logger.warning("Campaña de carga de embeddings agotada — fail-fast")
            return False
        return cls._ready.wait(timeout)

    @classmethod
    def _load_model(cls):
        backoff = _LOAD_BACKOFF_SECONDS
        try:
            from sentence_transformers import SentenceTransformer

            while cls._load_attempts < _MAX_LOAD_ATTEMPTS:
                try:
                    from core.config import settings

                    logger.info(
                        f"Cargando modelo de embeddings (intento "
                        f"{cls._load_attempts + 1}/{_MAX_LOAD_ATTEMPTS}): {cls._model_name()}"
                    )
                    # EMBED_DEVICE (auto delega en ST)
                    load_kwargs: dict = {}
                    device = str(getattr(settings, "embed_device", "auto") or "auto")
                    if device != "auto":
                        load_kwargs["device"] = device
                    cls._model = SentenceTransformer(cls._model_name(), **load_kwargs)
                    logger.info("Modelo de embeddings cargado correctamente.")
                    cls._ready.set()
                    return
                except Exception as e:
                    cls._load_attempts += 1
                    cls.load_error = str(e)
                    logger.error(
                        "Error cargando embeddings (intento %d/%d): %s",
                        cls._load_attempts,
                        _MAX_LOAD_ATTEMPTS,
                        e,
                    )
                    if cls._load_attempts < _MAX_LOAD_ATTEMPTS:
                        time.sleep(backoff)
                        backoff *= 2
        except ImportError as e:
            cls.load_error = f"sentence-transformers no instalado: {e}"
            logger.error(cls.load_error)
        finally:
            # permite una nueva campaña en el próximo get_instance()
            cls._loading = False


def preload_if_configured() -> None:
    """Precarga opcional del modelo según EMBED_PRELOAD (no fatal).

    Vive aquí —NO en core.bootstrap— porque ese módulo ejecuta load_dotenv()
    al importarse y contamina os.environ en contexto de tests.
    """
    from core.config import settings as _settings

    if bool(getattr(_settings, "embed_preload", False)):
        try:
            EmbeddingProvider.get_instance()
            logger.info("EMBED_PRELOAD=true — campaña de carga de embeddings disparada")
        except Exception:
            logger.warning("preload embeddings falló (no fatal)", exc_info=True)
