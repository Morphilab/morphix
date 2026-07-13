# Collaborative Workflow

The Collaborative workflow runs a **multi-agent panel debate** where agents with different perspectives discuss your question across multiple rounds. A neutral moderator synthesizes the final consensus. It's designed for design decisions, architecture reviews, and trade-off discussions — tasks where code output is secondary to reasoned analysis.

## How It Works

```
Round 1: Each agent gives initial opinion
Round 2: Agents critique and refine, considering others' views
Round 3: Agents converge toward consensus
Final: Moderator synthesizes the verdict
```

### The Panel

The default panel has two agents:

| Agent | Perspective | Tools |
|-------|-------------|-------|
| **developer** | Implementation-focused: feasibility, complexity, code patterns | file_manager (read), lsp_manager, code_search |
| **analista** | Analysis-focused: risks, trade-offs, best practices | file_manager (read), lsp_manager, code_search, web_search |

You can force a specific agent to **lead** the debate by selecting an agent in the Maestro agent dropdown. The leader's opinion carries extra weight in the final consensus.

### Round Structure

Each round, all panel agents respond **in parallel** with a 120-second timeout. Agents receive:

- **Round 1**: The question plus project context (directory structure, dependency files)
- **Round 2+**: The question, all previous opinions, and instructions to refine
- **Tools**: Agents can use their registered tools (file_manager read, code_search) to inspect the project before responding

Agents are instructed to stay in character and respond in first person. They can maintain their position, refine it, or change their mind if convinced by others' arguments.

### Moderator Synthesis

After all rounds complete, the **moderador** agent receives:

```
You are the moderator of a debate. Summarize the consensus reached by the panel
on the following question:

**Question:** [original question]

**Final opinions of the panel:**

**Developer:** [final opinion]
**Analista:** [final opinion]

Synthesize the final conclusion of the group. Combine the best ideas from each.
If there is disagreement, point it out with diplomacy but tip the balance with
your neutral judgment. Structure your answer as a final verdict.
```

The moderator produces a balanced, structured consensus that combines the best ideas from all panelists.

## Configuration

| Parameter | Default | Source |
|-----------|:---:|--------|
| Rounds | 3 | `collaborative.yaml` → `rounds` |
| Panel agents | developer, analista | `collaborative.yaml` → `panel` |
| Moderator | moderador | `collaborative.yaml` → `moderator` |
| Per-round timeout | 120s | Hardcoded in `CollaborativeOrchestrator.run()` |
| Per-agent tool calls per round | Max 3 | Hardcoded in `_ask_agent()` |
| `max_parallel` | 2 (panel) | `collaborative.yaml` |

## Agent Tool Access in Debate

During the collaborative workflow, agents have limited tool access:

- Their registered tools are filtered against the workflow's `allowed_tools` (none by default in `collaborative.yaml`, but the panel agents' own tool lists are used)
- Each agent can make up to **3 tool calls** per round (file reads, code searches, web searches)
- If an agent requests a tool, the result is fed back into a second LLM call for an informed response
- Tool results include `tool_call_id` matching for strict-mode LLM compatibility

This means agents can actually **read your project files** during the debate, making their opinions grounded in your actual codebase rather than hypotheticals.

## Context Compression

Before each agent call, conversation history is compressed if it exceeds 60% of `max_context_tokens` to keep within LLM context windows while preserving debate coherence.

## Fallback Behavior

If the moderator fails (LLM error or exception), a fallback consensus is produced by concatenating all final opinions prefixed with:

```
The panel debated but a formal consensus could not be reached.
Below is a summary of the opinions:
```

## When to Use

**Good fits:**
- "Should we use PostgreSQL or MongoDB for this project?"
- "Review the architecture of this module and suggest improvements"
- "Compare FastAPI vs Django for our use case"
- "Evaluate the security of this authentication flow"
- "Should we refactor this monolith into microservices?"

**Not ideal for:**
- Writing code (use Development or Coordinated)
- Building features with tests (use TDD)
- Simple factual questions (use Chat mode)
- Tasks that require file creation (debates are analysis-only)

## Example Prompt

```
We're building an e-commerce platform. Compare these two approaches
for the product catalog:

Option A: PostgreSQL with JSONB columns for flexible product attributes
Option B: MongoDB with a document-per-product model

Consider: query performance, schema flexibility, data integrity,
developer experience, and operational complexity.

Our current stack: Python 3.12, FastAPI, running on AWS with Docker.
We expect 10k products and 50k daily queries.
```

**Expected flow:**

- **Round 1**: developer argues for Option A (better SQL integration, transactions); analista argues for Option B (flexibility for varied product attributes)
- **Round 2**: developer acknowledges MongoDB's schema flexibility for the product catalog use case; analista concedes that transactional integrity matters for order processing
- **Round 3**: Both converge on a hybrid: PostgreSQL for orders/users with JSONB for product attributes, avoiding MongoDB operational overhead
- **Moderator final**: Synthesizes the consensus, notes the remaining trade-offs (JSONB query perf vs MongoDB's native document queries), and delivers a structured verdict with rationale

!!! tip "Project context"
    If you select a project before starting a collaborative workflow, the agents will receive the project's directory structure and dependency files (`requirements.txt`, `pyproject.toml`, or `package.json`) as context. This grounds the debate in your actual tech stack.
