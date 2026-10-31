"""Lazy tool loading: relevance selection is deterministic and bounded, find_tools pins,
unpromoted-but-granted tools still execute.  python -m unittest tests.test_lazytools -v"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ZEROTHEBOT_HOME", tempfile.mkdtemp(prefix="zttest-lazy-"))

from core import agent as agent_mod  # noqa: E402
from core.engine import Engine  # noqa: E402
from core.providers import LLMResult, ToolCall  # noqa: E402
from core.tooling import ToolResult, ToolSpec  # noqa: E402
from tests.test_engine import FakeProvider, wait_for  # noqa: E402


def spec(name, desc="Does a thing.", schema=None):
    return ToolSpec(name, desc, schema or {"type": "object", "properties": {}},
                    lambda ctx, a: ToolResult(f"ran {name}"), label=lambda a: name,
                    group="test", read_only=True)


def many_specs(n, prefix="plug_tool"):
    out = {}
    topics = ["invoice", "calendar", "email", "file", "chat", "meeting", "contact", "task"]
    for i in range(n):
        t = topics[i % len(topics)]
        out[f"{prefix}_{i}_{t}"] = spec(f"{prefix}_{i}_{t}", f"Work with {t} number {i}.")
    return out


class PureTests(unittest.TestCase):
    def test_words_and_scoring(self):
        self.assertIn("invoice", agent_mod._words("Pay the invoice now!"))
        self.assertNotIn("the", agent_mod._words("Pay the invoice now!"))
        s = spec("xero_invoice_create", "Create a sales invoice in Xero.")
        self.assertGreater(agent_mod._tool_score({"invoice", "xero"}, s), 0)
        self.assertEqual(agent_mod._tool_score({"banana"}, s), 0)
        self.assertGreater(agent_mod._tool_score({"invoice"}, spec("invoice_list", "X")),
                           agent_mod._tool_score({"invoice"}, spec("calendar_free", "X")))

    def test_pick_tools_bounded_and_deterministic(self):
        all_specs = many_specs(60)
        words = {"invoice"}
        first, catalog = agent_mod._pick_tools(all_specs, words, set(), [], 8)
        second, _ = agent_mod._pick_tools(all_specs, words, set(), [], 8)
        self.assertEqual(set(first), set(second))
        self.assertLessEqual(len(first), 9)   # top_k + find_tools
        self.assertIn("find_tools", first)
        self.assertTrue(any("invoice" in n for n in first), first.keys())
        self.assertIn("calendar", catalog)          # non-matching tools drop to the catalog
        self.assertNotIn("plug_tool_0_invoice", catalog)   # promoted tools are not cataloged
        self.assertNotIn("find_tools:", catalog)   # not listed as a catalog item (it is promoted)

    def test_pick_tools_respects_pins_and_recent(self):
        all_specs = many_specs(60)
        keep, _ = agent_mod._pick_tools(all_specs, {"zzz_no_match"}, {"plug_tool_2_email"}, ["plug_tool_7_task"], 8)
        self.assertIn("plug_tool_2_email", keep)
        self.assertIn("plug_tool_7_task", keep)
        self.assertIn("find_tools", keep)

    def test_small_sets_pass_through(self):
        all_specs = many_specs(5)
        keep, catalog = agent_mod._pick_tools(all_specs, set(), set(), [], 24)
        self.assertEqual(set(keep), set(all_specs))
        self.assertEqual(catalog, "")

    def test_recent_tool_uses_and_last_text(self):
        history = [{"role": "assistant", "content": [{"type": "tool_use", "name": "a_x", "input": {}}]},
                   {"role": "user", "content": [{"type": "text", "text": "thanks"}]},
                   {"role": "assistant", "content": [{"type": "tool_use", "name": "b_y", "input": {}}]}]
        self.assertEqual(agent_mod._recent_tool_uses(history), ["b_y", "a_x"])
        self.assertEqual(agent_mod._last_user_text(history), "thanks")


class LazyTurnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["ZEROTHEBOT_HOME"] = tempfile.mkdtemp(prefix="zttest-")
        cls.eng = Engine()
        cls.eng.settings.set("notifications.toast", False)
        cls.eng.settings.set("memory.auto_reflect", False)
        cls.eng.settings.set("context.lazy_top_k", 8)
        cls.eng.start()

    @classmethod
    def tearDownClass(cls):
        cls.eng.stop()

    def setUp(self):
        self.eng.settings.set("context.lazy_tools", True)
        self.eng.settings.set("context.lazy_top_k", 8)
        self._real_make_provider = agent_mod.make_provider

    def use(self, script):
        fake = FakeProvider(script)
        agent_mod.make_provider = lambda *a, **k: fake
        return fake

    _bot_n = 0

    def new_bot(self):
        type(self)._bot_n += 1
        bot = self.eng.bots.create(f"Lazy{type(self)._bot_n}", job="Test job")
        return bot, self.eng.threads.main_thread(bot["id"])

    def run_turn(self, bot, th, text="go"):
        self.eng.send_user_message(th["id"], text)
        self.assertTrue(wait_for(lambda: not self.eng.turns.is_busy(bot["id"])), "turn did not finish")
        time.sleep(0.2)
        self.assertTrue(wait_for(lambda: not self.eng.turns.is_busy(bot["id"]), 10), "turn restarted")

    def tearDown(self):
        agent_mod.make_provider = self._real_make_provider
        if "tools_for" in self.eng.__dict__:
            del self.eng.__dict__["tools_for"]

    def _tools(self, registry):
        self.eng.tools_for = lambda run: list(registry.values())

    def test_big_toolset_is_trimmed_but_findable(self):
        registry = many_specs(60)
        self._tools(registry)
        bot, th = self.new_bot()
        fake = self.use([LLMResult(tool_calls=[ToolCall("t1", "find_tools", {"query": "invoice"})]),
                         LLMResult(text="done")])
        self.run_turn(bot, th, "handle the invoice please")
        first_tools = fake.calls[0]["tools"]
        self.assertLessEqual(len(first_tools), 9, first_tools)   # top_k + find_tools
        self.assertIn("find_tools", first_tools)
        self.assertNotIn("plug_tool_1_calendar", first_tools)
        # find_tools pinned invoice tools: second call carries full invoice schemas
        second_tools = fake.calls[1]["tools"]
        self.assertTrue(any("invoice" in t for t in second_tools), second_tools)

    def test_unpromoted_granted_tool_still_executes(self):
        registry = {"obscure_thing": spec("obscure_thing", "An unrelated helper.")}
        registry.update(many_specs(30, prefix="filler"))
        self._tools(registry)
        bot, th = self.new_bot()
        fake = self.use([LLMResult(tool_calls=[ToolCall("t1", "obscure_thing", {})]),
                         LLMResult(text="done")])
        self.run_turn(bot, th, "hello there")
        self.assertNotIn("obscure_thing", fake.calls[0]["tools"], "held back at first")
        items = self.eng.threads.display(th["id"])
        tools = [i for i in items if i["type"] == "tool"]
        self.assertTrue(tools and all(t["status"] == "ok" for t in tools), tools)

    def test_lazy_off_sends_everything(self):
        self.eng.settings.set("context.lazy_tools", False)
        registry = many_specs(40)
        self._tools(registry)
        bot, th = self.new_bot()
        fake = self.use([LLMResult(text="hi")])
        self.run_turn(bot, th)
        sent = fake.calls[0]["tools"]
        self.assertEqual(len(sent), 40)
        self.assertNotIn("find_tools", sent)


if __name__ == "__main__":
    unittest.main()
