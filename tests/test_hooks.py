# tests/test_hooks.py
import asyncio

import pytest


class TestHookContext:
    def test_default_values(self):
        from core.hooks_registry import HookContext

        ctx = HookContext(hook_point="on_before_tool", tool_name="test_tool")
        assert ctx.hook_point == "on_before_tool"
        assert ctx.tool_name == "test_tool"
        assert ctx.parameters == {}
        assert ctx.role == "agent"
        assert ctx.result is None
        assert ctx.error is None
        assert ctx.duration == 0.0
        assert ctx.attempt == 1
        assert ctx.workspace == "main"
        assert ctx.session_id is None


class TestHooksRegistry:
    def test_register_and_list(self):
        from core.hooks_registry import HooksRegistry

        reg = HooksRegistry()

        @reg.register("on_before_tool")
        def my_hook(ctx):
            pass

        hooks = reg.list_hooks()
        assert "on_before_tool" in hooks
        assert "my_hook" in hooks["on_before_tool"]

    def test_register_multiple_hooks_same_point(self):
        from core.hooks_registry import HooksRegistry

        reg = HooksRegistry()

        @reg.register("on_before_tool")
        def hook_a(ctx):
            pass

        @reg.register("on_before_tool")
        def hook_b(ctx):
            pass

        hooks = reg.list_hooks()
        assert len(hooks["on_before_tool"]) == 2

    def test_unregister(self):
        from core.hooks_registry import HooksRegistry

        reg = HooksRegistry()

        @reg.register("on_before_tool")
        def hook_a(ctx):
            pass

        assert reg.unregister("on_before_tool", hook_a) is True
        assert reg.unregister("on_before_tool", hook_a) is False
        assert "on_before_tool" not in reg.list_hooks()

    def test_clear_hook_point(self):
        from core.hooks_registry import HooksRegistry

        reg = HooksRegistry()

        @reg.register("on_before_tool")
        def hook(ctx):
            pass

        @reg.register("on_after_tool")
        def hook2(ctx):
            pass

        reg.clear_hook_point("on_before_tool")
        hooks = reg.list_hooks()
        assert "on_before_tool" not in hooks
        assert "on_after_tool" in hooks

    def test_clear_all(self):
        from core.hooks_registry import HooksRegistry

        reg = HooksRegistry()

        @reg.register("on_before_tool")
        def hook(ctx):
            pass

        reg.clear()
        assert reg.list_hooks() == {}

    @pytest.mark.asyncio
    async def test_dispatch_calls_sync_hook(self):
        from core.hooks_registry import HookContext, HooksRegistry

        reg = HooksRegistry()
        called = []

        @reg.register("on_before_tool")
        def my_hook(ctx):
            called.append(ctx.tool_name)

        ctx = HookContext(hook_point="on_before_tool", tool_name="bash_manager")
        await reg.dispatch("on_before_tool", ctx)
        assert called == ["bash_manager"]

    @pytest.mark.asyncio
    async def test_dispatch_calls_async_hook(self):
        from core.hooks_registry import HookContext, HooksRegistry

        reg = HooksRegistry()
        called = []

        @reg.register("on_after_tool")
        async def my_async_hook(ctx):
            await asyncio.sleep(0)
            called.append(ctx.tool_name)

        ctx = HookContext(hook_point="on_after_tool", tool_name="file_manager")
        await reg.dispatch("on_after_tool", ctx)
        assert called == ["file_manager"]

    @pytest.mark.asyncio
    async def test_dispatch_silently_catches_exceptions(self):
        from core.hooks_registry import HookContext, HooksRegistry

        reg = HooksRegistry()
        good_called = []

        @reg.register("on_before_tool")
        def bad_hook(ctx):
            raise RuntimeError("boom")

        @reg.register("on_before_tool")
        def good_hook(ctx):
            good_called.append(True)

        ctx = HookContext(hook_point="on_before_tool", tool_name="test")
        # Should not raise
        await reg.dispatch("on_before_tool", ctx)
        assert good_called == [True]

    @pytest.mark.asyncio
    async def test_dispatch_no_matching_hook(self):
        from core.hooks_registry import HookContext, HooksRegistry

        reg = HooksRegistry()
        ctx = HookContext(hook_point="on_before_tool", tool_name="test")
        # Should not raise
        await reg.dispatch("on_before_tool", ctx)

    @pytest.mark.asyncio
    async def test_dispatch_sets_hook_point_on_context(self):
        from core.hooks_registry import HookContext, HooksRegistry

        reg = HooksRegistry()
        received = []

        @reg.register("on_after_tool")
        def my_hook(ctx):
            received.append(ctx.hook_point)

        ctx = HookContext(hook_point="", tool_name="test")
        await reg.dispatch("on_after_tool", ctx)
        assert received == ["on_after_tool"]


class TestHookContextMutation:
    @pytest.mark.asyncio
    async def test_dispatch_updates_context_hook_point(self):
        from core.hooks_registry import HookContext, HooksRegistry

        reg = HooksRegistry()
        received = []

        @reg.register("on_before_tool")
        def hook(ctx):
            received.append(ctx.hook_point)

        ctx = HookContext(hook_point="wrong", tool_name="test")
        await reg.dispatch("on_before_tool", ctx)
        assert received == ["on_before_tool"]
