"""Tests for core/workflow_state.py."""

import core.workflow_state as wf


class TestWorkflowState:
    def test_set_and_get(self):
        wf.set_active_workflow("tdd")
        assert wf.get_active_workflow() == "tdd"

    def test_default_when_not_set(self):
        wf._workflow_map.clear()
        result = wf.get_active_workflow()
        assert result in ("development", "default")

    def test_switch_workspace_preserves(self):
        wf.set_active_workflow("tdd")
        assert wf.get_active_workflow() == "tdd"
        wf.switch_workspace("other")
        assert wf.get_active_workflow() != "tdd"  # other workspace, different state

    def test_different_workspaces_isolated(self):
        wf.switch_workspace("ws_a")
        wf.set_active_workflow("collaborative")
        wf.switch_workspace("ws_b")
        wf.set_active_workflow("coordinated")

        wf.switch_workspace("ws_a")
        assert wf.get_active_workflow() == "collaborative"
        wf.switch_workspace("ws_b")
        assert wf.get_active_workflow() == "coordinated"
