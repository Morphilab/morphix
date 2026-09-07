# core/output_bounds.py — bounding de salida de tools (byte-exacto)
"""Semántica de bounding:

- Corte SIEMPRE en frontera UTF-8, nunca mojibake.
- ``omitted_bytes`` EXACTO: lo que el presupuesto dejó fuera.
- ``truncated`` significa SOLO 'el retainer omitió por presupuesto',
  nunca 'upstream incompleto'.
- ``tail_bytes`` opcional conserva el final (head+tail) para errores.
"""

from dataclasses import dataclass

TOOL_OUTPUT_MAX_BYTES = 50_000

_MARKER_ES = "\n…[Omitidos {n} bytes por presupuesto de salida]"


@dataclass(frozen=True)
class BoundResult:
    text: str
    truncated: bool
    omitted_bytes: int

    def marker_bytes(self) -> int:
        """Bytes del marcador incluidos en text (para cálculo inverso en tests)."""
        return len(_MARKER_ES.format(n=self.omitted_bytes).encode("utf-8"))


def _cut_at_boundary(data: bytes, limit: int) -> bytes:
    if len(data) <= limit:
        return data
    cut = data[:limit]
    while cut and (cut[-1] & 0b1100_0000) == 0b1000_0000:
        cut = cut[:-1]  # retrocede sobre bytes de continuación UTF-8
    return cut


def bound_output(
    text: str,
    max_bytes: int = 50_000,
    *,
    tail_bytes: int = 0,
) -> BoundResult:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return BoundResult(text=text, truncated=False, omitted_bytes=0)

    head = _cut_at_boundary(raw, max(0, max_bytes - tail_bytes))
    tail = b""
    if tail_bytes > 0:
        tail_raw = raw[-tail_bytes:] if tail_bytes < len(raw) else raw
        # recorta a la frontera por la IZQUIERDA (descarta continuation bytes)
        i = 0
        while i < len(tail_raw) and (tail_raw[i] & 0b1100_0000) == 0b1000_0000:
            i += 1
        tail = tail_raw[i:]

    omitted = len(raw) - len(head) - len(tail)

    # el propio marcador consume presupuesto: ajústalo iterativamente
    marker_len = len(_MARKER_ES.format(n=omitted).encode("utf-8"))
    while len(head) + marker_len + len(tail) > max_bytes and head:
        head = _cut_at_boundary(head, max(0, len(head) - marker_len))
        omitted = len(raw) - len(head) - len(tail)
        marker_len = len(_MARKER_ES.format(n=omitted).encode("utf-8"))

    parts = [head]
    if tail:
        parts.append("\n…[snip]…\n".encode())
    parts.append(tail)
    body_bytes = b"".join(parts)

    # Invariante absoluto: text nunca excede max_bytes. Si ni el marcador
    # corto cabe, se sacrifica el marcador (el conteo queda en omitted_bytes).
    marker = _MARKER_ES.format(n=omitted).encode("utf-8")
    if len(body_bytes) + len(marker) > max_bytes:
        short = f"\n[+{omitted}B]".encode()
        if len(body_bytes) + len(short) <= max_bytes:
            marker = short
        else:
            over = len(body_bytes) + len(marker) - max_bytes
            body_bytes = _cut_at_boundary(body_bytes, max(0, len(body_bytes) - over))
            omitted += over
            marker = b""
        omitted += len(marker) and 0  # el conteo ya refleja solo contenido

    body = body_bytes.decode("utf-8", errors="ignore")

    return BoundResult(
        text=body + marker.decode("utf-8"),
        truncated=True,
        omitted_bytes=omitted,
    )
