# tests/test_core_database_schema_validation.py
"""create_tables_in_schema debe validar el nombre (defensa en profundidad)."""

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_create_tables_in_schema_rejects_invalid_name():
    from core.database import create_tables_in_schema

    with pytest.raises(ValueError):
        with patch(
            "core.database._get_async_engine", side_effect=AssertionError("no debe crear engine")
        ):
            await create_tables_in_schema('mal"nombre; DROP SCHEMA public')
