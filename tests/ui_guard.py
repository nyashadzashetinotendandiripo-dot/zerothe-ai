"""Offscreen check of Settings > Providers -> Test (full): key + model list + a real chat call, and its failure wording.

    python tests/ui_guard.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ZEROTHEBOT_HOME"] = tempfile.mkdtemp(prefix="zttest-guardui-")

import uvicorn  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.engine import Engine  # noqa: E402
from service.server import create_app  # noqa: E402
from tests.test_providers import Handler  # noqa: E402

fake = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=fake.serve_forever, daemon=True).start()
eng = Engine()
eng.settings.set("notifications.toast", False)
eng.settings.set("memory.auto_reflect", False)
eng.start()
PORT, TOKEN = 18767, "t"
threading.Thread(target=uvicorn.Server(uvicorn.Config(create_app(eng, TOKEN), host="127.0.0.1", port=PORT, log_level="warning")).run, daemon=True).start()
time.sleep(1.5)

from ui import theme  # noqa: E402
from ui.api import Api, Connection  # noqa: E402
from ui.pages_settings import SettingsPage  # noqa: E402
from ui.store import Store  # noqa: E402

app = QApplication(sys.argv)
app.setStyleSheet(theme.qss())
api = Api(Connection("local", f"http://127.0.0.1:{PORT}", TOKEN))
base = f"http://127.0.0.1:{fake.server_address[1]}/oai"
api.call("PUT", "/api/providers/fakeprov", json_body={"label": "Fake endpoint", "kind": "openai", "base_url": base,
                                                      "model": "gpt-a", "needs_key": False, "vision": False})
api.call("PUT", "/api/providers/deadprov", json_body={"label": "Dead endpoint", "kind": "openai",
                                                      "base_url": "http://127.0.0.1:9/v1", "model": "gpt-a", "needs_key": False})
store = Store(api)
store.apply_bootstrap(api.call("GET", "/api/bootstrap"))


def pump(sec: float) -> None:
    end = time.time() + sec
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def select(page, pid: str) -> None:
    for i in range(page.prov_list.count()):
        if page.prov_list.item(i).data(0x100) == pid:   # Qt.UserRole
            page.prov_list.setCurrentRow(i)
            return
    raise AssertionError(f"provider {pid} not in list")


fails = []
page = SettingsPage(api, store)
page.resize(1000, 700)
page.show()
page.load()

# 1. healthy provider: Test shows key OK, model count, and a real chat reply
select(page, "fakeprov")
pump(1.5)
page.p_test()
pump(6.0)
s1 = page.p_status.text()
print("healthy test:", s1)
if "Key OK" not in s1 or "Chat test OK" not in s1:
    fails.append("healthy provider: expected Key OK + Chat test OK, got: " + s1)

# 2. wrong model id: Test warns it isn't on this provider (saved first, like a user typing it)
page.p_picker.combo.setCurrentText("wrong-model")
pump(0.2)
page.p_test()
pump(6.0)
s2 = page.p_status.text()
print("wrong model:", s2)
if "isn't on this provider" not in s2:
    fails.append("wrong model: expected 'isn't on this provider', got: " + s2)

# 3. unreachable endpoint: Test reports the chat failure in plain language
select(page, "deadprov")
pump(1.5)
page.p_test()
pump(8.0)
s3 = page.p_status.text()
print("dead endpoint:", s3)
if "Chat failed" not in s3 or "Network" not in s3:
    fails.append("dead endpoint: expected Chat failed + Network, got: " + s3)

code = 1 if fails else 0
print("FAIL: " + "; ".join(fails) if fails else "ALL OK")
# cooperative teardown first: closing the page and quitting Qt avoids the flaky
# access violation when daemon threads are killed mid-syscall by os._exit
try:
    page.hide()
    page.deleteLater()
    app.quit()
    pump(1.0)
except Exception:
    pass
os._exit(code)
