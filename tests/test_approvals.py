"""Full Access approval mode: auto-approve everything except money, logins and tainted turns."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.engine import Engine  # noqa: E402
from core.tooling import Risk, ToolContext  # noqa: E402


class FakeRun:
    def __init__(self, tainted: bool = False, stopped: bool = False):
        self.dry_run = False
        self.tainted = tainted
        self.trigger = "user"
        self.stop = threading.Event()
        if stopped:
            self.stop.set()
        self.status = ""

    def set_status(self, s: str) -> None:
        self.status = s


def risk(category: str, **kw) -> Risk:
    return Risk(category=category, summary=f"test {category}", details={}, **kw)


class FullAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["ZEROTHEBOT_HOME"] = tempfile.mkdtemp(prefix="ztapprove-")
        cls.eng = Engine()
        cls.eng.settings.set("notifications.toast", False)
        cls.eng.settings.set("memory.auto_reflect", False)
        cls.eng.start()

    @classmethod
    def tearDownClass(cls):
        cls.eng.stop()

    def new_bot(self, **kw):
        kw.setdefault("job", "Test job")
        return self.eng.bots.create(f"Fa{uuid.uuid4().hex[:6]}", **kw)

    def ctx_for(self, bot: dict, **run_kw) -> ToolContext:
        th = self.eng.threads.main_thread(bot["id"])
        return ToolContext(engine=self.eng, bot=bot, thread_id=th["id"], turn_id="t1", run=FakeRun(**run_kw))  # type: ignore[arg-type]

    def test_full_access_auto_approves_send(self):
        bot = self.new_bot(approval_mode="full_access")
        d = self.eng.approvals.request(self.ctx_for(bot), risk("send"), "tool", {})
        self.assertTrue(d.approved)
        self.assertEqual(d.decided_by, "full_access")
        rows = self.eng.approvals.list(status="auto_approved", bot_id=bot["id"])
        self.assertTrue(any(r["decided_by"] == "full_access" for r in rows))

    def test_money_logins_tainted_never_auto_still_ask(self):
        bot = self.new_bot(approval_mode="full_access")
        cases = [risk("purchase"), risk("login"), risk("send", never_auto=True)]
        for r in cases:
            d = self.eng.approvals.request(self.ctx_for(bot, stopped=True), r, "tool", {})
            self.assertFalse(d.approved, r.category)
            self.assertNotEqual(d.decided_by, "full_access")
        d = self.eng.approvals.request(self.ctx_for(bot, tainted=True, stopped=True), risk("send"), "tool", {})
        self.assertFalse(d.approved)
        self.assertNotEqual(d.decided_by, "full_access")

    def test_ask_mode_unchanged(self):
        bot = self.new_bot(approval_mode="ask")
        d = self.eng.approvals.request(self.ctx_for(bot, stopped=True), risk("send"), "tool", {})
        self.assertFalse(d.approved)

    def test_decide_full_access_flips_mode(self):
        bot = self.new_bot()
        ctx = self.ctx_for(bot)
        box: list = []
        t = threading.Thread(target=lambda: box.append(self.eng.approvals.request(ctx, risk("send"), "tool", {})), daemon=True)
        t.start()
        aid = ""
        deadline = time.time() + 15
        while time.time() < deadline and not aid:
            pend = self.eng.approvals.list(status="pending", bot_id=bot["id"])
            if pend:
                aid = pend[0]["id"]
            else:
                time.sleep(0.2)
        self.assertTrue(aid, "approval never reached pending")
        out = self.eng.approvals.decide(aid, True, full_access=True)
        self.assertEqual(out["status"], "approved")
        t.join(15)
        self.assertTrue(box and box[0].approved)
        self.assertEqual(self.eng.bots.get(bot["id"])["approval_mode"], "full_access")

    def test_mode_validation(self):
        from core.bots import BotError  # noqa: E402

        bot = self.new_bot()
        self.eng.bots.update(bot["id"], approval_mode="full_access")
        self.assertEqual(self.eng.bots.get(bot["id"])["approval_mode"], "full_access")
        with self.assertRaises(BotError):
            self.eng.bots.update(bot["id"], approval_mode="nope")


if __name__ == "__main__":
    unittest.main()
