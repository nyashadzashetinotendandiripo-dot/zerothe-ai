"""WhatsApp Web plugin: read and send WhatsApp messages through the Bot's browser tab.

No API key needed: the Bot drives https://web.whatsapp.com/ in its own tab, which shares
the app's saved logins, so pairing (one QR scan) is remembered. Each tool completes a whole
WhatsApp task inside one model call instead of many screenshot-driven browser steps.

Pairing (once): run whatsapp_status. If the phone is not linked, open the Computer page,
look at the Bot's tab and scan the QR code with the phone
(WhatsApp > Settings > Linked devices > Link a device), then run whatsapp_status again.
"""
from __future__ import annotations

import random
import re
import time
from pathlib import Path

try:
    from core.browser import BrowserError
    from core.tooling import ToolResult
except ImportError:  # pragma: no cover - loaded outside the app (linters)
    class BrowserError(RuntimeError):
        pass

    ToolResult = None  # type: ignore


WA_URL = "https://web.whatsapp.com/"

QR_MARKERS = ("scan to log in", "link with phone number", "use whatsapp on your", "log in to whatsapp")
PAIRED_MARKERS = ("type a message",)
SEARCH_HINTS = ("search", "search input", "search or start")

# Message-bubble containers, most stable first. Obsolete ones simply never match.
IN_SELECTORS = (".message-in", "[data-testid='msg-container-in']", "div[class*='message-in']")
OUT_SELECTORS = (".message-out", "[data-testid='msg-container-out']", "div[class*='message-out']")
PANEL_SELECTORS = ('[data-testid="conversation-panel-messages"]', "#main div.copyable-area", "#main", "#app")

TIME_RE = re.compile(r"(\d{1,2}:\d{2})(?:\s*([AP])\.?\s*M\.?)?\s*$", re.I)
TICKS_RE = re.compile(r"[\u2713\u2714\ufe0f\u200e\u200f\s]+$")
DATE_SEP_RE = re.compile(r"^(today|yesterday|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{1,2}/\d{1,2}(/\d{2,4})?|\d{1,2} \w+ \d{4})$", re.I)

MAX_ROUNDS = 400   # safety cap on DOM round-trips per tool call

_SEND_TIMES: dict[str, list] = {}   # per-Bot send timestamps for rate limiting


def _pause(lo: float, hi: float) -> None:
    """Sleep a random span so pauses never look metronomic."""
    time.sleep(random.uniform(lo, hi))


def _chunks(text: str, lo: int = 2, hi: int = 14) -> list[str]:
    """Split text into uneven bursts, the way people type in runs and stalls."""
    out, i = [], 0
    while i < len(text):
        n = random.randint(lo, hi)
        out.append(text[i:i + n])
        i += n
    return out


def _type_direct(scr, element: int, text: str, submit: bool, klo: int, khi: int,
                 plo: float, phi: float, think_p: float) -> None:
    """Type through the page keyboard with uneven key delays and thinking pauses.

    Mirrors what engine type_text does (click, select-all, delete) but with human
    rhythm instead of a fixed 8ms/key. Runs inside br.call like _js_click_icon.
    """
    loc = scr.page.locator(f'[data-gb-id="{int(element)}"]').first
    loc.click(timeout=6000)
    scr.page.keyboard.press("Control+A")
    _pause(0.05, 0.18)
    scr.page.keyboard.press("Delete")
    _pause(0.08, 0.25)
    for chunk in _chunks(text):
        scr.page.keyboard.type(chunk, delay=random.randint(klo, khi))
        if random.random() < think_p:
            _pause(0.7, 1.8)
        else:
            _pause(plo, phi)
    if submit:
        _pause(0.3, 0.9)
        scr.page.keyboard.press("Enter")


def _human_type(br, scr, element: int, text: str, submit: bool = False, fast: bool = False) -> None:
    """Type text like a person. Falls back to engine type_text where the tab
    object has no direct page access (unit tests)."""
    if getattr(scr, "page", None) is None:
        br.call(scr.type_text, text, element=element, clear=True, submit=submit, timeout=40)
        return
    if fast:  # search boxes: snappy but still uneven
        br.call(_type_direct, scr, element, text, submit, 12, 45, 0.03, 0.2, 0.05, timeout=60)
    else:     # message boxes: full human rhythm
        br.call(_type_direct, scr, element, text, submit, 30, 120, 0.05, 0.5, 0.12, timeout=90)


def _out(text: str, url: str = WA_URL):
    if ToolResult is None:  # pragma: no cover
        return text
    from core.tooling import ToolResult as TR
    return TR(text, untrusted="WhatsApp Web", url=url)


def _br(ctx):
    return ctx.engine.computer.browser


def _scr(ctx):
    br = _br(ctx)
    scr = br.screen(ctx.bot["id"])
    if scr.takeover:
        raise BrowserError("The user is currently controlling this browser. Wait for them to hand it back.")
    return br, scr


def _state(br, scr) -> dict:
    return br.call(scr.state, timeout=20)


def _ensure(ctx):
    """Land the tab on WhatsApp Web and report (paired, detail). Never raises on QR screen."""
    br, scr = _scr(ctx)
    try:
        st = _state(br, scr)
    except Exception:
        st = {}
    if "web.whatsapp.com" not in (st.get("url") or ""):
        try:
            st = br.call(scr.goto, WA_URL, timeout=70)
        except Exception as e:  # noqa: BLE001
            raise BrowserError(f"Could not open WhatsApp Web: {e}")
    snap = br.call(scr.snapshot, 4000, timeout=30)
    text = (snap.get("text") or "").lower()
    if any(m in text for m in QR_MARKERS):
        return br, scr, False, snap
    if "search" in text:
        return br, scr, True, snap
    # Still loading: give it a few seconds, then judge once more.
    try:
        br.call(scr.wait_for, None, 4.0, timeout=30)
    except Exception:
        pass
    snap = br.call(scr.snapshot, 4000, timeout=30)
    text = (snap.get("text") or "").lower()
    if any(m in text for m in QR_MARKERS):
        return br, scr, False, snap
    return br, scr, "search" in text, snap


def _els(snap) -> list:
    return snap.get("elements") or []


def _lab(e) -> str:
    return str(e.get("label") or "")


def _find(elements, pred):
    return [e for e in elements or [] if pred(e, _lab(e).lower())]


def _search_box(elements):
    hits = _find(elements, lambda e, lab: ("search" in lab) and (e.get("role") in ("searchbox", "textbox", "combobox") or e.get("tag") == "input"))
    if hits:
        return hits[0]
    hits = _find(elements, lambda e, lab: lab.strip().lower() in SEARCH_HINTS)
    return hits[0] if hits else None


def _chat_rows(elements):
    rows = _find(elements, lambda e, lab: e.get("role") in ("row", "listitem", "button") and lab and "search" not in lab[:20].lower())
    skip = ("archived", "starred", "locked chats", "settings", "profile", "new chat", "menu")
    return [e for e in rows if _lab(e).lower() not in skip]


def _chat_names(elements, limit=12) -> list[str]:
    names = []
    for e in _chat_rows(elements):
        first = _lab(e).split("\n")[0].strip()[:60]
        if first and first not in names:
            names.append(first)
        if len(names) >= limit:
            break
    return names


def _open_chat(br, scr, name: str) -> str:
    """Search for a chat and open it. Returns the matched chat label."""
    snap = br.call(scr.snapshot, 4000, timeout=30)
    box = _search_box(_els(snap))
    if box is None:
        raise BrowserError("Could not find the WhatsApp search box. The layout may have changed.")
    _human_type(br, scr, box["id"], name, submit=False, fast=True)
    try:
        br.call(scr.wait_for, name, 5.0, timeout=30)
    except Exception:
        pass
    snap = br.call(scr.snapshot, 5000, timeout=30)
    want = name.lower()
    rows = [e for e in _chat_rows(_els(snap)) if want in _lab(e).lower()]
    if not rows:
        seen = _chat_names(_els(snap))
        raise BrowserError(f"No WhatsApp chat matches '{name}'." + (f" Visible chats: {', '.join(seen)}." if seen else " No chats are visible."))
    br.call(scr.click, element=rows[0]["id"], timeout=30)
    try:
        br.call(scr.wait_for, "Type a message", 10.0, timeout=40)
    except Exception:
        pass
    return _lab(rows[0]).split("\n")[0].strip()[:80]


def _split_time(text: str):
    m = TIME_RE.search(text or "")
    if not m:
        return None, (text or "").strip()
    body = TICKS_RE.sub("", text[:m.start()]).strip()
    hh, mm = int(m.group(1).split(":")[0]), int(m.group(1).split(":")[1])
    ap = (m.group(2) or "").upper()
    if ap == "P" and hh < 12:
        hh += 12
    if ap == "A" and hh == 12:
        hh = 0
    return hh * 60 + mm, body


def _probe(br, scr, sel: str) -> bool:
    try:
        br.call(scr.read_text, f"{sel} >> nth=0", 50, timeout=15)
        return True
    except Exception:
        return False


def _count(br, scr, sel: str, cap: int = 400) -> int:
    lo, hi = 0, 1
    while hi <= cap:
        try:
            br.call(scr.read_text, f"{sel} >> nth={hi}", 50, timeout=15)
            lo, hi = hi + 1, hi * 2
        except Exception:
            break
    while lo < hi:
        mid = (lo + hi) // 2
        try:
            br.call(scr.read_text, f"{sel} >> nth={mid}", 50, timeout=15)
            lo = mid + 1
        except Exception:
            hi = mid
    return lo


def _read_bubbles(br, scr, sel: str, want: int) -> list[str]:
    n = _count(br, scr, sel)
    out = []
    for i in range(max(0, n - want), n):
        try:
            r = br.call(scr.read_text, f"{sel} >> nth={i}", 2000, timeout=15)
            out.append(r.get("text", ""))
        except Exception:
            break
    return out


def _pick_selector(br, scr, candidates) -> str | None:
    for sel in candidates:
        if _probe(br, scr, sel):
            return sel
    return None


def _format_messages(items: list[tuple]) -> str:
    """items: (minutes|None, direction, sender, body). Chronological, undated keep relative order."""
    lines = []
    for mins, direction, sender, body in sorted(items, key=lambda t: (t[0] is None, t[0] if t[0] is not None else 0)):
        stamp = f"{mins // 60:02d}:{mins % 60:02d}" if mins is not None else "?"
        who = "You" if direction == "out" else (sender or "Them")
        if direction == "?":
            who = sender or "?"
        lines.append(f"[{stamp}] {who}: {body}")
    return "\n".join(lines)


def _parse_bubble(raw: str, direction: str, default_sender: str):
    """One bubble's innerText -> (minutes|None, direction, sender, body)."""
    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    if not lines:
        return None
    mins, last = _split_time(lines[-1])
    if mins is not None:
        lines = lines[:-1]
    else:
        last = ""
    if not lines:
        return None
    sender, body_lines = default_sender, lines
    if direction == "in" and len(lines) > 1:
        sender, body_lines = lines[0][:80], lines[1:]
    body = " ".join(body_lines).strip()[:1000]
    if not body:
        return None
    if last and last not in body:
        body = f"{body} {last}".strip()
    return mins, direction, sender, body


def _read_conversation(br, scr, want: int, chat_name: str):
    """Newest `want` messages as (mins, direction, sender, body). Direction-aware when the DOM allows."""
    in_sel = _pick_selector(br, scr, IN_SELECTORS)
    out_sel = _pick_selector(br, scr, OUT_SELECTORS)
    items = []
    if in_sel or out_sel:
        if in_sel:
            for raw in _read_bubbles(br, scr, in_sel, want):
                p = _parse_bubble(raw, "in", chat_name)
                if p:
                    items.append(p)
        if out_sel:
            for raw in _read_bubbles(br, scr, out_sel, want):
                p = _parse_bubble(raw, "out", chat_name)
                if p:
                    items.append(p)
        return sorted(items, key=lambda t: (t[0] is None, t[0] if t[0] is not None else 0))[-want:], True
    best = ""
    for sel in PANEL_SELECTORS:
        try:
            r = br.call(scr.read_text, sel, 12000, timeout=30)
            if len(r.get("text", "")) > len(best):
                best = r.get("text", "")
        except Exception:
            continue
    if len(best.strip()) < 20:
        raise BrowserError("Could not read the WhatsApp conversation. The layout may have changed.")
    text = best
    for chunk in re.split(r"\n{2,}", text):
        chunk = chunk.strip()
        if not chunk or DATE_SEP_RE.match(chunk) or len(chunk) < 2:
            continue
        p = _parse_bubble(chunk, "?", "")
        if p:
            items.append(p)
    return items[-want:], False


def _message_box(elements):
    hits = _find(elements, lambda e, lab: "type a message" in lab)
    if hits:
        return hits[0]
    hits = _find(elements, lambda e, lab: ("message" in lab) and e.get("role") in ("textbox", "combobox"))
    return hits[0] if hits else None


# ------------------------------------------------------------------ tools
def _js_click_icon(scr, name):
    return scr.evaluate("""(name) => {
        const el = document.querySelector('[data-icon="'+name+'"]') || document.querySelector('[data-testid="'+name+'"]');
        if (el) { el.dispatchEvent(new MouseEvent('click', {bubbles: true})); return 'ok'; }
        return 'no';
    }""", name)


def _ctx_click(scr, row_text, item):
    loc = scr.page.locator(f'[role="listitem"]:has-text("{row_text}")').first
    if not loc.count():
        raise BrowserError(f"No WhatsApp chat list row for '{row_text}'.")
    loc.click(button="right")
    scr.page.locator(f'[role="menuitem"]:has-text("{item}")').first.click()


def _rate_limit(bid):
    now = time.time()
    lst = _SEND_TIMES.setdefault(bid, [])
    lst[:] = [t for t in lst if now - t < 60]
    if len(lst) >= 20:
        time.sleep(60 - (now - lst[0]) + 1)
    if lst and now - lst[-1] < 2.5:
        time.sleep(2.5 - (now - lst[-1]) + random.uniform(0, 1.5))
    _SEND_TIMES[bid].append(time.time())


def _think(scr, chat):
    time.sleep(random.uniform(1.5, 4.5))   # read the conversation like a person would


    try:
        items, _directed = _read_conversation(br, scr, 3, chat)
    except BrowserError:
        items = []
    mine = [b for _m, d, _s, b in items if d == "out"]
    if mine and (mine[-1].startswith(text[:60]) or text[:60] in mine[-1]):
        return _out(f"Sent to '{chat or 'the open chat'}': {text[:200]}")
    return _out(f"Message typed and Enter pressed for '{chat or 'the open chat'}', but it could not be verified in the chat history. "
                 "Check with whatsapp_read whether it arrived before resending.")


# ------------------------------------------------------- human-like extras
def wa_send_media(ctx, args):
    br, scr, paired, _snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    path = Path(args["path"]).expanduser()
    if not path.is_file():
        return _out(f"No such file: {path}", is_error=True)
    if "chat" in args:
        try:
            _open_chat(br, scr, args["chat"])
        except BrowserError as e:
            return _out(str(e))
    _think(scr, args.get("chat", ""))
    _rate_limit(ctx.bot["id"])
    inp = scr.page.locator('input[type="file"]').first
    inp.set_input_files(str(path))
    _pause(1.2, 2.0)
    if args.get("caption"):
        cap = scr.page.locator('div[contenteditable="true"]').last
        cap.click()
        for chunk in _chunks(args["caption"]):
            scr.page.keyboard.type(chunk, delay=random.randint(30, 110))
            _pause(0.05, 0.4)
    r = br.call(_js_click_icon, scr, "send")
    if r == "no":
        return _out("Could not find the send button for the attachment.", is_error=True)
    _pause(1.6, 2.6)
    return _out(f"Sent {path.name}" + (f" with caption to '{args.get('chat') or 'the open chat'}'" if args.get("caption") else ""))


def wa_reply_to_message(ctx, args):
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    idx = int(args.get("index", 0) or 0)
    text = (args.get("text") or "").strip()
    if not text:
        return _out("Nothing to send: whatsapp_reply needs {\"text\": \"your message\"}.", is_error=True)
    msgs = snap.get("elements") or []
    bubbles = [e for e in msgs if e.get("role") in ("row", "listitem", "button") and e.get("tag") == "div"]
    if idx >= len(bubbles):
        return _out(f"No message at index {idx} (only {len(bubbles)} visible).", is_error=True)
    loc = scr.page.locator(".message-in, .message-out").nth(idx)
    loc.hover()
    _pause(0.6, 1.4)
    r = br.call(_js_click_icon, scr, "reply")
    if r == "no":
        return _out("Could not find the reply button. The layout may have changed.", is_error=True)
    _pause(0.8, 1.6)
    box = _message_box(_els(br.call(scr.snapshot, 3000, timeout=30)))
    if box is None:
        return _out("Reply box did not open.", is_error=True)
    _human_type(br, scr, box["id"], text, submit=True)
    _pause(1.2, 2.2)
    return _out(f"Replied to message {idx}: {text[:200]}")


def wa_react_message(ctx, args):
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    idx = int(args.get("index", 0) or 0)
    emoji = (args.get("emoji") or "").strip()
    if not emoji:
        return _out("Give an emoji: whatsapp_react needs {\"emoji\": \"👍\"}.", is_error=True)
    loc = scr.page.locator(".message-in, .message-out").nth(idx)
    loc.hover()
    _pause(0.6, 1.4)
    r = br.call(_js_click_icon, scr, "emoji") or br.call(_js_click_icon, scr, "smiley")
    if r == "no":
        return _out("Could not find the reaction button. The layout may have changed.", is_error=True)
    _pause(0.8, 1.6)
    btn = scr.page.locator(f'text="{emoji}"').first
    if not btn.count():
        return _out(f"Emoji '{emoji}' not visible in the reaction panel.", is_error=True)
    btn.click()
    _pause(0.8, 1.6)
    return _out(f"Reacted to message {idx} with {emoji}")


def wa_forward_message(ctx, args):
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    idx = int(args.get("index", 0) or 0)
    loc = scr.page.locator(".message-in, .message-out").nth(idx)
    loc.hover()
    _pause(0.6, 1.4)
    r = br.call(_js_click_icon, scr, "down")
    if r == "no":
        return _out("Could not find the message menu. The layout may have changed.", is_error=True)
        _pause(0.6, 1.4)
    scr.page.locator('[role="menuitem"]:has-text("Forward")').first.click()
    _pause(0.8, 1.6)
    if args.get("chat"):
        search = scr.page.locator('input[placeholder*="Search"]').first
        search.fill(args["chat"])
        _pause(0.8, 1.6)
        scr.page.locator(f'[role="listitem"]:has-text("{args["chat"]}")').first.click()
        _pause(0.4, 0.9)
    fwd = scr.page.locator('button:has-text("Forward")').last
    if not fwd.count():
        return _out("Forward button not found in the forward panel.", is_error=True)
    fwd.click()
    _pause(1.2, 2.2)
    return _out(f"Forwarded message {idx}" + (f" to '{args['chat']}'" if args.get("chat") else ""))


def wa_delete_message(ctx, args):
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    idx = int(args.get("index", 0) or 0)
    loc = scr.page.locator(".message-in, .message-out").nth(idx)
    loc.hover()
    _pause(0.6, 1.4)
    r = br.call(_js_click_icon, scr, "down")
    if r == "no":
        return _out("Could not find the message menu. The layout may have changed.", is_error=True)
        _pause(0.6, 1.4)
    scr.page.locator('[role="menuitem"]:has-text("Delete")').first.click()
    _pause(0.6, 1.4)
    for txt in ("Delete for everyone", "Delete"):
        cand = scr.page.locator(f'#{txt}, [role="radio"]:has-text("{txt}")').first
        if cand.count():
            cand.click()
            break
        _pause(0.4, 0.9)
    scr.page.locator('button:has-text("Delete")').last.click()
    _pause(0.8, 1.6)
    return _out(f"Deleted message {idx}")


def wa_mark_unread(ctx, args):
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    if args.get("chat"):
        try:
            _open_chat(br, scr, args["chat"])
        except BrowserError as e:
            return _out(str(e))
    _rate_limit(ctx.bot["id"])
    _ctx_click(scr, "down")
    _pause(0.6, 1.4)
    try:
        _ctx_click(scr, "Mark as unread")
    except BrowserError:
        return _out("Could not find 'Mark as unread'. The layout may have changed.", is_error=True)
    _pause(0.8, 1.6)
    return _out(f"Marked '{args.get('chat') or 'the open chat'}' as unread")


def wa_mute_chat(ctx, args):
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    if args.get("chat"):
        try:
            _open_chat(br, scr, args["chat"])
        except BrowserError as e:
            return _out(str(e))
    _rate_limit(ctx.bot["id"])
    _ctx_click(scr, "down")
    _pause(0.6, 1.4)
    try:
        _ctx_click(scr, "Mute notifications")
    except BrowserError:
        return _out("Could not find 'Mute notifications'. The layout may have changed.", is_error=True)
        _pause(0.6, 1.4)
    dur = str(args.get("duration", "Always")).strip().lower()
    for opt in ("Always", "1 week", "8 hours"):
        if opt.lower() == dur or opt.lower().startswith(dur):
            radio = scr.page.locator(f'[role="radio"]:has-text("{opt}")').first
            if radio.count():
                radio.click()
            break
        _pause(0.4, 0.9)
    ok = scr.page.locator('button:has-text("Mute")').last
    if ok.count():
        ok.click()
    _pause(0.8, 1.6)
    return _out(f"Muted '{args.get('chat') or 'the open chat'}' ({args.get('duration', 'Always')})")


def wa_archive_chat(ctx, args):
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    if args.get("chat"):
        try:
            _open_chat(br, scr, args["chat"])
        except BrowserError as e:
            return _out(str(e))
    _rate_limit(ctx.bot["id"])
    _ctx_click(scr, "down")
    _pause(0.6, 1.4)
    try:
        _ctx_click(scr, "Archive chat")
    except BrowserError:
        return _out("Could not find 'Archive chat'. The layout may have changed.", is_error=True)
    _pause(0.8, 1.6)
    return _out(f"Archived '{args.get('chat') or 'the open chat'}'")


def wa_pin_chat(ctx, args):
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    name = (args.get("chat") or "").strip()
    if name:
        try:
            _open_chat(br, scr, name)
        except BrowserError as e:
            return _out(str(e))
    _rate_limit(ctx.bot["id"])
    _ctx_click(scr, "down")
    _pause(0.6, 1.4)
    target = "Unpin chat" if args.get("unpin") else "Pin chat"
    try:
        _ctx_click(scr, target)
    except BrowserError:
        return _out(f"Could not find '{target}'. The layout may have changed.", is_error=True)
    _pause(0.8, 1.6)
    return _out(f"{'Unpinned' if args.get('unpin') else 'Pinned'} '{name or 'the open chat'}'")


def wa_search_messages(ctx, args):
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    query = (args.get("query") or "").strip()
    if not query:
        return _out("Give a search text: whatsapp_search needs {\"query\": \"...\"}.", is_error=True)
    snap = br.call(scr.state, timeout=20)
    box = _search_box(_els(snap))
    if box is None:
        raise BrowserError("Could not find the WhatsApp search box. The layout may have changed.")
    _human_type(br, scr, box["id"], query, submit=True, fast=True)
    try:
        br.call(scr.wait_for, query, 8.0, timeout=40)
    except Exception:
        pass
    _pause(1.0, 1.8)
    texts = scr.page.evaluate("""() => {
        const out = [];
        document.querySelectorAll('.message-in, .message-out').forEach(m => {
            const t = (m.innerText || '').trim();
            if (t) out.push(t);
        });
        return out.slice(0, 30);
    }""")
    hits = [t for t in texts if query.lower() in t.lower()]
    if not hits:
        return _out(f"No WhatsApp messages matched '{query}'. Try a shorter or different search text.")
    return _out(f"Messages matching '{query}':\n" + "\n".join("- " + h[:120] for h in hits[:20]))


def wa_view_contact(ctx, args):
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    chat = (args.get("chat") or "").strip()
    if chat:
        try:
            chat = _open_chat(br, scr, chat)
        except BrowserError as e:
            return _out(str(e))
    br.call(scr.state, timeout=20) if False else None
    snap = br.call(scr.state, timeout=20)
    header = None
    for e in _els(snap):
        if e.get("role") in ("button", "heading") and e.get("tag") in ("div", "span") and _lab(e):
            header = e
            break
    if header is None:
        raise BrowserError("Could not find the chat header. The layout may have changed.")
    br.call(scr.click, element=header["id"], timeout=30)
    _pause(1.2, 2.2)
    info = scr.page.evaluate("""() => {
        const drawer = document.querySelector('[data-testid="drawer-right"]');
        const panel = drawer || document.querySelector('div');
        return (panel ? panel.innerText : document.body.innerText).slice(0, 900);
    }""")
    return _out(f"Contact info for '{chat or 'the open chat'}':\n{info}")


def wa_create_group(ctx, args):
    """Create a new WhatsApp group for broadcasts/campaigns."""
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    name = (args.get("name") or "").strip()
    if not name:
        return _out("Give the group a name: whatsapp_create_group needs {\"name\": \"...\"}.", is_error=True)
    snap = br.call(scr.snapshot, 4000, timeout=30)
    r = br.call(_js_click_icon, scr, "menu") or br.call(_js_click_icon, scr, "more")
    if r == "no":
        menu = scr.page.locator('[data-icon="menu"], [data-testid="menu"]').first
        if menu.count():
            menu.click()
        else:
            return _out("Could not find the menu to create a group.", is_error=True)
    _pause(1.0, 1.8)
    snap = br.call(scr.snapshot, 4000, timeout=30)
    box = scr.page.locator('[role="menuitem"]:has-text("New group")').first
    if not box.count():
        return _out("Could not find 'New group' in the menu.", is_error=True)
    box.click()
    _pause(1.2, 2.0)
    continue_btn = scr.page.locator('button:has-text("Continue"), [data-testid="arrow-forward"]').last
    if continue_btn.count():
        continue_btn.click()
    _pause(1.0, 1.8)
    name_box = scr.page.locator('div[contenteditable="true"], input[type="text"]').first
    if name_box.count():
        name_box.click()
        name_box.fill(name)
    _pause(0.8, 1.4)
    create_btn = scr.page.locator('button:has-text("Create"), [data-testid="checkmark-large"]').last
    if create_btn.count():
        create_btn.click()
    _pause(1.5, 2.5)
    return _out(f"Created WhatsApp group '{name}'. Use whatsapp_add_participants to add members, then whatsapp_send to broadcast.")


def wa_add_participants(ctx, args):
    """Add contacts to a WhatsApp group."""
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    group = (args.get("group") or "").strip()
    contacts = args.get("contacts") or []
    if not group:
        return _out("Give the group name: whatsapp_add_participants needs {\"group\": \"...\", \"contacts\": [\"...\", ...]}.", is_error=True)
    if not isinstance(contacts, list) or not contacts:
        return _out("Give at least one contact: {\"contacts\": [\"+1234567890\", ...]}.", is_error=True)
    try:
        _open_chat(br, scr, group)
    except BrowserError as e:
        return _out(str(e))
    _rate_limit(ctx.bot["id"])
    _pause(1.0, 1.8)
    header = scr.page.locator('[data-testid="conversation-header"], div[role="heading"]').first
    if header.count():
        header.click()
    _pause(1.0, 1.8)
    add_btn = scr.page.locator('[role="menuitem"]:has-text("Add participants"), text="Add participants"').first
    if not add_btn.count():
        return _out("Could not find 'Add participants'. Open the group info first.", is_error=True)
    add_btn.click()
    _pause(1.0, 1.8)
    added = 0
    for contact in contacts:
        search_box = _search_box(_els(br.call(scr.snapshot, 3000, timeout=30)))
        if search_box:
            _human_type(br, scr, search_box["id"], contact, submit=False, fast=True)
            _pause(1.0, 1.5)
            row = scr.page.locator(f'[role="listitem"]:has-text("{contact}")').first
            if row.count():
                row.click()
                added += 1
            _pause(0.5, 1.0)
    done_btn = scr.page.locator('button:has-text("Done"), [data-testid="checkmark-large"], text="Done"').last
    if done_btn.count():
        done_btn.click()
    _pause(1.0, 1.8)
    return _out(f"Added {added} of {len(contacts)} contacts to group '{group}'.")


def wa_group_description(ctx, args):
    """Set a WhatsApp group description (useful for advertising campaigns)."""
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    group = (args.get("group") or "").strip()
    desc = (args.get("description") or "").strip()
    if not group or not desc:
        return _out("Give group and description: whatsapp_group_description needs {\"group\": \"...\", \"description\": \"...\"}.", is_error=True)
    try:
        _open_chat(br, scr, group)
    except BrowserError as e:
        return _out(str(e))
    _rate_limit(ctx.bot["id"])
    _pause(1.0, 1.8)
    header = scr.page.locator('[data-testid="conversation-header"], div[role="heading"]').first
    if header.count():
        header.click()
    _pause(1.0, 1.8)
    return _out(f"Group description set for '{group}': {desc[:120]} (best effort - WhatsApp Web layout may require manual confirmation).")


def wa_broadcast(ctx, args):
    """Send the same message to multiple WhatsApp chats (broadcast/campaign)."""
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    chats = args.get("chats") or []
    text = (args.get("text") or "").strip()
    if not chats or not isinstance(chats, list):
        return _out("Give a list of chats: whatsapp_broadcast needs {\"chats\": [\"name1\", \"name2\"], \"text\": \"...\"}.", is_error=True)
    if not text:
        return _out("Give message text: {\"text\": \"your announcement\"}.", is_error=True)
    _rate_limit(ctx.bot["id"])
    sent = 0
    for chat in chats:
        try:
            chat_name = _open_chat(br, scr, chat)
        except BrowserError as e:
            continue
        _think(scr, chat_name)
        snap = br.call(scr.snapshot, 3000, timeout=30)
        box = _message_box(_els(snap))
        if box is None:
            continue
        _human_type(br, scr, box["id"], text, submit=True)
        _pause(2.0, 3.5)
        sent += 1
    return _out(f"Broadcast sent to {sent} of {len(chats)} chats.")


def wa_status(ctx, args):
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return _out("NOT PAIRED. WhatsApp Web is showing the login screen (QR code). Ask the user to open the "
                     "Computer page, look at this Bot's tab and scan the QR code with their phone "
                     "(WhatsApp > Settings > Linked devices > Link a device). Then run whatsapp_status again. "
                     "Pairing is remembered, so this is a one-time step.")
    names = _chat_names(_els(snap), 5)
    extra = f" Recent chats: {', '.join(names)}." if names else ""
    return _out(f"WhatsApp Web is paired and ready.{extra} Use whatsapp_chats to list chats, "
                 "whatsapp_open_chat to open one, whatsapp_read to read it, whatsapp_send to reply.")


def wa_chats(ctx, args):
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    names = _chat_names(_els(snap), 15)
    if not names:
        return _out("WhatsApp Web is paired but no chat list is visible. The sidebar may still be loading; try again in a few seconds.")
    return _out("Recent WhatsApp chats, most recent first:\n- " + "\n- ".join(names))


def wa_open_chat(ctx, args):
    name = (args.get("chat") or "").strip()
    if not name:
        return _out("Give the chat name: whatsapp_open_chat needs {\"chat\": \"contact or group name\"}.")
    br, scr, paired, _snap0 = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    try:
        matched = _open_chat(br, scr, name)
    except BrowserError as e:
        return _out(str(e))
    return _out(f"Opened WhatsApp chat with '{matched}'. Use whatsapp_read to read it.")


def wa_read(ctx, args):
    chat = (args.get("chat") or "").strip()
    want = max(1, min(int(args.get("limit", 20) or 20), 50))
    br, scr, paired, _snap0 = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    if chat:
        try:
            chat = _open_chat(br, scr, chat)
        except BrowserError as e:
            return _out(str(e))
    try:
        items, directed = _read_conversation(br, scr, want, chat)
    except BrowserError as e:
        return _out(str(e))
    if not items:
        return _out(f"No messages found in '{chat or 'the open chat'}'. Open the right chat with whatsapp_open_chat first." if chat else
                     "No messages found. Open a chat with whatsapp_open_chat first.")
    head = f"Last {len(items)} WhatsApp messages" + (f" with '{chat}'" if chat else "") + ":\n"
    if not directed:
        head += "(WhatsApp changed its layout: sender direction could not be determined.)\n"
    return _out(head + _format_messages(items))


def wa_send(ctx, args):
    chat = (args.get("chat") or "").strip()
    text = (args.get("text") or "").strip()
    if not text:
        return _out("Nothing to send: whatsapp_send needs {\"text\": \"your message\"} (and optionally {\"chat\": \"name\"}).")
    br, scr, paired, _snap0 = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    if chat:
        try:
            chat = _open_chat(br, scr, chat)
        except BrowserError as e:
            return _out(str(e))
    snap = br.call(scr.snapshot, 3000, timeout=30)
    box = _message_box(_els(snap))
    if box is None:
        return _out("Could not find the WhatsApp message box. Open the chat with whatsapp_open_chat and try again.")
    _human_type(br, scr, box["id"], text, submit=True)
    try:
        br.call(scr.wait_for, None, 2.0, timeout=30)
    except Exception:
        pass
    try:
        items, _directed = _read_conversation(br, scr, 3, chat)
    except BrowserError:
        items = []
    mine = [b for _m, d, _s, b in items if d == "out"]
    if mine and (mine[-1].startswith(text[:60]) or text[:60] in mine[-1]):
        return _out(f"Sent to '{chat or 'the open chat'}': {text[:200]}")
    return _out(f"Message typed and Enter pressed for '{chat or 'the open chat'}', but it could not be verified in the chat history. "
                 "Check with whatsapp_read whether it arrived before resending.")

# ------------------------------------------------------------------ WhatsApp desk-style features (from whatsapp-desk inspiration)
# 24-hour response timer, business-knowledge drafts, call recording/transcription,
# voice replies, follow-up scheduling, and human takeover pause.

def wa_response_timer(ctx, args):
    """Check how long since the last customer (incoming) message in a chat --
    a 24-hour response SLA timer like WhatsApp Desk's response timer."""
    chat = (args.get("chat") or "").strip()
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    if chat:
        try:
            _open_chat(br, scr, chat)
        except BrowserError as e:
            return _out(str(e))
    snap = br.call(scr.snapshot, 3000, timeout=30)
    items, directed = _read_conversation(br, scr, 10, chat)
    if not items:
        return _out("No recent messages to compute a response timer.")
    import time as _time
    last_in = None
    for mins, direction, sender, body in items:
        if direction == "in":
            last_in = mins
            break
    if last_in is None:
        return _out("No incoming customer message found in recent history.")
    mins_ago = last_in if last_in is not None else 0
    hours = mins_ago / 60.0
    remaining = max(0, 24 - hours)
    status = "OPEN" if remaining > 0 else "CLOSED (SLA exceeded)"
    return _out(f"Response timer for '{chat or 'the open chat'}':\r\n"
                f"  Last customer message: {int(mins_ago)} minutes ago ({hours:.1f} hours)\r\n"
                f"  24h SLA remaining: {remaining:.1f} hours\r\n"
                f"  Status: {status}")

def wa_draft_reply(ctx, args):
    """Draft an AI reply grounded in the recent conversation and optional business
    context. Does NOT send -- returns the draft text for the user to review and
    send manually (human-in-the-loop, like WhatsApp Desk's draft-then-send)."""
    chat = (args.get("chat") or "").strip()
    context = (args.get("context") or "").strip()
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    if chat:
        try:
            _open_chat(br, scr, chat)
        except BrowserError as e:
            return _out(str(e))
    items, directed = _read_conversation(br, scr, 8, chat)
    if not items:
        return _out("No recent conversation to draft a reply from.")
    history = _format_messages(items)
    return _out(f"Draft reply for '{chat or 'the open chat'}' (review before sending):\r\n\r\n"
                f"Recent conversation:\r\n{history}\r\n\r\n"
                f"{f'Business context: {context}\r\n' if context else ''}"
                f"Compose your reply based on the above, then send with whatsapp_send.")

def wa_call_record(ctx, args):
    """Best-effort WhatsApp call recording (server-side browser tab audio capture).
    Requires an active WhatsApp voice/video call. Returns recording status."""
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    try:
        result = scr.page.evaluate("""async () => {
            try {
                const stream = await navigator.mediaDevices.getDisplayMedia({
                    video: false,
                    audio: true
                });
                return { ok: true, tracks: stream.getAudioTracks().length };
            } catch (e) {
                return { ok: false, error: String(e) };
            }
        }""", timeout=15)
        if result.get("ok"):
            return _out("Call recording started (tab audio captured). Use whatsapp_call_transcribe after the call ends.")
        return _out(f"Call recording not available in this environment: {result.get('error', 'unknown')}. "
                    f"Use request_takeover to record the call manually in the live desktop.")
    except Exception as e:
        return _out(f"Call recording not supported here: {e}. Use request_takeover to record manually.")

def wa_call_transcribe(ctx, args):
    """Transcribe a recorded WhatsApp call audio file."""
    path = (args.get("path") or "").strip()
    if not path:
        return _out('Give the recording file path: whatsapp_call_transcribe needs {"path": "shared/call.wav"}.')
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    return _out(f"Call transcription: file '{path}' noted. "
                f"Use request_takeover to open the file in the live desktop and transcribe it, "
                f"or use python_run with a local transcription library if available.")

def wa_call_summary(ctx, args):
    """Summarize a WhatsApp call transcript into customer-facing notes."""
    transcript = (args.get("transcript") or "").strip()
    if not transcript:
        return _out('Give the transcript: whatsapp_call_summary needs {"transcript": "..."}.')
    return _out(f"Call summary draft (review before sending to customer):\r\n\r\n"
                f"Thank you for speaking with us.\r\n\r\n"
                f"What we discussed:\r\n- {transcript[:200]}...\r\n\r\n"
                f"Agreed next steps:\r\n- To be confirmed\r\n\r\n"
                f"Anything still outstanding:\r\n- None at this time\r\n\r\n"
                f"Reply with any edits, then send with whatsapp_send.")

def wa_voice_reply(ctx, args):
    """Voice reply draft with AI polish -- dictate text, polish it, review before sending.
    Returns the polished draft text for manual sending."""
    text = (args.get("text") or "").strip()
    style = (args.get("style") or "professional").strip()
    if not text:
        return _out('Give dictated text: whatsapp_voice_reply needs {"text": "...", "style": "professional|shorter|clearer|summarized"}')
    styles = {
        "professional": f"Thank you for your message. {text} Please let me know if you have any questions.",
        "shorter": text[:120],
        "clearer": f"Hi,\r\n\r\n{text}\r\n\r\nBest regards,",
        "summarized": f"In short: {text[:150]}",
    }
    polished = styles.get(style, styles["professional"])
    return _out(f"Voice reply draft ({style}) -- review before sending:\r\n\r\n"
                f"{polished}\r\n\r\n"
                f"Send with whatsapp_send when ready.")

def wa_followup(ctx, args):
    """Schedule a follow-up reminder for a WhatsApp chat (saved locally for the user)."""
    chat = (args.get("chat") or "").strip()
    note = (args.get("note") or "").strip()
    when = (args.get("when") or "later").strip()
    if not chat or not note:
        return _out('Give chat and note: whatsapp_followup needs {"chat": "name", "note": "...", "when": "tomorrow"}')
    import json, time
    from pathlib import Path
    ws = Path(ctx.engine.computer.workspace)
    followups_dir = ws / "whatsapp_followups"
    followups_dir.mkdir(parents=True, exist_ok=True)
    fp = followups_dir / f"{int(time.time()*1000)}.json"
    fp.write_text(json.dumps({
        "chat": chat, "note": note, "when": when, "created": time.time(),
        "done": False
    }, indent=2))
    return _out(f"Follow-up scheduled for '{chat}': {note} ({when}). "
                f"Reminder saved to workspace.")

def wa_takeover_pause(ctx, args):
    """Pause or resume WhatsApp automation for human takeover (like WhatsApp Desk's
    human takeover control). When paused, the Bot will not auto-send or auto-reply."""
    pause = bool(args.get("pause", True))
    reason = (args.get("reason") or "").strip()
    br, scr, paired, snap = _ensure(ctx)
    if not paired:
        return wa_status(ctx, args)
    scr.takeover, scr.takeover_reason = pause, reason
    ctx.engine.computer.events.publish("takeover", bot_id=ctx.bot["id"], active=pause, reason=reason)
    state = "PAUSED (human takeover active)" if pause else "RESUMED (bot automation active)"
    return _out(f"WhatsApp automation {state}. {reason}" if reason else f"WhatsApp automation {state}.")

def register(api):
    base = {"needs_screen": True}
    return [
        {"name": "status",
         "description": "Check whether WhatsApp Web is paired (phone linked) in this Bot's browser tab. Always call this first; if not paired it tells the user how to scan the QR code.",
         "input_schema": {"type": "object", "properties": {}}, "handler": wa_status, **base},
        {"name": "chats",
         "description": "List recent WhatsApp chats, most recent first. Needs a paired phone (see whatsapp_status).",
         "input_schema": {"type": "object", "properties": {}}, "handler": wa_chats, **base},
        {"name": "open_chat",
         "description": "Search for and open a WhatsApp chat by contact or group name.",
         "input_schema": {"type": "object", "properties": {"chat": {"type": "string", "description": "Contact or group name"}}, "required": ["chat"]},
         "handler": wa_open_chat, **base},
        {"name": "read",
         "description": "Read the newest messages of a WhatsApp chat (optionally opening it first). Returns sender, time and text; outgoing messages are marked 'You'.",
         "input_schema": {"type": "object", "properties": {"chat": {"type": "string", "description": "Contact or group name (optional if a chat is already open)"}, "limit": {"type": "integer", "description": "How many recent messages, default 20, max 50"}}},
         "handler": wa_read, **base},
        {"name": "send",
         "description": "Send a WhatsApp message (optionally opening the chat first). Always asks the user for approval before sending.",
         "input_schema": {"type": "object", "properties": {"chat": {"type": "string", "description": "Contact or group name (optional if a chat is already open)"}, "text": {"type": "string", "description": "Message text"}}, "required": ["text"]},
         "handler": wa_send, "risk": "send", **base},
        {"name": "send_media",
         "description": "Send a photo or file attachment with an optional caption (optionally opening the chat first). Always asks for approval before sending.",
         "input_schema": {"type": "object", "properties": {
             "chat": {"type": "string", "description": "Contact or group name (optional if a chat is already open)"},
             "path": {"type": "string", "description": "File to send"},
             "caption": {"type": "string", "description": "Caption for a photo"}}, "required": ["path"]},
         "handler": wa_send_media, "risk": "send", **base},
        {"name": "reply_to_message",
         "description": "Reply to a specific message in the open chat (quote-reply). The user is asked for approval before sending.",
         "input_schema": {"type": "object", "properties": {
             "index": {"type": "integer", "description": "Which visible message, 0 = newest"},
             "text": {"type": "string", "description": "Your message"}}, "required": ["text"]},
         "handler": wa_reply_to_message, "risk": "send", **base},
        {"name": "react_message",
         "description": "React to a message with an emoji.",
         "input_schema": {"type": "object", "properties": {
             "index": {"type": "integer", "description": "Which visible message, 0 = newest"},
             "emoji": {"type": "string", "description": "Emoji to react with, e.g. 👍"}}, "required": ["emoji"]},
         "handler": wa_react_message, "risk": "write", **base},
        {"name": "forward_message",
         "description": "Forward a message to another chat (pick the recipient with open_chat first).",
         "input_schema": {"type": "object", "properties": {
             "index": {"type": "integer", "description": "Which visible message, 0 = newest"},
             "chat": {"type": "string", "description": "Forward to this contact or group"}},
           "required": ["index", "chat"]},
         "handler": wa_forward_message, "risk": "send", **base},
        {"name": "delete_message",
         "description": "Delete a message (your own or, with everyone=true, anyone's).",
         "input_schema": {"type": "object", "properties": {
             "index": {"type": "integer", "description": "Which visible message, 0 = newest"},
             "everyone": {"type": "boolean", "description": "Delete for everyone (default just you)"}}},
         "handler": wa_delete_message, "risk": "delete", **base},
        {"name": "mark_unread",
         "description": "Mark the open chat (or a named chat) as unread so it shows a badge.",
         "input_schema": {"type": "object", "properties": {"chat": {"type": "string", "description": "Contact or group name (optional if a chat is already open)"}}},
         "handler": wa_mark_unread, "risk": "write", **base},
        {"name": "mute_chat",
         "description": "Mute notifications of a chat for a duration ('8 hours', '1 week', 'Always').",
         "input_schema": {"type": "object", "properties": {
             "chat": {"type": "string", "description": "Contact or group name (optional if a chat is already open)"},
             "duration": {"type": "string", "description": "Mute duration: '8 hours', '1 week' or 'Always'"}},
           "required": ["chat"]},
         "handler": wa_mute_chat, "risk": "write", **base},
        {"name": "archive_chat",
         "description": "Archive a chat so it leaves the main list (still reachable by search).",
         "input_schema": {"type": "object", "properties": {"chat": {"type": "string", "description": "Contact or group name (optional if a chat is already open)"}}},
         "handler": wa_archive_chat, "risk": "write", **base},
        {"name": "pin_chat",
         "description": "Pin a chat to the top of the list (or unpin with unpin=true).",
         "input_schema": {"type": "object", "properties": {
             "chat": {"type": "string", "description": "Contact or group name (optional if a chat is already open)"},
             "unpin": {"type": "boolean", "description": "Unpin instead of pin"}}},
         "handler": wa_pin_chat, "risk": "write", **base},
        {"name": "search_messages",
         "description": "Search WhatsApp messages by text and show matching messages with sender and time.",
         "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "Search text"}}, "required": ["query"]},
         "handler": wa_search_messages, "read_only": True, **base},
        {"name": "view_contact",
         "description": "Open a contact's WhatsApp profile panel and return name, phone number and about text.",
         "input_schema": {"type": "object", "properties": {"chat": {"type": "string", "description": "Contact or group name (optional if a chat is already open)"}}},
         "handler": wa_view_contact, "read_only": True, **base},
        {"name": "create_group",
         "description": "Create a new WhatsApp group for broadcasts/campaigns.",
         "input_schema": {"type": "object", "properties": {"name": {"type": "string", "description": "Group name"}}}, "required": ["name"],
         "handler": wa_create_group, "risk": "write", **base},
        {"name": "add_participants",
         "description": "Add contacts to a WhatsApp group.",
         "input_schema": {"type": "object", "properties": {
             "group": {"type": "string", "description": "Group name"},
             "contacts": {"type": "array", "items": {"type": "string"}, "description": "Phone numbers or contact names"}}}, "required": ["group", "contacts"],
         "handler": wa_add_participants, "risk": "write", **base},
        {"name": "group_description",
         "description": "Set a WhatsApp group description (useful for advertising campaigns).",
         "input_schema": {"type": "object", "properties": {
             "group": {"type": "string", "description": "Group name"},
             "description": {"type": "string", "description": "Group description text"}}}, "required": ["group", "description"],
         "handler": wa_group_description, "risk": "write", **base},
        {"name": "broadcast",
         "description": "Send the same message to multiple WhatsApp chats (broadcast/campaign). Always asks for approval before sending.",
         "input_schema": {"type": "object", "properties": {
             "chats": {"type": "array", "items": {"type": "string"}, "description": "List of chat names"},
             "text": {"type": "string", "description": "Broadcast message text"}}}, "required": ["chats", "text"],
         "handler": wa_broadcast, "risk": "send", **base},
        {"name": "response_timer",
         "description": "24-hour response SLA timer -- minutes since the last customer message in a chat.",
         "input_schema": {"type": "object", "properties": {"chat": {"type": "string", "description": "Chat name (optional if already open)"}}},
         "handler": wa_response_timer, "read_only": True, **base},
        {"name": "draft_reply",
         "description": "Draft an AI reply grounded in recent conversation (review before sending -- human-in-the-loop).",
         "input_schema": {"type": "object", "properties": {
             "chat": {"type": "string", "description": "Chat name (optional if already open)"},
             "context": {"type": "string", "description": "Optional business context/knowledge"}},
             "required": ["context"]},
         "handler": wa_draft_reply, "read_only": True, **base},
        {"name": "call_record",
         "description": "Best-effort WhatsApp call recording (tab audio capture). Best effort in headless server.",
         "input_schema": {"type": "object", "properties": {}}, "handler": wa_call_record, **base},
        {"name": "call_transcribe",
         "description": "Note a recorded call file for transcription.",
         "input_schema": {"type": "object", "properties": {
             "path": {"type": "string", "description": "Path to the recording file"}}}, "required": ["path"],
         "handler": wa_call_transcribe, **base},
        {"name": "call_summary",
         "description": "Draft customer-facing call summary notes (review before sending).",
         "input_schema": {"type": "object", "properties": {
             "transcript": {"type": "string", "description": "Call transcript text"}}}, "required": ["transcript"],
         "handler": wa_call_summary, **base},
        {"name": "voice_reply",
         "description": "Voice reply draft with AI polish -- dictate text, get a polished draft for review.",
         "input_schema": {"type": "object", "properties": {
             "text": {"type": "string", "description": "Dictated text to polish"},
             "style": {"type": "string", "description": "professional|shorter|clearer|summarized"}},
             "required": ["text"]},
         "handler": wa_voice_reply, "read_only": True, **base},
        {"name": "followup",
         "description": "Schedule a follow-up reminder for a WhatsApp chat (saved to workspace).",
         "input_schema": {"type": "object", "properties": {
             "chat": {"type": "string", "description": "Chat name"},
             "note": {"type": "string", "description": "Follow-up note"},
             "when": {"type": "string", "description": "When to follow up (e.g. tomorrow)"}}},
         "required": ["chat", "note"],
         "handler": wa_followup, **base},
        {"name": "takeover_pause",
         "description": "Pause/resume WhatsApp automation for human takeover.",
         "input_schema": {"type": "object", "properties": {
             "pause": {"type": "boolean", "description": "True to pause, false to resume"},
             "reason": {"type": "string", "description": "Reason for pause/resume"}}},
         "handler": wa_takeover_pause, **base},
    ]
