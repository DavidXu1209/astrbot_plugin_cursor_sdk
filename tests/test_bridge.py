"""Offline contract checks: no AstrBot install, SDK process, network or keys."""

import asyncio
import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch
import sys


class ProviderStub:
    def __init__(self, config, settings):
        self.provider_config = config

    def set_model(self, model):
        self.model = model

    def get_model(self):
        return self.model

    def _ensure_message_to_dicts(self, messages):
        return [m.model_dump() if hasattr(m, "model_dump") else m for m in messages or []]


@dataclass
class UsageStub:
    input_other: int
    input_cached: int
    output: int


def load_plugin():
    names = [
        "astrbot", "astrbot.api", "astrbot.api.provider", "astrbot.api.star",
        "astrbot.core", "astrbot.core.provider", "astrbot.core.provider.entities",
        "astrbot.core.provider.register",
    ]
    modules = {name: ModuleType(name) for name in names}
    modules["astrbot.api.provider"].Provider = ProviderStub
    modules["astrbot.api.provider"].ProviderType = SimpleNamespace(CHAT_COMPLETION="chat")
    modules["astrbot.api.provider"].LLMResponse = lambda **kwargs: SimpleNamespace(**kwargs)
    modules["astrbot.api.star"].Context = object
    modules["astrbot.api.star"].Star = type("Star", (), {})
    modules["astrbot.core.provider.entities"].TokenUsage = UsageStub
    register = modules["astrbot.core.provider.register"]
    register.provider_cls_map = {}
    register.provider_registry = []
    def register_adapter(*args, **kwargs):
        def decorate(cls):
            cls.registered_config_template = kwargs["default_config_tmpl"]
            return cls
        return decorate
    register.register_provider_adapter = register_adapter
    with patch.dict(sys.modules, modules):
        path = Path(__file__).resolve().parents[1] / "main.py"
        spec = importlib.util.spec_from_file_location("cursor_plugin_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


plugin = load_plugin()


class RequestChecks(TestCase):
    def test_new_provider_default_template(self):
        template = plugin.CursorTextProvider.registered_config_template
        self.assertEqual(template["id"], "cursor_sdk_text")
        self.assertEqual(template["provider"], "cursor_sdk")
        self.assertEqual(template["model"], "auto")
        self.assertEqual(template["timeout"], 120)

    def test_persona_order_roles_and_extra_text(self):
        history = [
            {"role": "user", "content": "叫我小蓝"},
            {"role": "assistant", "content": [{"type": "text", "text": "知道啦"}]},
        ]
        request = plugin.build_text_request("我叫什么？", history, "温柔简短", [{"type": "text", "text": "记忆：蓝色"}])
        self.assertIn("【角色设定】\n温柔简短\n", request)
        self.assertIn("不要自称 Cursor", request)
        line = next(l for l in request.splitlines() if l.startswith("[{"))
        messages = json.loads(line)
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "user"])
        self.assertEqual(messages[-1]["content"], "我叫什么？\n记忆：蓝色")
        self.assertEqual(len(history), 2)

    def test_task_without_persona_has_no_roleplay_framing(self):
        request = plugin.build_text_request("总结这段对话", [], None)
        self.assertNotIn("角色扮演", request)
        self.assertEqual(json.loads(request.splitlines()[-1])[-1]["content"], "总结这段对话")

    def test_tools_and_media_fail_closed(self):
        for message in [
            {"role": "tool", "content": "result"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "x"}]},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "file://private"}}]},
        ]:
            with self.assertRaises(ValueError):
                plugin.build_text_request(None, [message], None)

    def test_model_selection_params(self):
        self.assertEqual(plugin.model_selection("auto"), "auto")
        self.assertEqual(
            plugin.model_selection("gpt-5.4-mini@reasoning=none,fast=true"),
            {"id": "gpt-5.4-mini", "params": [{"id": "reasoning", "value": "none"}, {"id": "fast", "value": "true"}]},
        )
        options = plugin.sdk_agent_options("gemini-3.6-flash@effort=minimal", "k", "w")
        self.assertEqual(options["model"]["id"], "gemini-3.6-flash")
        for bad in ["x@reasoning", "x@=none", "bad id@a=b", "x@a=b c"]:
            with self.assertRaises(ValueError):
                plugin.model_selection(bad)

    def test_no_tools_or_ambient_settings(self):
        options = plugin.sdk_agent_options("auto", "fake-test-key", "empty-workspace")
        self.assertEqual(options["tools"], [])
        self.assertEqual(options["disallowed_tools"], ["mcp", "task"])
        self.assertEqual(options["local"]["setting_sources"], [])
        self.assertEqual(options["local"]["custom_tools"], {})
        self.assertEqual(options["mcp_servers"], {})
        self.assertEqual(options["agents"], {})

    def test_token_mapping_preserves_sdk_total_without_reasoning_double_count(self):
        mapped = plugin._token_usage(SimpleNamespace(input_tokens=10, cache_read_tokens=20, cache_write_tokens=3, output_tokens=4, reasoning_tokens=2))
        self.assertEqual((mapped.input_other, mapped.input_cached, mapped.output), (13, 20, 4))


class ProviderChecks(IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = plugin.CursorTextProvider({"key": ["fake-test-key"], "model": "auto"}, {})

    async def test_tool_request_rejected_before_sdk(self):
        with patch.object(self.provider, "_call_sdk", new_callable=AsyncMock) as call:
            with self.assertRaises(ValueError):
                await self.provider.text_chat(prompt="run shell", func_tool=SimpleNamespace(empty=lambda: False))
            call.assert_not_awaited()

    async def test_separate_calls_do_not_reuse_or_leak_history(self):
        with patch.object(self.provider, "_call_sdk", new_callable=AsyncMock, return_value=SimpleNamespace(completion_text="ok")) as call:
            await self.provider.text_chat(prompt="用户甲的秘密", contexts=[])
            await self.provider.text_chat(prompt="用户乙", contexts=[])
            self.assertNotIn("用户甲的秘密", call.await_args_list[1].args[0])

    async def test_stream_yields_one_chunk_then_final_response(self):
        response = SimpleNamespace(completion_text="hello", is_chunk=False, id="r1")
        with patch.object(self.provider, "_call_sdk", new_callable=AsyncMock, return_value=response) as call:
            items = [item async for item in self.provider.text_chat_stream(prompt="hi")]
        call.assert_awaited_once()
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0].is_chunk)
        self.assertEqual(items[0].completion_text, "hello")
        self.assertIs(items[1], response)

    async def test_exception_does_not_echo_secret(self):
        with patch.object(self.provider, "_call_sdk", new_callable=AsyncMock, side_effect=Exception("Authorization: secret-api-key")):
            with self.assertRaises(RuntimeError) as raised:
                await self.provider.text_chat(prompt="hi")
        self.assertNotIn("secret-api-key", str(raised.exception))

    async def test_timeout_is_bounded_and_not_retried(self):
        self.provider.timeout = 0.01
        async def pending(*args):
            await asyncio.Event().wait()
        with patch.object(self.provider, "_call_sdk", side_effect=pending) as call:
            with self.assertRaises(RuntimeError):
                await self.provider.text_chat(prompt="hi")
            self.assertEqual(call.call_count, 1)


class RuntimeChecks(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory(prefix="cursor-runtime-test-")
        self.provider = plugin.CursorTextProvider({"id": "test/auto", "key": ["fake-key"], "model": "auto"}, {})
        self.root_patch = patch.object(self.provider, "_runtime_root", return_value=Path(self.temp.name))
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.clients, self.options, self.runs, self.requests, self.released = [], [], [], [], []
        self.next_status = "finished"
        self.block_wait = False
        owner = self

        class Agent:
            def __init__(self, options):
                owner.options.append(options)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                owner.released.append(self)

            async def send(self, request):
                owner.requests.append(request)
                status = owner.next_status
                async def wait():
                    if owner.block_wait:
                        await asyncio.Event().wait()
                    return SimpleNamespace(status=status, result="ok", usage=None,
                                           error_code=None, error_message="not supported in your region https://secret", id="synthetic-run")
                run = SimpleNamespace(id=f"synthetic-run-{len(owner.runs)}", status="running", wait=wait, cancel=AsyncMock())
                owner.runs.append(run)
                return run

        def make_client(**kwargs):
            client = SimpleNamespace(agents=SimpleNamespace(create=AsyncMock(side_effect=lambda options: Agent(options))),
                                     ping=AsyncMock(return_value="pong"), aclose=AsyncMock(), launch_options=kwargs)
            self.clients.append(client)
            return client

        self.launch = AsyncMock(side_effect=make_client)
        sdk = ModuleType("cursor_sdk")
        sdk.AsyncClient = SimpleNamespace(launch_bridge=self.launch)
        self.sdk_patch = patch.dict(sys.modules, {"cursor_sdk": sdk})
        self.sdk_patch.start()
        self.addCleanup(self.sdk_patch.stop)

    async def asyncTearDown(self):
        await self.provider.terminate()
        self.temp.cleanup()

    async def test_shared_bridge_has_fresh_agents_and_workspaces(self):
        await self.provider.text_chat(prompt="first synthetic secret")
        await self.provider.text_chat(prompt="second")
        self.assertEqual(self.launch.await_count, 1)
        self.assertEqual(len(self.options), 2)
        paths = [item["local"]["cwd"] for item in self.options]
        self.assertNotEqual(*paths)
        self.assertTrue(all(not Path(path).exists() for path in paths))
        self.assertNotIn("first synthetic secret", self.requests[1])
        self.assertEqual(len(self.released), 2)
        self.assertEqual(self.clients[0].launch_options["host"], "127.0.0.1")
        self.assertFalse(self.clients[0].launch_options["allow_api_key_env_fallback"])
        self.assertEqual(self.clients[0].launch_options["max_retries"], 0)
        for options in self.options:
            self.assertEqual(options["tools"], [])
            self.assertEqual(options["api_key"], "fake-key")

    async def test_concurrent_first_calls_start_one_bridge(self):
        await asyncio.gather(self.provider.text_chat(prompt="one"), self.provider.text_chat(prompt="two"))
        self.assertEqual(self.launch.await_count, 1)
        self.assertEqual(len(self.runs), 2)
        self.assertNotEqual(self.options[0]["local"]["cwd"], self.options[1]["local"]["cwd"])

    async def test_dead_bridge_replaced_before_next_request(self):
        await self.provider.text_chat(prompt="one")
        self.clients[0].ping.side_effect = ConnectionError("synthetic transport failure")
        await self.provider.text_chat(prompt="two")
        self.assertEqual(self.launch.await_count, 2)
        self.clients[0].aclose.assert_awaited_once()
        self.assertEqual(len(self.runs), 2)

    async def test_model_failure_is_not_retried_and_stays_private(self):
        self.next_status = "error"
        with self.assertRaises(RuntimeError) as raised:
            await self.provider.text_chat(prompt="one")
        self.assertIn("模型在当前地区不可用", str(raised.exception))
        self.assertNotIn("https://secret", str(raised.exception))
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(len(self.released), 1)

    async def test_cancellation_cancels_run_without_closing_shared_transport(self):
        self.block_wait = True
        task = asyncio.create_task(self.provider.text_chat(prompt="one"))
        while not self.runs:
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.runs[0].cancel.assert_awaited_once()
        self.assertEqual(len(self.released), 1)
        self.clients[0].aclose.assert_not_awaited()

    async def test_termination_closes_bridge_and_rejects_new_requests(self):
        await self.provider.text_chat(prompt="one")
        bridge_path = Path(self.clients[0].launch_options["workspace"])
        self.assertTrue(bridge_path.exists())
        await self.provider.terminate()
        self.clients[0].aclose.assert_awaited_once()
        self.assertFalse(bridge_path.exists())
        with self.assertRaises(RuntimeError):
            await self.provider.text_chat(prompt="two")
        self.assertEqual(self.launch.await_count, 1)
        self.assertEqual(len(self.runs), 1)
