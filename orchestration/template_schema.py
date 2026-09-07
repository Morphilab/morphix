"""Configs de plantilla compartidos legacy→DSL.

Desde el retiro del legacy este módulo solo conserva los tres
configs que el schema DSL reutiliza (`orchestration.dsl.schema`):
`AgentsConfig`, `ToolsConfig`, `ProjectConfig` — misma semántica
deny-by-default. `WorkflowTemplate`/`WorkflowPolicy`/`StageSpec`/`PhaseSpec`
fueron eliminados junto con las rutas legacy (ver git history).
"""

from pydantic import BaseModel, ConfigDict, Field


class AgentsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: list[str] = Field(default_factory=list)
    default_simple: str | None = None


class ToolsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # None = deny-by-default (el runtime lo traduce a []).
    allowed: list[str] | None = None


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool = False
    root: str | None = None
