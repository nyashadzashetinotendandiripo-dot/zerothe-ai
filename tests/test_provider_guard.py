"""Provider Guard tests: actionable errors, learned token limits, size fail-fast, vision auto-fix, full provider Test.
Run: python -m unittest tests.test_provider_guard -v"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_HOME = tempfile.mkdtemp(prefix="zttest-")
os.environ["ZEROTHEBOT_HOME"] = _HOME

from core import agent as agent_mod  # noqa: E402
from core.db import jload  # noqa: E402
from core.engine import Engine  # noqa: E402
from core.providers import (LLMResult, OpenAICompatProvider, ProviderError, _map_http_status,  # noqa: E402
                            estimate_tokens, estimate_with_tools, friendly_error, learn_limit,
                            learned_limit, model_warnings)


class FakeSettings:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


# ------------------------------------------------------------------ error mapping
class MapTests(unittest.TestCase):
    def test_402_is_actionable_billing(self):
        e = _map_http_status(402, "out of credits", model="m")
        self.assertEqual(e.kind, "billing")
        self.assertIn("billing page", friendly_error(e))

    def test_404_names_the_model(self):
        e = _map_http_status(404, "model_not_found", model="qwen-3.8-27b")
        self.assertEqual(e.kind, "bad_request")
        self.assertIn("qwen-3.8-27b", str(e))
        self.assertIn("Settings > Providers", str(e))

    def test_groq_413_size_with_numbers(self):
        msg = ("Request too large for model `openai/gpt-oss-120b`. The input token count exceeds the limit. "
               "Limit: 8000, Requested: 12205.")
        e = _map_http_status(413, msg, model="openai/gpt-oss-120b")
        self.assertEqual(e.kind, "size")
        self.assertEqual(e.limit, 8000)
        self.assertEqual(e.requested, 12205)
        self.assertTrue(friendly_error(e).startswith("Prompt too big"))

    def test_429_too_large_is_size_not_rate(self):
        e = _map_http_status(429, "Request too large for model m. Limit: 8000 Requested: 16077")
        self.assertEqual(e.kind, "size")
        self.assertEqual(e.limit, 8000)

    def test_429_real_rate_limit_stays_rate(self):
        e = _map_http_status(429, "Rate limit reached. Please try again in 12s.")
        self.assertEqual(e.kind, "rate_limit")
        self.assertEqual(e.retry_after, 12.0)

    def test_429_daily_quota_fails_fast(self):
        e = _map_http_status(429, "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day")
        self.assertEqual(e.kind, "quota")
        self.assertTrue(friendly_error(e).startswith("Daily limit"))
        self.assertEqual(agent_mod.RETRIES.get("quota", 0), 0)
        hint = agent_mod.AgentRun._hint(e)
        self.assertIn("reset daily", hint)

    def test_openai_context_length_is_size(self):
        msg = "This model's maximum context length is 128000 tokens, however you requested 131072 tokens."
        e = _map_http_status(400, msg)
        self.assertEqual(e.kind, "size")
        self.assertEqual(e.limit, 128000)
        self.assertEqual(e.requested, 131072)

    def test_image_rejection_is_vision(self):
        e = _map_http_status(400, "messages[20].content must be a string")
        self.assertEqual(e.kind, "vision")
        self.assertTrue(friendly_error(e).startswith("Images not supported"))

    def test_generic_400_stays_bad_request(self):
        self.assertEqual(_map_http_status(400, "unsupported parameter foo").kind, "bad_request")

    def test_auth_and_server_kinds_unchanged(self):
        self.assertEqual(_map_http_status(401, "bad key").kind, "auth")
        self.assertEqual(_map_http_status(500, "boom").kind, "server")


# ------------------------------------------------------------------ helpers
class HelperTests(unittest.TestCase):
    def test_model_warnings(self):
        self.assertEqual(model_warnings("a", []), [])
        self.assertEqual(model_warnings("a", ["a"]), [])
        self.assertEqual(model_warnings("", ["a", "b"]), [])
        w = model_warnings("qwen-3.8-27b", ["qwen/qwen3.8-27b", "llama-3.3-70b-versatile"])
        self.assertEqual(len(w), 1)
        self.assertIn("qwen/qwen3.8-27b", w[0])
        w = model_warnings("zzzz", ["llama-3.3-70b-versatile"])
        self.assertIn("isn't on this provider", w[0])
        self.assertIn("Pick one", w[0])

    def test_learn_limit_roundtrip(self):
        s = FakeSettings()
        self.assertIsNone(learned_limit(s, "groq", "m"))
        learn_limit(s, "groq", "m", 8000)
        self.assertEqual(learned_limit(s, "groq", "m"), 8000)
        self.assertIsNone(learned_limit(s, "groq", "other"))

    def test_estimate_counts_tool_schemas(self):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        schemas = [{"name": "x", "description": "does a thing",
                    "input_schema": {"type": "object", "properties": {f"p{i}": {"type": "string"} for i in range(60)}}}]
        self.assertGreater(estimate_with_tools(msgs, "sys", schemas), estimate_tokens(msgs, "sys"))
        self.assertEqual(estimate_with_tools(msgs, "sys"), estimate_tokens(msgs, "sys"))


# ------------------------------------------------------------------ vision auto-retry
def _fake_stream(text: str = "OK"):
    class FakeStream:
        def __enter__(self):
            return iter([SimpleNamespace(usage=None, choices=[
                SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None), finish_reason="stop")])])

        def __exit__(self, *a):
            return False
    return FakeStream()


class VisionRetryTests(unittest.TestCase):
    def _provider(self):
        return OpenAICompatProvider({"id": "vp", "label": "V", "vision": True, "needs_key": False}, "m", "k")

    def test_image_rejection_retries_once_without_images(self):
        import httpx
        import openai
        p = self._provider()
        calls = []
        req = httpx.Request("POST", "http://x/v1/chat/completions")

        def create(**kw):
            calls.append(kw)
            if len(calls) == 1:
                raise openai.BadRequestError("messages[3].content must be a string",
                                             response=httpx.Response(400, request=req), body=None)
            return _fake_stream("OK")

        p._client = lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        msgs = [{"role": "user", "content": [{"type": "text", "text": "look"}, {"type": "image", "data": "AAAA"}]}]
        out = p.stream("sys", msgs, [], lambda _t: None, lambda: False)
        self.assertEqual(out.text, "OK")
        self.assertEqual(len(calls), 2)
        self.assertTrue(p.vision_stripped)
        self.assertFalse(p.vision)
        self.assertNotIn("image_url", json.dumps(calls[1]["messages"]))

    def test_image_rejection_that_survives_retry_maps_to_vision(self):
        import httpx
        import openai
        p = self._provider()
        req = httpx.Request("POST", "http://x/v1/chat/completions")

        def create(**kw):
            raise openai.BadRequestError("messages[3].content must be a string",
                                         response=httpx.Response(400, request=req), body=None)

        p._client = lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        msgs = [{"role": "user", "content": [{"type": "text", "text": "look"}, {"type": "image", "data": "AAAA"}]}]
        with self.assertRaises(ProviderError) as cm:
            p.stream("sys", msgs, [], lambda _t: None, lambda: False)
        self.assertEqual(cm.exception.kind, "vision")
        self.assertTrue(p.vision_stripped)   # still remembered, so the agent persists vision-off


# ------------------------------------------------------------------ engine-level guard
class RaisingProvider:
    vision = False
    model = "fake"
    profile = {"id": "fake", "label": "Fake"}

    def __init__(self, err: ProviderError | None = None, input_tokens: int = 0):
        self.err = err
        self.input_tokens = input_tokens
        self.calls = []

    def stream(self, system, messages, tools, on_text, should_stop, max_tokens=8192):
        self.calls.append({"system": system, "messages": messages, "tools": [t["name"] for t in tools]})
        if self.err:
            raise self.err
        r = LLMResult(text="done")
        r.input_tokens, r.output_tokens = self.input_tokens or 100, 20
        on_text("done")
        return r


def wait_for(cond, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class GuardEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["ZEROTHEBOT_HOME"] = tempfile.mkdtemp(prefix="zttest-guard-")
        cls.eng = Engine()
        cls.eng.settings.set("notifications.toast", False)
        cls.eng.settings.set("memory.auto_reflect", False)
        cls.eng.start()

    @classmethod
    def tearDownClass(cls):
        cls.eng.stop()

    def setUp(self):
        self.eng.settings.set("model_limits", {})
        self.eng.settings.set("providers", {"fake": {"label": "Fake", "kind": "openai", "base_url": "http://localhost:9/v1",
                                                     "model": "fake", "vision": True, "needs_key": False}})

    def use(self, provider):
        agent_mod.make_provider = lambda *a, **k: provider
        return provider

    def new_bot(self, name):
        bot = self.eng.bots.create(name, job="Test job")
        th = self.eng.threads.main_thread(bot["id"])
        return bot, th

    def run_turn(self, bot, th, text="go"):
        self.eng.send_user_message(th["id"], text)
        self.assertTrue(wait_for(lambda: not self.eng.turns.is_busy(bot["id"])), "turn did not finish")
        time.sleep(0.2)
        self.assertTrue(wait_for(lambda: not self.eng.turns.is_busy(bot["id"]), 10), "turn restarted after finishing")

    def notices(self, th):
        out = []
        for r in self.eng.threads.rows(th["id"]):
            if r["kind"] == "notice":
                for b in jload(r["content"], []) or []:
                    out.append((b.get("level", ""), b.get("text", "")))
        return out

    def test_size_error_learns_limit_and_explains(self):
        prov = self.use(RaisingProvider(ProviderError(
            "size", "Request too large. Limit: 8000, Requested: 12205.", limit=8000, requested=12205)))
        bot, th = self.new_bot("LearnBot")
        self.run_turn(bot, th)
        self.assertEqual(learned_limit(self.eng.settings, "fake", "fake"), 8000)
        errs = [t for lvl, t in self.notices(th) if lvl == "error"]
        self.assertTrue(any("Prompt too big" in t for t in errs), errs)
        self.assertTrue(any("bigger context window" in t for t in errs), errs)
        self.assertGreaterEqual(len(prov.calls), 1)

    def test_preflight_fails_fast_when_bot_cannot_fit(self):
        self.eng.settings.set("model_limits", {"fake:fake": 10})
        prov = self.use(RaisingProvider())
        bot, th = self.new_bot("NoFitBot")
        self.run_turn(bot, th)
        self.assertEqual(prov.calls, [], "must not call the API when the prompt cannot fit")
        errs = [t for lvl, t in self.notices(th) if lvl == "error"]
        self.assertTrue(any("Prompt too big" in t for t in errs), errs)
        self.assertTrue(any("10" in t for t in errs), errs)

    def test_usage_raises_a_stale_low_limit(self):
        self.eng.settings.set("model_limits", {"fake:fake": 5000})

        class _A:
            engine = self.eng

        agent_mod.AgentRun._learn_from_usage(_A, RaisingProvider(), 9000)
        self.assertEqual(learned_limit(self.eng.settings, "fake", "fake"), 9000)

    def test_usage_keeps_limit_when_none_stored(self):
        self.eng.settings.set("model_limits", {})

        class _A:
            engine = self.eng

        agent_mod.AgentRun._learn_from_usage(_A, RaisingProvider(), 9000)
        self.assertIsNone(learned_limit(self.eng.settings, "fake", "fake"))

    def test_vision_stripped_is_persisted_and_announced(self):
        prov = self.use(RaisingProvider())
        prov.vision_stripped = True
        bot, th = self.new_bot("VisionBot")
        self.run_turn(bot, th)
        self.assertFalse(self.eng.settings.profile("fake").get("vision", True))
        infos = [t for lvl, t in self.notices(th) if lvl == "info"]
        self.assertTrue(any("can see images" in t for t in infos), infos)
        # second turn: no duplicate announcement
        self.run_turn(bot, th)
        self.assertEqual(len([t for lvl, t in self.notices(th) if lvl == "info"]), len(infos))

    def test_normal_turn_unaffected_without_limits(self):
        prov = self.use(RaisingProvider())
        bot, th = self.new_bot("FineBot")
        self.run_turn(bot, th)
        self.assertEqual(len(prov.calls), 1)
        asst = [i for i in self.eng.threads.display(th["id"]) if i["type"] == "assistant"]
        self.assertTrue(asst)


class KwProvider(RaisingProvider):
    def __init__(self):
        super().__init__()
        self.max_tokens_seen = []

    def stream(self, system, messages, tools, on_text, should_stop, max_tokens=8192):
        self.max_tokens_seen.append(max_tokens)
        return super().stream(system, messages, tools, on_text, should_stop, max_tokens)


def _msg(role, *texts, tool_use=None, tool_result=None):
    content = [{"type": "text", "text": t} for t in texts]
    if tool_use:
        content.append({"type": "tool_use", "id": tool_use[0], "name": tool_use[1], "input": tool_use[2]})
    if tool_result:
        content.append({"type": "tool_result", "tool_use_id": tool_result[0],
                        "content": [{"type": "text", "text": tool_result[1]}], "is_error": False})
    return {"role": role, "content": content}


class CostControlTests(unittest.TestCase):
    def test_history_cap_passthrough_under_budget(self):
        hist = [_msg("user", "hi"), _msg("assistant", "hello")]
        out = agent_mod.AgentRun._enforce_history_cap(hist, 12000)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["content"][0]["text"], "hi")

    def test_history_cap_trims_old_blobs_keeps_pairs(self):
        big = "x" * 20000
        hist = [_msg("user", "first question"),
                _msg("assistant", "working", tool_use=("t1", "fs_list", {"path": "."})),
                _msg("user", "tool ran", tool_result=("t1", big)),
                _msg("user", "second question"),
                _msg("assistant", "on it", tool_use=("t2", "fs_read", {"path": "a"})),
                _msg("user", "file text", tool_result=("t2", "small")),
                _msg("user", "latest question"),
                _msg("assistant", "answering")]
        out = agent_mod.AgentRun._enforce_history_cap(hist, 3000, keep_last=4)
        from core.providers import estimate_tokens
        self.assertLessEqual(estimate_tokens(out), 3000)
        # original untouched (deep copy)
        self.assertEqual(len(hist[2]["content"][1]["content"][0]["text"]), 20000)
        # newest messages survive
        texts = [b.get("text", "") for m in out for b in m["content"] if b.get("type") == "text"]
        self.assertTrue(any("latest question" in t for t in texts))
        # no orphan tool_use: every tool_use id still has its tool_result
        uses = {b["id"] for m in out for b in m["content"] if b.get("type") == "tool_use"}
        res = {b["tool_use_id"] for m in out for b in m["content"] if b.get("type") == "tool_result"}
        self.assertTrue(uses <= res, (uses, res))

    def test_history_cap_drops_oldest_first(self):
        hist = [m for i in range(20) for m in (_msg("user", f"q{i}"), _msg("assistant", f"a{i} " + "y" * 700))]
        out = agent_mod.AgentRun._enforce_history_cap(hist, 2000, keep_last=4)
        from core.providers import estimate_tokens
        self.assertLessEqual(estimate_tokens(out), 2000)
        texts = " ".join(b.get("text", "") for m in out for b in m["content"] if b.get("type") == "text")
        self.assertNotIn("q0 ", texts)
        self.assertIn("q19", texts)

    def test_cheap_defaults(self):
        from core.settings import DEFAULTS
        self.assertEqual(DEFAULTS["defaults"]["step_limit"], 25)
        self.assertEqual(DEFAULTS["context"]["max_history_tokens"], 12000)
        self.assertEqual(DEFAULTS["context"]["max_tokens"], 4096)
        self.assertEqual(agent_mod.RETRIES["rate_limit"], 3)
        self.assertNotIn("quota", agent_mod.RETRIES)

    def test_max_tokens_forwarded_to_provider(self):
        os.environ["ZEROTHEBOT_HOME"] = tempfile.mkdtemp(prefix="zttest-cost-")
        eng = Engine()
        eng.settings.set("notifications.toast", False)
        eng.settings.set("memory.auto_reflect", False)
        eng.start()
        try:
            prov = KwProvider()
            old = agent_mod.make_provider
            agent_mod.make_provider = lambda *a, **k: prov
            try:
                bot = eng.bots.create("KwBot", job="Test job")
                th = eng.threads.main_thread(bot["id"])
                eng.send_user_message(th["id"], "go")
                self.assertTrue(wait_for(lambda: not eng.turns.is_busy(bot["id"])))
                self.assertEqual(prov.max_tokens_seen, [4096])
                eng.settings.set("context.max_tokens", 512)
                eng.send_user_message(th["id"], "go again")
                self.assertTrue(wait_for(lambda: not eng.turns.is_busy(bot["id"])))
                self.assertEqual(prov.max_tokens_seen[-1], 512)
            finally:
                agent_mod.make_provider = old
        finally:
            eng.stop()


if __name__ == "__main__":
    unittest.main()
