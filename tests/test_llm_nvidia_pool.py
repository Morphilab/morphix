# tests/test_llm_nvidia_pool.py
"""Pool de claves NVIDIA y su integración en el proveedor.

El pool se declara con ``NVIDIA_API_KEY_<n>`` emparejada con
``NVIDIA_MODEL_<n>``: cada clave está ligada a un modelo. La resolución
distingue emparejamiento exacto, mono-modelo, pool ambiguo y ausencia total —
cada caso cae donde debe (clave correcta, primera con warning, u Ollama).
"""


from llm.provider import LLMProvider, _nvidia_pool_disponible, nvidia_key_for_model

GLM = "z-ai/glm-5.3-flash"


def _limpia_pool(monkeypatch):
    """Pool vacío: el resolutor lee os.environ Y el .env real (fuente dual),
    así que el control de tests va por el seam nvidia_pool_entries."""
    import core.config

    monkeypatch.setattr(core.config, "nvidia_pool_entries", lambda: {})
    monkeypatch.setattr("core.config.settings.nvidia_api_key", "", raising=False)


def _declara_pares(monkeypatch, pares: list[tuple[str, str]]):
    """Pares (modelo, clave) EN ORDEN: índice determinista para los asserts."""
    _limpia_pool(monkeypatch)
    entradas: dict[str, str] = {}
    for i, (modelo, clave) in enumerate(pares, start=1):
        entradas[f"NVIDIA_API_KEY_{i}"] = clave
        entradas[f"NVIDIA_MODEL_{i}"] = modelo
    import core.config

    monkeypatch.setattr(core.config, "nvidia_pool_entries", lambda: entradas)


class TestResolucionDelPool:
    def test_sin_claves_no_hay_proveedor(self, monkeypatch):
        _limpia_pool(monkeypatch)
        assert nvidia_key_for_model(GLM) is None
        assert _nvidia_pool_disponible() is False

    def test_clave_base_como_conveniencia_mono_modelo(self, monkeypatch):
        # el fallback de "ninguna numerada" es settings.nvidia_api_key (la
        # clave base NO se relee del entorno: Settings se congeló al importar)
        _limpia_pool(monkeypatch)
        monkeypatch.setattr("core.config.settings.nvidia_api_key", "nvapi-base", raising=False)
        assert nvidia_key_for_model(GLM) == "nvapi-base"
        assert nvidia_key_for_model("otro/modelo") == "nvapi-base"

    def test_una_sola_numerada_sin_emparejar_sirve_para_todo(self, monkeypatch):
        _limpia_pool(monkeypatch)
        import core.config

        monkeypatch.setattr(
            core.config, "nvidia_pool_entries", lambda: {"NVIDIA_API_KEY_1": "nvapi-sola"}
        )
        assert nvidia_key_for_model(GLM) == "nvapi-sola"

    def test_emparejamiento_exacto_enruta_por_modelo(self, monkeypatch):
        _declara_pares(monkeypatch, [(GLM, "nvapi-glm"), ("otro/modelo", "nvapi-otro")])
        assert nvidia_key_for_model(GLM) == "nvapi-glm"
        assert nvidia_key_for_model("otro/modelo") == "nvapi-otro"
        # un modelo SIN par en un pool emparejado: no hay clave (no adivina)
        assert nvidia_key_for_model("tercero/modelo") is None

    def test_pool_ambiguo_usa_la_primera_con_warning(self, monkeypatch, caplog):
        _limpia_pool(monkeypatch)
        import core.config

        monkeypatch.setattr(
            core.config,
            "nvidia_pool_entries",
            lambda: {"NVIDIA_API_KEY_2": "nvapi-b", "NVIDIA_API_KEY_1": "nvapi-a"},
        )
        with caplog.at_level("WARNING"):
            assert nvidia_key_for_model(GLM) == "nvapi-a"  # orden por índice
        assert "ambiguo" in caplog.text.lower()


class TestSeleccionDelProveedor:
    """Ningún rol de producto apunta a NVIDIA: estos tests ejercen el
    proveedor con un rol sintético — el pool debe seguir funcionando
    aunque el default de producto viva en otro proveedor."""

    def _rol_nvidia(self, monkeypatch):
        from core.config import settings

        roles = {
            **settings.model_roles,
            "nvidia-test": {"provider": "nvidia", "model": GLM, "temperature": 0.7},
        }
        monkeypatch.setattr(settings, "model_roles", roles)
        return "nvidia-test"

    def test_rol_nvidia_con_clave_crea_cliente_openai(self, monkeypatch):
        _declara_pares(monkeypatch, [(GLM, "nvapi-glm")])
        rol = self._rol_nvidia(monkeypatch)
        cliente, modelo, temp = LLMProvider.get_client(rol)
        assert modelo == GLM
        assert str(cliente.base_url).startswith("https://integrate.api.nvidia.com/v1")

    def test_sin_clave_nvidia_el_rol_cae_a_ollama(self, monkeypatch):
        _limpia_pool(monkeypatch)
        rol = self._rol_nvidia(monkeypatch)
        from core.config import settings

        cliente, modelo, _ = LLMProvider.get_client(rol)
        assert modelo == settings.ollama_model  # fallback de Ollama del settings

    def test_proveedor_efectivo_refleja_el_pool(self, monkeypatch):
        _declara_pares(monkeypatch, [(GLM, "nvapi-glm")])
        rol = self._rol_nvidia(monkeypatch)
        assert LLMProvider._effective_provider("nvidia") == "nvidia"
        assert LLMProvider.get_provider_name(rol) == "nvidia"


class TestFuenteDual:
    """El pool vive en .env Y en os.environ: pydantic-settings no exporta el
    fichero a os.environ, así que una clave solo en .env no sería vista y el
    proveedor caería a Ollama."""

    def test_fichero_env_y_os_environ_mezclados_con_prioridad_del_entorno(
        self, tmp_path, monkeypatch
    ):
        import core.config

        (tmp_path / ".env").write_text(
            "NVIDIA_API_KEY_1=nvapi-del-fichero\n"
            "NVIDIA_MODEL_1=z-ai/glm-5.3-flash\n"
            "NVIDIA_API_KEY_2=nvapi-2-fichero\n",
            encoding="utf-8",
        )
        monkeypatch.setitem(core.config.Settings.model_config, "env_file", str(tmp_path / ".env"))
        monkeypatch.setenv("NVIDIA_API_KEY_1", "nvapi-del-entorno")
        entradas = core.config.nvidia_pool_entries()
        assert entradas["NVIDIA_API_KEY_1"] == "nvapi-del-entorno"  # environ manda
        assert entradas["NVIDIA_API_KEY_2"] == "nvapi-2-fichero"  # fichero leído
        assert entradas["NVIDIA_MODEL_1"] == "z-ai/glm-5.3-flash"

    def test_fichero_env_con_hash_y_comentarios_no_confunde(self, tmp_path, monkeypatch):
        import core.config

        (tmp_path / ".env").write_text(
            "# NVIDIA_API_KEY_9=nvapi-comentada\n"
            "OTRA=no-nvidia\n"
            "NVIDIA_API_BASE=https://ejemplo/v1\n",
            encoding="utf-8",
        )
        monkeypatch.setitem(core.config.Settings.model_config, "env_file", str(tmp_path / ".env"))
        for k in [k for k in __import__("os").environ if k.startswith("NVIDIA_")]:
            monkeypatch.delenv(k, raising=False)
        entradas = core.config.nvidia_pool_entries()
        assert entradas == {"NVIDIA_API_BASE": "https://ejemplo/v1"}
