"""Paquete DSL de workflows — schema, compiler, validator, registry y motor.

El DSL es el contrato declarativo: `version: 1`, steps como unión discriminada
por `kind`, contrato I/O para includes. Todo compila a un IR (grafo de nodos)
que el motor ejecuta. Los orquestadores legacy quedan como referencia
(ver `dev/docinterno/motor-referencia.md`).
"""

from orchestration.dsl.compiler import CompiledWorkflow, compile_workflow
from orchestration.dsl.schema import WorkflowDSL

__all__ = ["WorkflowDSL", "CompiledWorkflow", "compile_workflow"]
