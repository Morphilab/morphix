# research/ — harness de evaluación: generación de trayectorias en lote
"""Paquete de investigación: batch runner + compresor de trayectorias.

Runner de evaluación adaptado al stack Morphix: asyncio en vez de procesos,
`execute_agent_loop`
como ejecutor por defecto (inyectable para tests), artefactos JSONL+JSON en
disco y reanudación exactly-once vía checkpoint + reconciliación.

`mini_swe.py` queda pendiente de la matriz de backends: sin `core/environments/`
no hay dónde alojar el runner SWE. Cuando esa capa aterrice, se añade sobre la
misma interfaz `executor` de :mod:`research.batch_runner`.
"""
