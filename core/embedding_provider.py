# core/embedding_provider.py
import logging
import threading

logger = logging.getLogger(__name__)


class EmbeddingProvider:
    """Provider lazy de embeddings — carga en background sin bloquear arranque."""

    _model_name = "intfloat/multilingual-e5-large"
    _model = None
    _loading = False
    _ready = threading.Event()
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls):
        """Retorna el modelo si está listo. Si no, inicia carga en background.
        Retorna None hasta que el modelo esté completamente cargado.
        """
        if cls._model is not None:
            return cls._model

        with cls._lock:
            if cls._model is not None:
                return cls._model
            if not cls._loading:
                cls._loading = True
                logger.info(f"Iniciando carga en background: {cls._model_name}")
                t = threading.Thread(target=cls._load_model, daemon=True)
                t.start()

        return None

    @classmethod
    def encode(cls, text: str):
        """Wrapper con fallback: si el modelo no está listo, retorna None."""
        model = cls.get_instance()
        if model is None:
            return None
        return model.encode(text)

    @classmethod
    def wait_until_ready(cls, timeout: float = 60) -> bool:
        """Espera hasta que el modelo esté cargado. Retorna True si listo."""
        cls.get_instance()  # starts loading if not active
        return cls._ready.wait(timeout)

    @classmethod
    def _load_model(cls):
        try:
            from sentence_transformers import SentenceTransformer

            logger.info(f"Cargando modelo de embeddings: {cls._model_name}")
            cls._model = SentenceTransformer(cls._model_name)
            logger.info("Modelo de embeddings cargado correctamente.")
            cls._ready.set()
        except Exception as e:
            logger.error(f"Error cargando modelo embeddings: {e}")
