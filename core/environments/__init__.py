# core/environments/ — matriz pluggable de backends de ejecución
"""Backends de ejecución: tabla ``BACKENDS`` + :func:`create_environment`.

Contrato: alta por decorador, resolución por tabla, JAMÁS ``elif`` sobre
el nombre (regla de forma del repo). Local y docker de fábrica;
ssh/modal/daytona como proveedores futuros — se dan de alta igual.
"""

from core.environments.base import (
    BACKENDS,
    BaseEnvironment,
    ExecResult,
    available_backends,
    create_environment,
    register_backend,
    terminate_group_graceful,
)
from core.environments.docker import DockerEnvironment
from core.environments.local import LocalEnvironment

__all__ = [
    "BACKENDS",
    "BaseEnvironment",
    "ExecResult",
    "DockerEnvironment",
    "LocalEnvironment",
    "available_backends",
    "create_environment",
    "register_backend",
    "terminate_group_graceful",
]
