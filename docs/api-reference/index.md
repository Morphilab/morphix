# API Reference

Auto-generated from Python docstrings using [mkdocstrings](https://mkdocstrings.github.io/).

Each page documents a layer's public API: classes, functions, methods, and their signatures.

## Layers

| Layer | Modules documented | Description |
|-------|--------------------|-------------|
| [Core](core.md) | 32 | Database, config, memory, security, MCP, paths, hooks |
| [LLM](llm.md) | 5 | AI controller, provider, parser, prompts, offline |
| [Agents](agents.md) | 6 | Registry, loader, profiles, execution, service, audit |
| [Tools](tools.md) | 16 | Specs, registry, orchestrator, wrapper, loader + implementation modules (file, bash, git, test, lsp, code exec, diff, search, PDF, delegation, clarification) |
| [Orchestration](orchestration.md) | 27 | DSL engine (schema, compiler, validator, engine), agent loop, decomposer, bots dispatch, orchestrator |
| [Desktop](desktop.md) | 25 | GUI tabs, services, widgets |

> **Modules documented** counts the mkdocstrings entries of each page (`grep -c '^::: ' docs/api-reference/<layer>.md`), measured 2026-11-13. The reference is **curated by policy** — only public surface is documented, not every module. Known intentional exclusions: `llm.tool_calls`, several `tools/*.py` implementation modules (file_viewer, goal_todo, memory_inspector, project_docs, skill_loader, vision_analyze), most `core/bots_*` and `desktop/services/*`, `core.sandbox.runner`, the operational security modules (`core.security.*`,
>   `core.hooks.distillation_guard` — summarized at a high level in
>   `docs/architecture/security-model.md`), and the internal modules (`core/skills_curator`, `core/session_search`, `core/checkpoints`, `core/environments`, `core/mcp/trust`, `research/*` — none of them part of the stable public API; see `docs/architecture/layers/research-layer.md` for the research layer overview).
