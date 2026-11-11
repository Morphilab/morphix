# core/errors.py — taxonomía de errores de dominio.
"""Base común de los errores del producto con ``code`` estable.

Agrupación en logs y telemetría por etiqueta (``exc.code``) en vez de por
texto del mensaje. Las clases de dominio heredan de ``MorphixError`` SIN
perder su base histórica (ValueError/KeyError/...) — compatibles con todo
``except`` existente; el guard ``tests/test_error_taxonomy.py`` exige que
ninguna clase ``*Error`` de los paquetes de producto nazca huérfana.
"""


class MorphixError(Exception):
    """Base de los errores de dominio de Morphix."""

    code: str = "morphix"
