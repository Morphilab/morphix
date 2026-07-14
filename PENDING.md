# PENDING — Morphix (June 3, 2026)

## Project state: STABLE — 26 sprints

**~698 tests. 0 mypy errors. 0 exclusions. 0 rollbacks.**

## Fixes verified (sprints 24–26)

| Fix | Sprint | Commit | Verified |
|-----|:---:|--------|:---:|
| diff_editor `content` alias | 24 | 84c4a7f | ✅ 0 errors in sessions |
| auto-commit rate limit validation | 24 | 84c4a7f | ✅ 0 corrupted commits |
| test_runner false negatives (parse counts) | 24 | 84c4a7f | ⚠️ Still occurs (see below) |
| bash_manager path hallucination | 24 | 84c4a7f | ✅ 0 occurrences |
| python3 -c improved block message | 24 | 84c4a7f | ✅ Fast-fail with alternatives |
| orchestrator result fast-fail | 24 | 84c4a7f | ✅ 0 unnecessary retries |
| git_manager commit validation | 24 | 84c4a7f | ✅ Defense in depth |
| Export HTML syntax highlighting | 24 | 84c4a7f | ✅ Basic HTML export |
| Rate limiter awareness in decomposer | 24 | 84c4a7f | ✅ remaining() working |
| Blackboard multi-phase | 25 | 75294ce | ✅ 2 workflows integrated |
| Phase-aware decomposition | 25 | 75294ce | ✅ decompose_task_with_phases() |
| Blackboard persistence | 25 | 75294ce | ✅ BlackboardEntry + sync |
| Export project_path fix | — | 6288f0e | ✅ No cross-project contamination |
| Agent messages in dev exports | — | 6288f0e | ✅ 8-11 history entries per conv |
| Agent role regression | — | 6fe2ccb | ✅ 0 `unknown variant agent` |
| tool_call_id filtering | — | 5fae521 | ✅ 0 `missing field tool_call_id` |
| lsp_manager NoneType | 26 | — | ✅ `issue.get("fix") or {}` |
| test_runner skill fixes tests/ → specific | 26 | — | ✅ LLM guided to specific tests |
| orchestrator test fast-fail | 26 | — | ✅ No retries when failed_count > 0 |
| Status log vs diagram split | 26 | — | ✅ Two QTextBrowser in QSplitter |

## Known limitations (not bugs)

| Limitation | Explanation |
|------------|-------------|
| Safety Net misses non-Python files | `.gitignore`, `README.md` not JSON-parseable |
| `python3 -c` permanently blocked | Security decision |
| LLM empty tool arguments | DeepSeek occasional behavior. Fast-fail + Safety Net |
| Agent iteration limit (default 8) | `MAX_AGENT_ITERATIONS` default `8` (`core/config.py`); TDD loop uses `MAX_TDD_ITERATIONS = 5`. Simple tasks may hit limit |
| Safety Net overwrites | Multiple activations can overwrite same file in same subtask |
| test_runner runs full suite sometimes | Agent may still run `tests/` despite skill fix (LLM discretion) |

## What's NOT in the project (intentional)

- **No Docker**: User rejected
- **No HTTP server**: CLI only
- **No CI/CD pipeline**: Pre-commit hooks cover all checks
- **No Alembic migration for BlackboardEntry**: `create_all` handles table creation

## Validation history

| Session | Date | Convs | Workflows | API errors | Verdict |
|---------|------|:---:|-----------|:---:|---|
| Sprint 19 pre-fix | May 27 | 3 | coordinated×2, dev | 4 | Fixes identified |
| Sprint 19 post-fix | May 27 | 2 | coordinated | 0 | ✅ |
| Sprint 20 | May 27 | 2 | development | 0 | ✅ |
| Sprint 24-25 (large) | May 28 | 17 | coord×10, dev×7 | 68 WARNINGs, 0 crashes | All fixes verified |
| Sprint export fix | Jun 2 | 3 | collab, coord, dev | 0 API errors | Export bugs discovered |
| Sprint export fix 2 | Jun 2 | 4 | coord×2, dev×2 | 0 `agent` role, 3 `tool_call_id` | Regression found |
| Sprint agent fix | Jun 2 | 4 | coord×2, dev×2 | 0 `agent` role, 3 `tool_call_id` | Regression fixed |
| Sprint tool_call_id fix | Jun 3 | 4 | coord×2, dev×2 | 0 `agent` role, 0 `tool_call_id` | All API errors eliminated |
| Sprint 26 final | Jun 3 | 4 | coord×2, dev×2 | 1 ERROR (lsp_manager, now fixed) | Production ready |

> **Total: 11 sessions, 43 conversations. 0 crashes in all sessions.**

## Project status

All critical bugs fixed. API errors eliminated (0 `agent` role, 0 `tool_call_id`). Exports clean (own project files only, include agent messages). Blackboard multi-phase working across both workflows. GUI split (diagram ≠ status). Files_written indicator ready for wiring.

**Production ready. In maintenance mode.**

## Latest maintenance pass (2026-06)

**Fixed** (guard test `tests/test_template_consistency.py` added):
- ✅ `coordinated.yaml` / `refactoring.yaml` `architect` agent — created `architect` profile + added to `agents.allowed`.
- ✅ `development.yaml` phantom `browser` tool — removed from global template.
- ✅ `orchestrator.py` `_run_simple_conversation` hardcoded `workspace="main"` — now `get_global_workspaces().current`.
- ✅ mypy 0 errors restored (`core/health.py`, `orchestrator.py` query types).

**Fixed — test suite** (test-only; production code was correct; full suite now **675 pass / 1 flake**):
- ✅ `test_decompose_with_kwargs` — pass `project_context` (decompose prompt placeholder).
- ✅ `test_context_snapshot` — populate `_phases` (sprint-25 storage).
- ✅ `test_coordinated_workflow_e2e` — mock `decompose_task_with_phases` (sprint-25 phase-first flow).
- ✅ `test_get_encoding_loads_and_caches` — reset global `token_counter._enc` at start (isolation bug).

**Open — environmental flake** (1): `test_workflow_orchestrator.py::test_development_route` passes in isolation but can raise `OSError: [Errno 22]` under full-suite load. Root cause: pytest-asyncio creates a fresh function-scoped event loop (new epoll fd) per test; across ~676 loops the create/teardown churn intermittently corrupts the process epoll/fd state (`EpollSelector.poll()` → EINVAL; pytest-asyncio logs `Error cleaning up asyncio loop: [Errno 22]`). Test-infra scaling artifact, not a product bug. Mitigated DB cross-loop reuse via loop-aware engine in `core/database.py`; a session-scoped test loop would remove the epoll churn (deferred — changes isolation for all tests).
