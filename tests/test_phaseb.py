"""Phase B: routine ceilings / tripwires / kill conditions, and approval escalation.

Run: python -m unittest tests.test_phaseb -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["OPENGROKBOT_HOME"] = tempfile.mkdtemp(prefix="gbtest-")

from core.db import new_id, now  # noqa: E402
from core.engine import Engine  # noqa: E402
from core.routines import limits_block  # noqa: E402


class PhaseBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["OPENGROKBOT_HOME"] = tempfile.mkdtemp(prefix="gbtest-")
        cls.eng = Engine()
        cls.eng.settings.set("notifications.toast", False)
        cls.eng.start()

    @classmethod
    def tearDownClass(cls):
        cls.eng.stop()

    def setUp(self):
        self.bot = self.eng.bots.create(f"PhaseB Bot {uuid.uuid4().hex[:8]}")
        self._created: list[str] = []
        self._old_timeout = self.eng.settings.get("approval.timeout_min", 1440)

    def tearDown(self):
        self.eng.settings.set("approval.timeout_min", self._old_timeout)
        for aid in self._created:
            self.eng.db.execute("DELETE FROM approvals WHERE id=?", (aid,))

    # -- ceilings / tripwires / kill conditions --------------------------------
    def test_create_and_update_limits(self):
        r = self.eng.routines.create(self.bot["id"], "Guarded sweep", "0 8 * * 1-5", prompt="do the sweep",
                                     ceiling_items=50, anomaly_pct=20,
                                     kill_condition="more than 3 outputs I disagree with in a week")
        self.assertEqual(r["ceiling_items"], 50)
        self.assertEqual(r["anomaly_pct"], 20)
        self.assertIn("3 outputs", r["kill_condition"])
        r2 = self.eng.routines.update(r["id"], ceiling_items=10, anomaly_pct=0, kill_condition="")
        self.assertEqual(r2["ceiling_items"], 10)
        self.assertEqual(r2["anomaly_pct"], 0)
        self.assertEqual(r2["kill_condition"], "")
        self.eng.routines.delete(r["id"])

    def test_limits_clamped(self):
        r = self.eng.routines.create(self.bot["id"], "Clamped", "0 9 * * 1", prompt="x",
                                     ceiling_items=-5, anomaly_pct=350)
        self.assertEqual(r["ceiling_items"], 0)
        self.assertEqual(r["anomaly_pct"], 100)
        self.eng.routines.delete(r["id"])

    def test_limits_block_contents(self):
        txt = limits_block({"ceiling_items": 50, "anomaly_pct": 20, "kill_condition": "site layout changed"})
        self.assertIn("CEILING: never process more than 50 items", txt)
        self.assertIn("TRIPWIRE", txt)
        self.assertIn("20%", txt)
        self.assertIn("KILL CONDITION", txt)
        self.assertIn("site layout changed", txt)
        self.assertIn("receipt", txt)

    def test_limits_block_bare_routine_still_logged(self):
        txt = limits_block({})
        self.assertNotIn("CEILING", txt)
        self.assertNotIn("TRIPWIRE", txt)
        self.assertIn("receipt", txt)

    def test_plain_routine_reads_back_defaults(self):
        r = self.eng.routines.create(self.bot["id"], "Plain", "0 9 * * 1", prompt="x")
        self.assertEqual(r["ceiling_items"], 0)
        self.assertEqual(r["anomaly_pct"], 0)
        self.assertEqual(r["kill_condition"], "")
        self.eng.routines.delete(r["id"])

    # -- approval escalation ----------------------------------------------------
    def _pend(self, age_s: float) -> str:
        aid = new_id()
        self.eng.db.insert("approvals", {"id": aid, "bot_id": self.bot["id"], "thread_id": "",
                                         "category": "send", "tool": "demo_send", "summary": "Send a test mail",
                                         "details": "{}", "status": "pending", "created_at": now() - age_s})
        self._created.append(aid)
        return aid

    def test_three_day_rule_escalates_once(self):
        self.eng.settings.set("approval.timeout_min", 60 * 24 * 10)  # long timeout so the 72h rule fires first
        aid = self._pend(73 * 3600)
        before = int(self.eng.db.scalar("SELECT COUNT(*) FROM notifications WHERE bot_id=?", (self.bot["id"],), 0))
        n = self.eng.approvals.check_escalations()
        self.assertGreaterEqual(n, 1)
        self.assertEqual(self.eng.db.one("SELECT escalated FROM approvals WHERE id=?", (aid,))["escalated"], 1)
        after = int(self.eng.db.scalar("SELECT COUNT(*) FROM notifications WHERE bot_id=?", (self.bot["id"],), 0))
        self.assertGreater(after, before)
        self.assertEqual(self.eng.approvals.check_escalations(), 0)  # never twice

    def test_last_chance_nudge_before_timeout(self):
        timeout_s = float(self.eng.settings.get("approval.timeout_min", 1440)) * 60  # default 24h
        aid = self._pend(timeout_s * 0.8)  # past the 75% line, before expiry
        self.assertEqual(self.eng.approvals.check_escalations(), 1)
        self.assertEqual(self.eng.db.one("SELECT escalated FROM approvals WHERE id=?", (aid,))["escalated"], 1)

    def test_fresh_approval_not_escalated(self):
        self._pend(60)
        self.assertEqual(self.eng.approvals.check_escalations(), 0)
        self.assertEqual(self.eng.db.one("SELECT escalated FROM approvals WHERE id=?", (self._created[-1],))["escalated"], 0)


if __name__ == "__main__":
    unittest.main()

