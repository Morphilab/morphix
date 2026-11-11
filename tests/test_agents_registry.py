"""Tests for AgentsRegistry — global, workspace, fallback, clear."""

from agents.registry import AgentsRegistry


class TestAgentsRegistry:
    def test_register_global_agent(self):
        reg = AgentsRegistry()

        @reg.register_global("test_agent", {"key": "val"})
        async def handler(task, history):
            return "done"

        agent = reg.get_agent("test_agent")
        assert agent is not None
        assert reg.get_profile("test_agent") == {"key": "val"}

    def test_register_workspace_agent_has_priority(self):
        reg = AgentsRegistry()

        @reg.register_global("shared", {"source": "global"})
        async def global_handler(task, history):
            return "global"

        async def workspace_handler(task, history):
            return "workspace"

        reg.register_workspace_agent("shared", workspace_handler, {"source": "workspace"})

        agent = reg.get_agent("shared")
        assert agent is workspace_handler
        assert reg.get_profile("shared") == {"source": "workspace"}

    def test_get_agent_global_fallback(self):
        reg = AgentsRegistry()

        @reg.register_global("fallback")
        async def handler(task, history):
            return "ok"

        assert reg.get_agent("fallback") is not None

    def test_get_agent_missing_returns_none(self):
        reg = AgentsRegistry()
        assert reg.get_agent("nonexistent") is None
        assert reg.get_profile("nonexistent") is None

    def test_clear_workspace_preserves_global(self):
        reg = AgentsRegistry()

        @reg.register_global("global_agent")
        async def global_handler(task, history):
            return "global"

        async def ws_handler(task, history):
            return "ws"

        reg.register_workspace_agent("ws_agent", ws_handler)
        reg.clear_workspace_agents()

        assert reg.get_agent("global_agent") is not None
        assert reg.get_agent("ws_agent") is None

    def test_list_agents_merges_both(self):
        reg = AgentsRegistry()

        @reg.register_global("g1")
        async def g1(task, history):
            return "g1"

        async def w1(task, history):
            return "w1"

        reg.register_workspace_agent("w1", w1)
        all_agents = reg.list_agents()
        assert "g1" in all_agents
        assert "w1" in all_agents

    def test_list_global_agents_returns_copy(self):
        reg = AgentsRegistry()

        @reg.register_global("g1")
        async def g1(task, history):
            return "g1"

        globals_copy = reg.list_global_agents()
        assert "g1" in globals_copy
        globals_copy.pop("g1")
        assert "g1" in reg.list_global_agents()

    def test_clear_removes_everything(self):
        reg = AgentsRegistry()

        @reg.register_global("g1")
        async def g1(task, history):
            return "g1"

        async def w1(task, history):
            return "w1"

        reg.register_workspace_agent("w1", w1)
        reg.clear()

        assert reg.get_agent("g1") is None
        assert reg.get_agent("w1") is None
        assert reg.list_agents() == {}
