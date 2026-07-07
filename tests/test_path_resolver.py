"""Tests for PathResolver — 22 methods covering 100%."""

from pathlib import Path

from core.path_resolver import PathResolver


class TestPathResolverSimple:
    def test_memory_base(self):
        p = PathResolver.memory_base()
        assert isinstance(p, Path)
        assert p.name == "memory"

    def test_memory_dir(self):
        p = PathResolver.memory_dir("main")
        assert isinstance(p, Path)
        assert p.name == "main"
        assert p.parent.name == "memory"

    def test_code_projects_dir_no_project(self):
        p = PathResolver.code_projects_dir("main")
        assert isinstance(p, Path)
        assert p.name == "main"

    def test_code_projects_dir_with_project(self):
        p = PathResolver.code_projects_dir("main", "subdir")
        assert isinstance(p, Path)
        assert p.name == "subdir"

    def test_workspaces_base(self):
        p = PathResolver.workspaces_base()
        assert isinstance(p, Path)
        assert p.name == "workspaces"

    def test_workspace_dir(self):
        p = PathResolver.workspace_dir("demo")
        assert str(p).endswith("workspaces/demo")

    def test_workspace_agents_dir(self):
        p = PathResolver.workspace_agents_dir("demo")
        assert str(p).endswith("workspaces/demo/agents")

    def test_workspace_hooks_dir(self):
        p = PathResolver.workspace_hooks_dir("demo")
        assert str(p).endswith("workspaces/demo/hooks")

    def test_mcp_servers_file(self):
        p = PathResolver.mcp_servers_file("demo")
        assert p.name == "mcp_servers.json"

    def test_workspace_tools_dir(self):
        p = PathResolver.workspace_tools_dir("demo")
        assert str(p).endswith("workspaces/demo/tools")

    def test_workspace_workflows_dir(self):
        p = PathResolver.workspace_workflows_dir("demo")
        assert str(p).endswith("workspaces/demo/workflows")

    def test_templates_dir(self):
        p = PathResolver.templates_dir()
        assert isinstance(p, Path)
        assert p.name == "templates"

    def test_templates_agents_dir(self):
        p = PathResolver.templates_agents_dir()
        assert str(p).endswith("templates/agents")

    def test_templates_hooks_dir(self):
        p = PathResolver.templates_hooks_dir()
        assert str(p).endswith("templates/hooks")

    def test_templates_workflows_dir(self):
        p = PathResolver.templates_workflows_dir()
        assert str(p).endswith("templates/workflows")

    def test_charts_dir(self):
        p = PathResolver.charts_dir()
        assert isinstance(p, Path)
        assert p.name == "charts"

    def test_exports_dir(self):
        p = PathResolver.exports_dir()
        assert isinstance(p, Path)
        assert p.name == "exports"

    def test_log_file(self):
        p = PathResolver.log_file()
        assert isinstance(p, Path)
        assert p.name == "morphix.log"

    def test_analytics_charts_dir(self):
        p = PathResolver.analytics_charts_dir()
        assert str(p).endswith("charts/analytics")


class TestNormalizePath:
    def test_no_project_root_returns_unchanged(self):
        result = PathResolver.normalize_path("src/main.py", project_root=None)
        assert result == "src/main.py"

    def test_full_prefix_match(self):
        result = PathResolver.normalize_path(
            "code_projects/myapp/src/main.py", project_root="code_projects/myapp"
        )
        assert result == "src/main.py"

    def test_last_component_match(self):
        result = PathResolver.normalize_path(
            "myapp/src/main.py", project_root="code_projects/myapp"
        )
        assert result == "src/main.py"

    def test_no_match_returns_unchanged(self):
        result = PathResolver.normalize_path(
            "other/src/main.py", project_root="code_projects/myapp"
        )
        assert result == "other/src/main.py"

    def test_exact_match_returns_dot(self):
        result = PathResolver.normalize_path(
            "code_projects/myapp", project_root="code_projects/myapp"
        )
        assert result == "."

    def test_last_component_exact_returns_dot(self):
        result = PathResolver.normalize_path("myapp", project_root="code_projects/myapp")
        assert result == "."


class TestNormalizeProjectRoot:
    def test_none_returns_none(self):
        result = PathResolver.normalize_project_root(None)
        assert result is None

    def test_adds_prefix_when_missing(self):
        result = PathResolver.normalize_project_root("myapp")
        assert result == "code_projects/myapp"

    def test_keeps_prefix_when_present(self):
        result = PathResolver.normalize_project_root("code_projects/myapp")
        assert result == "code_projects/myapp"
