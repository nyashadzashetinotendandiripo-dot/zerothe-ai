"""Swarm mode tests with a scripted fake model. Run: .venv/Scripts/python.exe -m pytest tests/test_swarm.py -q"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["ZEROTHEBOT_HOME"] = tempfile.mkdtemp(prefix="ztswarm-")

from core import agent as agent_mod  # noqa: E402
from core.engine import Engine  # noqa: E402
from core.providers import LLMResult  # noqa: E402
from core.swarm import run_swarm  # noqa: E402

import core.providers as _prov_mod  # noqa: E402

if not hasattr(agent_mod, "estimate_tokens"):
    # WORKAROUND (test-local only): core/agent.py at HEAD calls estimate_tokens()
    # but never imports it -> every turn crashes with NameError. The owning agent
    # must fix the import; until then, bind the real helper here so swarm tests
    # exercise the real turn path. Intentionally NOT touching core/agent.py.
    agent_mod.estimate_tokens = _prov_mod.estimate_tokens  # type: ignore[attr-defined]


class EchoProvider:
    """Replies 'reply from <BotName>' using the name in the system prompt (parallel-safe)."""

    vision = False
    model = "fake"
    profile = {"id": "fake", "label": "Fake"}

    def stream(self, system, messages, tools, on_text, should_stop, max_tokens=8192):
        m = re.match(r"You are (\S+)", system or "")
        text = f"reply from {m.group(1)}" if m else "reply from bot"
        on_text(text)
        return LLMResult(text=text, input_tokens=100, output_tokens=20)


class HangProvider(EchoProvider):
    """Never replies until the turn is stopped (lets the timeout path trigger)."""

    def stream(self, system, messages, tools, on_text, should_stop, max_tokens=8192):
        while not should_stop():
            time.sleep(0.05)
        return LLMResult(text="", input_tokens=0, output_tokens=0)


class SwarmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["ZEROTHEBOT_HOME"] = tempfile.mkdtemp(prefix="ztswarm-")
        cls.eng = Engine()
        cls.eng.settings.set("notifications.toast", False)
        cls.eng.settings.set("memory.auto_reflect", False)
        cls.eng.start()

    @classmethod
    def tearDownClass(cls):
        cls.eng.stop()

    def use(self, provider):
        agent_mod.make_provider = lambda *a, **k: provider

    def new_bot(self, prefix):
        return self.eng.bots.create(f"{prefix}{uuid.uuid4().hex[:6]}", job="Test job")

    def test_fanout_reaches_two_bots(self):
        a, b, caller = self.new_bot("SwAlpha"), self.new_bot("SwBeta"), self.new_bot("SwCaller")
        reply = self.eng.threads.main_thread(caller["id"])
        self.use(EchoProvider())
        out = run_swarm(self.eng, [a["id"], b["id"]], "report status", reply["id"], timeout_each=60)
        self.assertTrue(out["ok"], out)
        self.assertEqual(set(out["results"]), {a["id"], b["id"]})
        self.assertIn(f"reply from {a['name']}", out["results"][a["id"]])
        self.assertIn(f"reply from {b['name']}", out["results"][b["id"]])
        for bot in (a, b):
            th = self.eng.threads.main_thread(bot["id"])
            self.assertIn("report status", self.eng.threads.last_user_text(th["id"]))

    def test_summary_posted_to_reply_thread(self):
        a, b, caller = self.new_bot("SwSumA"), self.new_bot("SwSumB"), self.new_bot("SwSumC")
        reply = self.eng.threads.main_thread(caller["id"])
        self.use(EchoProvider())
        out = run_swarm(self.eng, [a["id"], b["id"]], "summarize this", reply["id"], timeout_each=60)
        self.assertTrue(out["ok"], out)
        self.assertIn(a["name"], out["summary"])
        self.assertIn(b["name"], out["summary"])
        items = self.eng.threads.display(reply["id"])
        assistants = [i for i in items if i.get("type") == "assistant"]
        self.assertEqual(len([i for i in assistants if "Swarm result" in i.get("text", "")]), 1)
        self.assertIn(f"reply from {a['name']}", assistants[-1]["text"])

    def test_timeout_records_error_and_still_returns(self):
        a, caller = self.new_bot("SwHang"), self.new_bot("SwHangC")
        reply = self.eng.threads.main_thread(caller["id"])
        self.use(HangProvider())
        t0 = time.time()
        out = run_swarm(self.eng, [a["id"]], "are you there?", reply["id"], timeout_each=2)
        self.assertLess(time.time() - t0, 30, "swarm must not hang forever")
        self.assertIn(a["id"], out["results"])
        self.assertTrue(out["results"][a["id"]].startswith("[timeout"), out["results"])
        self.assertFalse(out["ok"])
        self.assertIn("SwHang", out["summary"])

    def test_empty_bot_list_is_clean_error(self):
        caller = self.new_bot("SwEmpty")
        reply = self.eng.threads.main_thread(caller["id"])
        self.use(EchoProvider())
        out = run_swarm(self.eng, [], "do things", reply["id"], timeout_each=10)
        self.assertFalse(out["ok"])
        self.assertEqual(out["results"], {})

    def test_unknown_bot_does_not_kill_swarm(self):
        a, caller = self.new_bot("SwPart"), self.new_bot("SwPartC")
        reply = self.eng.threads.main_thread(caller["id"])
        self.use(EchoProvider())
        out = run_swarm(self.eng, [a["id"], "no-such-bot"], "go", reply["id"], timeout_each=60)
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["results"]["no-such-bot"].startswith("[error]"))
        self.assertIn(f"reply from {a['name']}", out["results"][a["id"]])


class SwarmCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["ZEROTHEBOT_HOME"] = tempfile.mkdtemp(prefix="ztswarm-cmd-")
        cls.eng = Engine()
        cls.eng.settings.set("notifications.toast", False)
        cls.eng.settings.set("memory.auto_reflect", False)
        cls.eng.start()

    @classmethod
    def tearDownClass(cls):
        cls.eng.stop()

    def test_usage_and_unknown_bot(self):
        from core import commands  # noqa: E402

        eng = self.eng
        b = eng.bots.create(f"SwCmd{uuid.uuid4().hex[:6]}", job="Test job")
        th = eng.threads.main_thread(b["id"])
        out = commands.run(eng, th["id"], "/swarm")
        self.assertTrue(out["handled"])
        out = commands.run(eng, th["id"], "/swarm NoSuchBotXYZ: do things")
        items = eng.threads.rows(th["id"])
        self.assertTrue(any("NoSuchBotXYZ" in str(m.get("content", "")) for m in items))

    def test_fanout_and_summary(self):
        from core import commands  # noqa: E402

        self.use_echo()
        a = self.eng.bots.create(f"SwC1{uuid.uuid4().hex[:6]}", job="Test job")
        b = self.eng.bots.create(f"SwC2{uuid.uuid4().hex[:6]}", job="Test job")
        caller = self.eng.bots.create(f"SwC0{uuid.uuid4().hex[:6]}", job="Test job")
        th = self.eng.threads.main_thread(caller["id"])
        out = commands.run(self.eng, th["id"], f"/swarm {a['name']}, {b['name']}: say hi")
        self.assertTrue(out["handled"])
        deadline = time.time() + 60
        summary = ""
        while time.time() < deadline:
            rows = self.eng.threads.rows(th["id"])
            for m in rows:
                c = m.get("content", "")
                s = c if isinstance(c, str) else json.dumps(c)
                if "Swarm summary" in s or ("reply from" in s and a["name"] in s):
                    summary = s
                    break
            if summary:
                break
            time.sleep(0.5)
        self.assertIn(a["name"], summary)
        self.assertIn(b["name"], summary)

    def use_echo(self):
        agent_mod.make_provider = lambda *a, **k: EchoProvider()


if __name__ == "__main__":
    unittest.main()
