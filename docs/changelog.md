# Changelog

All notable changes to Morphix across 26 sprints of development.

---

## Sprint 26b: pending-items-cleanup (2026-06)

- **Skills/kits deployment**: `tools/skills/` (8 YAML) + `tools/kits/` (5 YAML) wired into the tool orchestrator.
- **files_written indicator**: enriched `emit_stats` payload + dashboard wiring; `_on_stats` tolerates `files_written` as `int` or `list`.
- **Safety Net respects agent type**: analysis agents (analista) never fabricate files.
- **HTML export**: `html` format in `ConversationRepository.export()` with pygments highlighting (lazy import + fallback).
- **Smart decomposition**: context-aware prompt with real project detection.
- **max_agent_iterations configurable**: via `.env`, template, and `core/config.py` (default **8**).
- **Maintenance**: batch-rebuild FAISS + progress-bar visibility + cancelled-error handler; mypy `progress_callback` annotation; missing `asyncio` import in `maestro_tab.py`; `workspaces/main` ↔ `templates/` sync.

---

## Sprint 26: remaining-fixes-polish (2026-06)

- **Export fixes**: `project_path` scoped to current project (no cross-project contamination); dev agent messages included in exports.
- **Agent role regression**: keep `agent`-role messages out of LLM history (0 `unknown variant agent`).
- **tool_call_id filtering**: drop tool messages missing `tool_call_id` before the LLM call (0 `missing field tool_call_id`).
- **lsp_manager NoneType**: `issue.get("fix") or {}` guard.
- **GUI**: status log vs diagram split (two `QTextBrowser` in a `QSplitter`).

---

## Sprint 25: blackboard-multi-phase (2026-05-28)

- **Phase-aware blackboard**: `SharedBlackboard` redesigned with phase namespaces (`write/read/read_phase/list_phases`), `snapshot/restore()` for pause/resume, `sync_to_db/from_db()` for PostgreSQL persistence (`BlackboardEntry` model), `get_cross_phase_context()` for injecting prior-phase context into current-phase agents. 20 tests.
- **Phase-aware decomposition**: new `decompose_task_with_phases()` that groups subtasks into logical phases (design, implement, test, verify). New prompt `DECOMPOSE_TASK_WITH_PHASES_PROMPT`. Rate limiter awareness integrated (remaining < 5 → max 2 phases). Fallback to single-phase if LLM produces no phases. 4 tests.
- **Coordinated multi-phase execution**: `_run_coordinated()` tests `decompose_task_with_phases()` first. If multiple phases, execute phase-by-phase with blackboard and persist after each phase. If not, use original DAG (backward compatible).
- **Development workflow blackboard**: `_run_full_orchestration()` creates `SharedBlackboard` assigned to `WorkflowContext.blackboard`. Subtasks write results to blackboard and receive context from prior subtasks via `execute_subtask_safe()` → `extra_context`.
- **Pause/resume blackboard persistence**: pause saves `blackboard_snapshot` in `paused_data`. Resume restores blackboard from `ctx.blackboard.restore()`.
- **Workflow template**: `coordinated.yaml` with `phases_enabled: true`, `max_phases: 4`, `default_phases` with agents per phase.

---

## Sprint 24: stabilization (2026-05-28)

- **diff_editor `content` alias**: added `content` as alias for `diff_content` (DeepSeek sends `content=` instead of `diff_content=`). Same pattern as `path`→`file_path`. 3 tests.
- **auto-commit rate limit validation**: `_generate_commit_message()` detects rate limiter error responses (starts with `❌`, contains "rate limit") and uses fallback `feat: {task}` instead of committing the error. Defense in depth in `git_manager` — rejects messages that start with `❌`. 4 tests.
- **test_runner false negatives**: `_test_runner_tool()` uses `_parse_pytest_counts()` to determine success instead of `returncode == 0`. If pytest ran tests and all passed, success even though returncode != 0. 3 tests.
- **bash_manager path hallucination fast-fail**: blocks hallucinated paths `/root/workspace`, `/root/.openclaw` in `_sanitize_command()` with instructive message. 3 tests.
- **python3 -c improved block message**: blocking message now suggests alternatives (`file_manager`, `bash_manager`, `test_runner`). 1 test.
- **orchestrator result-based fast-fail**: added fast-fail for "file not found" in the result-based retry path (Path B). 2 tests.
- **rate limiter awareness**: `RateLimiter.remaining()` exposes available slots. `decompose_task()` limits to 2 subtasks when `remaining < 10`. 3 tests.
- **Export HTML**: new `html` format in `ConversationRepository.export()`. Syntax highlighting with pygments, CSS inline, self-contained HTML.

---

## Sprint 23: dev-dashboard (2026-05-27)

- **QProgressBar**: added to right panel of stats, shows completed/total.
- **QListWidget**: subtask list in left panel with status icons (✅🔵❌⏳).
- **`_build_subtask_list()`**: helper that generates `{name, status}` from DAG results.
- **emit_stats**: payloads enriched with `subtask_list` in decompose, per-subtask, and final.

---

## Sprint 22: conversation-continuity (2026-05-27)

- **load_conversation**: now includes agent/tool messages in `_history` (previously filtered them out).
- **WorkflowContext.is_follow_up**: new flag, set by GUI when `conversation_id` is present.
- **decompose_task**: injects continuation context — tells LLM that the project already exists.
- **TaskAnalyzer**: receives `is_follow_up`, uses separate cache key, suggests lower complexity.
- **ConversationRepository.save()**: resume path now saves ALL agent/tool, removes fragile side-channel.
- **Tests**: 6 new tests for decompose follow-up, save resume, context flag.

---

## Sprint 21: clarification-requests (2026-05-27)

- **New tool**: `tools/ask_clarification.py` — the agent can ask the user mid-workflow.
- **Agent loop**: intercepts `ask_clarification` before execution, returns pause dict.
- **Subtask executor**: propagates status `clarification_needed` upward without completing the subtask.
- **Orchestrator**: `_save_paused_session()` + `resume_workflow()` with full state restoration.
- **WorkflowContext**: field `last_clarification` to pass the question to GUI.
- **Model**: `PausedSession` table for cross-session pause persistence.
- **GUI**: detects `[PAUSED:clarification_needed]`, shows question, redirects input to resume.
- **Tests**: 8 new tests (tool registration, loop interception, context, model).

---

## Sprint 20: final-polish (2026-05-27)

- **Export improved**: strip watermarks from exported content, includes disk files at the end of export.
- **diff_editor path alias**: accepts `path=` as alias for `file_path=` for LLM compatibility.
- **Watermark skip flag**: `skip_watermark=True` in `get_safe_response()` and `add_watermark()` for exports.
- **Development decomposer**: asks for 3-5 granular subtasks instead of 1-4 monolithic.
- **SAFE_MODULES**: `ast` and `io` added to sandbox.
- **AGENTS.md**: sprint history cleanup, summarized to one paragraph.
- **CHANGELOG.md**: created with full history of 20 sprints.

### Post-deploy validation (May 27, conv 5-6)

- **2 conversations, workflow development**: web_scraper + backup_automator
- **0 crashes, 0 ERROR logs, 0 circuit breaker trips**
- **bash_manager failures**: 0 real (the 4 prior were prompt context, not executions)
- **python -c blocked**: 0 | **python: not found**: 0
- **code_exec/sqlite3 errors**: 0
- **Safety Net**: 7/8 (87.5%) — 1 failure on non-.py files (known limitation)
- **Watermarks in exports**: 0 — fix confirmed
- **Export with disk files**: present in both exports
- **Decomposer**: 3 and 5 subtasks — new prompt working
- **Supervisor**: 0 analyst at level 1
- **Code generated**: web_scraper (52 lines, OK) + backup_automator (260 lines, 7 files, OK)
- **Graceful shutdown**: confirmed

---

## Sprint 19: safety-net-reliability (2026-05-27)

- **bash_manager wrapper fast-fail**: `tools/wrapper.py` checks empty `command` before calling the tool, without 3 retries.
- **Supervisor analyst preservation**: the supervisor no longer forces `developer` instead of `analyst` for verification.
- **Safety Net WARNING logs**: `DEBUG → WARNING` on JSON failures and exceptions, better visibility.
- **SAFE_MODULES `sqlite3`**: added to `restricted_executor.py`.

---

## Sprint 18: export-and-skill-fixes (2026-05-26)

- **Aggregator**: reads real files from disk instead of truncated DB content.
- **subtask.py**: removes truncation `content[:200]` — full code reaches the aggregator.
- **lsp_manager**: `issue.get("location") or {}` handles keys with `null` value in JSON from ruff.

---

## Sprint 17: tool-kits (2026-05-26)

- **Tool kits**: `tools/kits/` — 5 YAML with multi-tool workflows (code_quality, debug_cycle, project_setup, refactoring, tdd_workflow).
- **python3 auto-rewrite**: `bash_manager` replaces `python` → `python3` transparently. Eliminates 6-9 `python: not found` errors per session.

---

## Sprint 16: skills-and-templates (2026-05-26)

- **Tool skills**: `tools/skills/` — 8 YAML teaching agents when/how to use each tool.
- **model_override**: agents can override temperature via `model_override` in template YAML.
- **Full templates**: `_FULL_TEMPLATE.yaml` for workflows and agents with all documented fields.
- **Decomposer prompt**: analysis/exploration prohibited as first subtask.
- **Stall detector**: `repeat_tracker` detects repetitive non-modifying calls as stall.

---

## Sprint 15: real-world-validation (2026-05-26)

- **SAFE_BUILTINS**: `repr`, `type`, `isinstance` added to sandbox.
- **lsp_manager location=null fix**: `issue.get("location") or {}`.
- **6 real conversations**: 6/6 successful, 0 crashes, 0 circuit breaker trips.
- **Code quality**: 7 correct and functional files.

---

## Sprint 14: memory-manager-tests (2026-05-25)

- **MemoryManager**: 36 tests — protected keys, read, write, prune, rebuild, profiles.

---

## Sprint 13: health-check (2026-05-25)

- **Health check CLI**: `core/health.py` — 5 probes (DB, LLM, Redis, Filesystem, Workspace). 11 tests.

---

## Sprint 12: core-coverage (2026-05-25)

- **6 core files**: `utils`, `workflow_state`, `mcp/adapter`, `models`, `mcp/config`, `frustration_detector`. 47 tests.

---

## Sprint 11: fix-hanging-tests (2026-05-25)

- **test_agent_loop.py**: fix for XLMRoberta model loading. Mocked `memory_manager` and `CodebaseIndexer`. 5 tests in 2.2s.

---

## Sprint 10: mypy-desktop (2026-05-25)

- **Mypy 0 exclusions**: `desktop/` (3,404 lines) passes mypy with 0 errors. Last `ignore_errors` removed.

---

## Sprint 9: massive-coverage (2026-05-25)

- **10 files**: `lru_cache`, `registry`, `token_counter`, `rate_limiter`, `audit`, `parser`, `offline`, `prompts`, `change_tracker`, `specs`. 76 tests.

---

## Sprint 8: production-hardening (2026-05-25)

- **Signal handling**: SIGTERM/SIGINT → graceful shutdown (stop daemons, close loop).
- **Config validation**: `validate_config()` — DATABASE_URL, API keys, ENCRYPTION_KEY.
- **Graceful shutdown**: `stop_daemons()` before task cancellation.

---

## Sprint 7: agent-experience (2026-05-24)

- **System prompt**: sandbox code_exec rules, command requirement in bash_manager.
- **Developer profile**: CODE_EXEC, BASH_MANAGER, SHARED CONTEXT sections.
- **Coordinated blackboard**: instructive header on shared context.
- **bash_manager spec**: emphasis "OBLIGATORIO" on command parameter.

---

## Sprint 6: test-coverage (2026-05-24)

- **circuit_breaker.py**: 98% → 100%.
- **metrics.py**: 73% → 100%.
- **path_resolver.py**: 61% → 100%.
- **feature_flags.py**: 53% → 100%.

---

## Sprint 5: orchestration-cleanup-pt2 (2026-05-24)

- **loop.py dedup**: `_execute_tool_calls_and_check_stall()` eliminates ~55 duplicated lines.
- **Circuit breaker in call_stream**: streaming calls trip the breaker.
- **emit_stats in agent loop**: visible to UI, emits start/iteration/end.
- **Refactoring template**: `templates/workflows/refactoring.yaml`.

---

## Sprint 4: workflow-health (2026-05-23)

- **WorkflowRunner**: class with `with_timeout()`, `safe_call()`, `check_cancelled()`, `phase_stats()`.
- **Cancellation**: `WorkflowContext.cancelled` + `Session.cancel()`/`is_cancelled`.
- **Timeouts**: collaborative (120s/round), coordinated (180s/subtask), TDD (300s/iteration).
- **Deduplication**: `_collect_files_written()`, `tool_matches_allowlist`, unified compression threshold.

---

## Sprint 3: lsp-fix (2026-05-23)

- **lsp_manager ruff_check NoneType**: guard for `[null]` and non-dict entries. 5 new tests.
- **bash_manager python3 -c**: documented — restriction maintained for security.

---

## Sprint 2: auditoria-mayo-2026 (2026-05-23)

- **28 commits**: moderator tool filtering, E2E tests, AgentLoopConfig, `_dispatch_route`, per-tool metrics.
- **Mypy to CI**: pre-commit + CI with baseline exclusion. Circuit breaker. Coordinated DAG tests.
- **kairos elimination**: 27 `kairos.get()` migrated to `settings.XXX`.
- **Stall detection 3 fixes**: files_written guard, tool_success as progress, aggregator with file_manager.
- **bash_manager guard**: `command=""` prevents crashes from DeepSeek omissions.
- **Export dedup**: stable filenames by conversation ID.

---

## Sprint 1: restructure (2026-05-22)

- **Project restructure**: 33 files moved to `llm/`, `agents/`, `tools/`, `orchestration/`. `features/` eliminated.
- **Code deduplication**: `_tool_calls_from_response` in `llm/parser.py`, `_tool_matches_allowlist` in `tools/specs.py`.
- **8 commits, 0 regressions, 338 tests pass**.

**Total: 26 sprints (latest 26b), 680 tests, 0 mypy errors, 0 rollbacks.**
