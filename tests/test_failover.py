"""Provider failover: dead primary retries the same step on the next chain pair.
python -m unittest tests.test_failover -v"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ZEROTHEBOT_HOME", tempfile.mkdtemp(prefix="zttest-fo-"))

from core import agent as agent_mod  # noqa: E402
from core.engine import Engine  # noqa: E402
from core.providers import LLMResult, ProviderError, ToolCall  # noqa: E402
from tests.test_engine import wait_for  # noqa: E402


class FoProvider:
    def __init__(self, pid, label, script):
        self.profile = {"id": pid, "label": label}
        self.label = label
        self.model = "m-" + pid
        self.vision = False
        self.script = list(script)
        self._last = None
        self.calls = []

    def stream(self, system, messages, tools, on_text, should_stop, max_tokens=8192):
        self.calls.append([t["name"] for t in tools])
        if self.script:
            step = self.script.pop(0)
            self._last = step
        else:
            step = self._last if isinstance(self._last, Exception) else LLMResult(text="done")
        if isinstance(step, Exception):
            raise step
        if step.text:
            on_text(step.text)
        step.input_tokens, step.output_tokens = 100, 20
        return step


def err(kind, msg="boom", **kw):
    return ProviderError(kind, msg, **kw)


class FailoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["ZEROTHEBOT_HOME"] = tempfile.mkdtemp(prefix="zttest-")
        cls.eng = Engine()
        cls.eng.settings.set("notifications.toast", False)
        cls.eng.settings.set("memory.auto_reflect", False)
        cls.eng.settings.set("providers", {
            "p0": {"label": "P0", "kind": "openai", "base_url": "http://localhost:9/v1", "model": "m-p0", "needs_key": False},
            "p1": {"label": "P1", "kind": "openai", "base_url": "http://localhost:9/v1", "model": "m-p1", "needs_key": False},
            "p2": {"label": "P2", "kind": "openai", "base_url": "http://localhost:9/v1", "model": "m-p2", "needs_key": False}})
        cls.eng.start()

    @classmethod
    def tearDownClass(cls):
        cls.eng.stop()

    def setUp(self):
        self._real_make = agent_mod.make_provider
        agent_mod._FAILOVER_COOL.clear()
        self.eng.settings.set("failover", {"chain": [], "cooldown_min": 10})
        self.eng.settings.set("model_limits", {})

    def tearDown(self):
        agent_mod.make_provider = self._real_make
        agent_mod._FAILOVER_COOL.clear()

    def use_map(self, fakes):
        def _make(settings, profile_id=None, model=None):
            prof = settings.profile(profile_id)
            return fakes[prof["id"]]
        agent_mod.make_provider = _make

    def new_bot(self, name):
        bot = self.eng.bots.create(name, job="Test job", profile="p0", model="m-p0")
        return bot, self.eng.threads.main_thread(bot["id"])

    def run_turn(self, bot, th, text="go"):
        self.eng.send_user_message(th["id"], text)
        self.assertTrue(wait_for(lambda: not self.eng.turns.is_busy(bot["id"]), 60), "turn did not finish")
        time.sleep(0.2)
        self.assertTrue(wait_for(lambda: not self.eng.turns.is_busy(bot["id"]), 10), "turn restarted")

    def notices(self, th, level="error"):
        out = []
        for r in self.eng.threads.rows(th["id"]):
            if r["kind"] == "notice":
                from core.db import jload
                for b in jload(r["content"], []) or []:
                    if b.get("level", "") == level:
                        out.append(b.get("text", ""))
        return out

    def test_failover_to_next_on_rate_limit(self):
        self.eng.settings.set("failover", {"chain": [{"profile": "p1"}, {"profile": "p2"}], "cooldown_min": 10})
        p0 = FoProvider("p0", "P0", [err("rate_limit", "slow down", retry_after=0.01)])
        p1 = FoProvider("p1", "P1", [LLMResult(text="served by P1")])
        p2 = FoProvider("p2", "P2", [LLMResult(text="served by P2")])
        self.use_map({"p0": p0, "p1": p1, "p2": p2})
        bot, th = self.new_bot("Fo1")
        self.run_turn(bot, th)
        self.assertEqual(len(p1.calls), 1)
        self.assertEqual(len(p2.calls), 0, "stops at the first working pair")
        infos = self.notices(th, "info")
        self.assertEqual(sum("Continuing on" in t for t in infos), 1, infos)
        asst = [i for i in self.eng.threads.display(th["id"]) if i["type"] == "assistant"]
        self.assertTrue(any("served by P1" in (a.get("text") or "") for a in asst))

    def test_all_fail_reports_last_error(self):
        self.eng.settings.set("failover", {"chain": [{"profile": "p1"}], "cooldown_min": 10})
        self.use_map({"p0": FoProvider("p0", "P0", [err("rate_limit", "slow", retry_after=0.01)]),
                      "p1": FoProvider("p1", "P1", [err("server", "down")])})
        bot, th = self.new_bot("Fo2")
        self.run_turn(bot, th)
        errs = self.notices(th)
        self.assertTrue(any("down" in t for t in errs), errs)

    def test_cooled_pair_is_skipped(self):
        self.eng.settings.set("failover", {"chain": [{"profile": "p1"}, {"profile": "p2"}], "cooldown_min": 10})
        agent_mod._failover_note("p1", "m-p1", 10)
        p1 = FoProvider("p1", "P1", [LLMResult(text="should not be used")])
        p2 = FoProvider("p2", "P2", [LLMResult(text="served by P2")])
        self.use_map({"p0": FoProvider("p0", "P0", [err("server", "boom")]), "p1": p1, "p2": p2})
        bot, th = self.new_bot("Fo3")
        self.run_turn(bot, th)
        self.assertEqual(len(p1.calls), 0)
        self.assertEqual(len(p2.calls), 1)

    def test_size_failover_to_bigger_model(self):
        self.eng.settings.set("model_limits", {"p0:m-p0": 8000})
        self.eng.settings.set("failover", {"chain": [{"profile": "p1"}], "cooldown_min": 10})
        self.use_map({"p0": FoProvider("p0", "P0", [err("size", "too big", limit=8000, requested=12000)]),
                      "p1": FoProvider("p1", "P1", [LLMResult(text="big model served")])})
        bot, th = self.new_bot("Fo4")
        self.run_turn(bot, th)
        asst = [i for i in self.eng.threads.display(th["id"]) if i["type"] == "assistant"]
        self.assertTrue(any("big model served" in (a.get("text") or "") for a in asst))
        self.assertEqual(self.eng.settings.get("model_limits", {}).get("p0:m-p0"), 8000)

    def test_quota_no_chain_fails_fast(self):
        self.use_map({"p0": FoProvider("p0", "P0", [err("quota", "Daily free-model allowance used up")])})
        bot, th = self.new_bot("Fo5")
        self.run_turn(bot, th)
        errs = self.notices(th)
        self.assertTrue(any("Daily limit" in t for t in errs), errs)

    def test_unknown_pid_skipped(self):
        self.eng.settings.set("failover", {"chain": [{"profile": "nope"}, {"profile": "p1"}], "cooldown_min": 10})
        self.use_map({"p0": FoProvider("p0", "P0", [err("server", "boom")]),
                      "p1": FoProvider("p1", "P1", [LLMResult(text="served")])})
        bot, th = self.new_bot("Fo6")
        self.run_turn(bot, th)
        asst = [i for i in self.eng.threads.display(th["id"]) if i["type"] == "assistant"]
        self.assertTrue(any("served" in (a.get("text") or "") for a in asst))


if __name__ == "__main__":
    unittest.main()
