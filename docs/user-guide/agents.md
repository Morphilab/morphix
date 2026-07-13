# Agents

Morphix includes five built-in agents, each with a distinct role, personality, and tool set. Agents are the workers that execute subtasks in workflows — they reason about your request, use tools to interact with your project, and produce structured output.

## Agent Registry

Agents are loaded from YAML templates in `templates/agents/` (copied to `workspaces/<name>/agents/` on first workspace switch). Each agent has a profile with:

- **System prompt**: Instructions, personality, constraints
- **Tools**: Allowed tool list
- **Keywords**: Used for routing (agent selection based on task description)
- **Model role**: Which LLM role to use (`agent`, `reasoning`, `fast`)
- **Temperature**: Response randomness (0.0 = deterministic, 1.0 = creative)
- **Priority**: Routing priority (higher = preferred for ambiguous tasks)

---

## Developer

| Property | Value |
|----------|-------|
| **Type** | `development` (agent) |
| **Model role** | `agent` |
| **Temperature** | 0.2 |
| **Priority** | 70 |
| **Tools** | `file_manager`, `git_manager`, `bash_manager`, `lsp_manager`, `code_exec`, `test_runner`, `diff_editor` |

The **Developer** agent writes, modifies, and tests code. It has the most extensive tool set of any agent — it can read/write files, run shell commands, execute tests, apply diffs, and manage Git.

### Best Use Cases

- Writing new code (functions, classes, modules, APIs)
- Fixing bugs and refactoring
- Running tests and debugging failures
- Installing dependencies via bash
- Creating commits

### Constraints

- Uses **relative paths only** — never absolute paths or `code_projects/` prefix
- `bash_manager` requires the `command` parameter (without it, the tool fast-fails)
- `python3 -c ...` is blocked by security; use `test_runner` instead
- `code_exec` sandbox blocks `os`, `sys`, `subprocess`, `io`, `socket`, `requests`, `pathlib`

### Example Prompt

```
Add pagination to the /api/users endpoint in the Flask app.
The endpoint should accept ?page=1&per_page=20 query parameters.
Return a JSON response with "data", "page", "per_page", and "total" fields.
```

---

## Analista

| Property | Value |
|----------|-------|
| **Type** | `analysis` (reasoning) |
| **Model role** | `reasoning` |
| **Temperature** | 0.2 |
| **Priority** | 55 |
| **Tools** | `file_manager`, `lsp_manager`, `code_search`, `web_search` |

The **Analista** agent analyzes and reviews — it reads code, evaluates architecture, identifies risks, and provides recommendations. It is **read-only**: it can read files, search code, and navigate with LSP, but it **cannot write files or commit**.

### Best Use Cases

- Code review and security analysis
- Architecture evaluation
- Identifying bugs, performance issues, anti-patterns
- Explaining what code does
- Recommending improvements

### Constraints

- No `file_manager.write`, `git_manager.commit`, or `diff_editor`
- Read-only analysis — hands off implementation to the developer agent
- Output is structured analysis, not code

### Example Prompt

```
Review the authentication module in src/auth/ for security issues.
Look for: SQL injection, hardcoded secrets, missing input validation,
insecure password handling, and CSRF vulnerabilities.
```

---

## Moderador

| Property | Value |
|----------|-------|
| **Type** | Agent (no specific type) |
| **Model role** | `reasoning` |
| **Temperature** | 0.4 |
| **Priority** | 1 (lowest — never auto-selected) |
| **Tools** | _none_ |

The **Moderador** agent is the neutral arbiter for the Collaborative workflow. It listens to all panelists' arguments, identifies common ground, guides toward consensus, and delivers the final verdict. It has no tools — its value is in synthesis and balanced judgment.

### Best Use Cases

- Synthesizing a debate into a final consensus
- Weighing conflicting opinions fairly
- Delivering structured, balanced conclusions

### Constraints

- No tools — pure reasoning agent
- Lowest priority — never selected by the AgentRouter for code tasks

### Example Prompt

```
(This agent is automatically invoked by the Collaborative workflow.
You don't call it directly.)
```

---

## Conversacional

| Property | Value |
|----------|-------|
| **Type** | Agent (no specific type) |
| **Model role** | `agent` |
| **Temperature** | 0.4 |
| **Priority** | 10 |
| **Tools** | _none_ |

The **Conversacional** agent is the friendly conversationalist. It handles small talk, greetings, profile questions, and general help. It has no tools — it's a pure chat agent designed for natural conversation.

### Best Use Cases

- Greetings and casual conversation
- "What can you do?" / "Help me get started"
- Profile and preference questions
- Quick factual explanations

### Example Prompt

```
Hi! What can you help me with today?
```

---

## Architect

| Property | Value |
|----------|-------|
| **Type** | `analysis` (reasoning) |
| **Model role** | `reasoning` |
| **Temperature** | 0.2 |
| **Priority** | 58 |
| **Tools** | `file_manager`, `lsp_manager`, `code_search`, `web_search` |

The **Architect** agent designs system structure before implementation. It maps existing code, evaluates patterns and libraries, and produces clear specifications with an ordered implementation plan. Like Analista, it is **read-only** — it designs, but does not write production code.

### Best Use Cases

- Designing system architecture for new features
- Planning module boundaries and interfaces
- Choosing between frameworks/libraries with trade-off analysis
- Creating implementation plans for the developer agent

### Constraints

- No `file_manager.write`, `git_manager`, or `diff_editor`
- Delivers specifications and plans — hands off to developer for implementation

### Example Prompt

```
Design the architecture for a notification system that supports:
- Email, SMS, and push notifications
- User preferences per channel
- Async delivery with retry logic
- Rate limiting per user

Provide component responsibilities, data flow, and an implementation plan.
```

---

## Agent Routing

When a task is decomposed, the `AgentRouter` selects the best agent for each subtask based on:

1. **Keyword matching** — Each agent has a `keywords` list in its profile. The router matches these against the subtask description.
2. **Priority** — Higher-priority agents are preferred when multiple match.

| Agent | Priority | Key keywords |
|-------|:---:|------|
| Developer | 70 | code, implement, create, build, deploy, refactor, fix, test, debug, api, endpoint |
| Architect | 58 | architecture, design, structure, blueprint, plan, components, interfaces, patterns |
| Analista | 55 | analyze, review, evaluate, architecture, risks, security, performance, diagnose |
| Conversacional | 10 | hello, help, thanks, small talk, what can you do |
| Moderador | 1 | consensus, moderate, debate, discuss (only used in Collaborative) |

!!! tip "Force an agent"
    You can override auto-routing by selecting a specific agent in the Maestro agent dropdown. In Orchestrate mode, only agents allowed by the active workflow are shown. In Chat mode, all agents are available.
