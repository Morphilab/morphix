# core/agent_profiles.py
"""
Global built-in agent profiles.
Only 'conversacional' is kept as the universal minimum fallback agent.
All other agents are defined per workspace in workspaces/<name>/agents/*.yaml.
Initial templates live in templates/agents/ for new workspaces.
"""

AGENT_PROFILES = [
    {
        "name": "conversacional",
        "system_prompt": (
            "You are Conversational. You are friendly, empathetic, and natural — like "
            "a smart friend who knows the user well. Never do deep analysis, code, or formal writing."
        ),
        "length_guidance": "Brief, natural, conversational (max 2-3 paragraphs).",
        "temperature": 0.4,
        "tools": [],
        "keywords": [
            "hello",
            "how are you",
            "my name",
            "remember",
            "who am i",
            "profile",
            "small talk",
            "help",
            "thanks",
            "what can you do",
        ],
        "priority": 10,
        "model_role": "agent",
        "last_memory_key": None,
    },
]
