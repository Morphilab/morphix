"""Saneamiento básico de rich-text antes de renderizar salida del LLM.

Qt no ejecuta JavaScript en QTextBrowser/QLabel, pero `<script>` y links
`javascript:` provenientes del modelo no deben llegar al widget: son
ruido o phishing potencial. Mantiene el markdown legítimo intacto.
"""

import re

_SCRIPT_BLOCK_RE = re.compile(r"<\s*script\b[^>]*>.*?<\s*/\s*script\s*>", re.IGNORECASE | re.DOTALL)
_JS_SCHEME_RE = re.compile(r"javascript\s*:", re.IGNORECASE)


def sanitize_rich_text(text: str) -> str:
    """Elimina bloques <script> y esquemas javascript: del texto rich-text."""
    text = _SCRIPT_BLOCK_RE.sub("[contenido bloqueado]", text)
    text = _JS_SCHEME_RE.sub("", text)
    return text
