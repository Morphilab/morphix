# Workflows Overview

Morphix provides four workflow types, each designed for a different class of software engineering tasks. Workflows determine how Morphix decomposes your request, which agents are involved, how they coordinate, and how results are synthesized.

## Comparison Table

| Workflow | Type | Best for | Agents involved | Key feature |
|----------|------|----------|-----------------|-------------|
| Development | `development` | Software engineering tasks | developer, analista | Full orchestration: decompose → route → execute → supervise → aggregate |
| Coordinated | `coordinated` | Complex multi-step tasks | developer, analista, architect, moderador | DAG parallel execution + shared blackboard |
| Collaborative | `collaborative` | Design decisions, architecture debates | developer, analista, moderador | 3-round multi-agent debate with moderator consensus |
| TDD | `tdd` | Test-driven feature development | developer | Automatic test-red-green-refactor loop (5 iterations max) |

## How Workflow Selection Works

When you submit a task in Orchestrate mode, Morphix follows this dispatch order:

1. **Direct tool command** — If your message matches the `tool_name: action, key=val` format and the tool exists in the registry, it executes immediately (fast path).
2. **Active workflow** — If you've selected a specific workflow from the Dashboard, that template is used.
3. **Default routing** — Otherwise, the `TaskAnalyzer` inspects your query and decides between a simple conversation (single-agent) or full orchestration.

!!! note "Active workflow"
    The active workflow is the one you selected from the Dashboard cards. If unset, the `development` template is used as the default. TDD requires explicitly clicking the TDD workflow card.

## When to Use Each Workflow

### Development

Use for day-to-day software engineering: building features, fixing bugs, refactoring code, adding tests, creating files. This is the **general-purpose** workflow and the default for any coding task.

**Choose Development when:**
- You want Morphix to analyze, decompose, and execute a coding task end-to-end
- You have a specific implementation request ("create a REST API", "add pagination to the list endpoint")
- You want Safety Net protection (analysis agents never fabricate files, supervisor reviews agent assignments)
- Your task fits in 3–5 subtasks and doesn't need cross-phase coordination

### Coordinated

Use for complex multi-step tasks that benefit from parallel execution and phased coordination. Agents share context via a blackboard, enabling DAG-based parallelism.

**Choose Coordinated when:**
- Your task naturally splits into independent phases (design → implement → verify)
- You want up to 4 subtasks running in parallel
- You need cross-phase context sharing between agents
- You have a large task that justifies structured decomposition

### Collaborative

Use for design discussions, architecture debates, and trade-off analysis. A panel of agents debates the question across 3 rounds, with a moderator synthesizing the final consensus.

**Choose Collaborative when:**
- You want multiple perspectives on a design decision
- You're evaluating trade-offs between approaches
- You need a reasoned consensus rather than code output
- You want to pressure-test an idea before implementing it

### TDD

Use for building features using test-driven development. Morphix writes tests first, then implementation, and iterates until all tests pass.

**Choose TDD when:**
- You're building a new feature from scratch
- You want guaranteed test coverage
- You prefer the red-green-refactor cycle
- Your project already uses pytest (or you want Morphix to set it up)

## How to Select a Workflow

1. Open the **Dashboard** tab
2. In the right panel, find the **Workflow Cards** section
3. Click any workflow card to activate it
4. Morphix automatically switches to the **Maestro** tab in Orchestrate mode
5. Type your task and press **Ctrl+Enter** to start

You can change the active workflow at any time by clicking a different workflow card.

## Quick Reference

| Workflow | max_parallel | Timeout per subtask | Retries | Project required |
|----------|:---:|:---:|:---:|:---:|
| Development | 1 (sequential) | — | No | Optional |
| Coordinated | 4 | 180s | Yes (max 2) | Optional |
| Collaborative | 2 (panel) | 120s per round | No | Optional |
| TDD | 1 | 300s per iteration | No | Optional |
