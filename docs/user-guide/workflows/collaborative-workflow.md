# Collaborative Workflow

The Collaborative preset runs a **multi-agent panel debate**: agents with different perspectives discuss your question across rounds, and a neutral moderator synthesizes the final consensus. It is designed for design decisions, architecture reviews, and trade-off discussions — tasks where reasoned analysis matters more than code output.

**How it runs (DSL preset `collaborative`):**

1. **Rounds (3)** — a deterministic loop where the `developer` gives an opinion and the `analista` replies; each panelist sees the previous output (`$last_output`) before responding.
2. **Synthesis** — the `moderador` agent receives all opinions and produces the final consensus.
3. Toolset is restricted to read-oriented tools: `file_manager`, `code_search`, `web_search`, `web_fetch`, `lsp_manager`.

Collaborative is the **only preset that does not require a project** — the debate can be purely based on the question. If a project is selected, agents can read it to ground their arguments.

For the shared mechanics (pauses/clarifications, resume, running from GUI or CLI, the other 8 presets), see [Workflows Overview](../workflows.md).
