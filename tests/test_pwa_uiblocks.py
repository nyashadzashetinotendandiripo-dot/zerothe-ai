"""The phone renders the same Intelligent-UI widgets as the desktop.

The protocol lives in core/uiblocks.py and the desktop widgets in ui/blocks.py; the
phone port lives in web/app.js. These checks keep the three in step without a browser.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.uiblocks import BLOCK_TYPES  # noqa: E402

WEB = Path(__file__).resolve().parent.parent / "web"


class PwaUiBlocks(unittest.TestCase):
    def test_phone_supports_every_desktop_block_type(self):
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("Intelligent-UI blocks", js)
        m = re.search(r"UI_TYPES\s*=\s*\[([^\]]*)\]", js)
        self.assertIsNotNone(m, "UI_TYPES list not found in app.js")
        phone = re.findall(r"'([a-z]+)'", m.group(1))
        self.assertEqual(phone, list(BLOCK_TYPES))

    def test_widget_hooks_and_parser_exist(self):
        js = (WEB / "app.js").read_text(encoding="utf-8")
        for marker in ("function uiSplit", "function uiPlain", "function uiEval", "function uiBlock", "function aiBody",
                       "data-uireply", "data-uiopen", "data-uikey", "uiform-send", "uicalc-send", "wireUI("):
            self.assertIn(marker, js, marker)

    def test_actions_post_replies_like_the_desktop(self):
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("async function postReply", js)
        self.assertIn("/api/threads/' + S.thread.id + '/messages", js)   # reuses the normal send endpoint

    def test_every_type_has_styles(self):
        css = (WEB / "style.css").read_text(encoding="utf-8")
        for cls in (".ui-buttons", ".ui-form", ".ui-table", ".ui-chart", ".ui-steps", ".ui-stats", ".ui-calc", ".ui-cards", ".ui-note"):
            self.assertIn(cls, css, cls)


if __name__ == "__main__":
    unittest.main()
