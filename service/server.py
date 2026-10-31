"""Local HTTP API used by the desktop app and the mobile PWA. Everything except /api/health, the
OAuth callback and the token-authenticated /api/hooks webhook requires the access token (Bearer
header, ?token= or the zt_token cookie)."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import queue
import re
import socket
import threading
import time
from urllib.parse import urlencode, quote
from pathlib import Path
from typing import Any

import httpx

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from core import APP_NAME, VERSION, backup as backup_mod, commands, packages, paths, secrets, skills as skills_mod
from core import auth as auth_mod
from core import host as host_mod
from core.browser import BrowserError
from core.bots import BotError
from core.computer import ComputerError
from core.engine import Engine
from core.files import FileError
from core.plugins import ConnectorError, google_client
from core.providers import ProviderError, friendly_error, make_provider, model_warnings
from core.routines import PRESETS
from core.secrets import SecretStoreError
from core.settings import PROVIDER_PRESETS
from core.templates import TEMPLATES

from .system_router import register as register_system_routes

SECRET_PREFIXES = ("provider:", "plugin:", "mcp:", "ntfy:")


def lan_addresses() -> list[str]:
    out = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in out:
                out.append(ip)
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        if not ip.startswith("127."):
            if ip in out:   # the address Windows uses to reach the network (Wi-Fi/LAN) must come first: it is the one phones can reach
                out.remove(ip)
            out.insert(0, ip)
    except OSError:
        pass
    return out


def create_app(engine: Engine, token: str) -> FastAPI:
    app = FastAPI(title=f"{APP_NAME} service", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    eng = engine
    fails: dict[str, list[float]] = {}

    # Sessions are signed with their own secret (kept in settings) so that rotating the
    # service token does not log everyone out and a leaked token cannot forge sessions.
    sess_sec = str(eng.settings.get("auth.session_secret") or "")
    if not sess_sec or len(sess_sec) < 32:
        sess_sec = os.urandom(32).hex()
        eng.settings.set("auth.session_secret", sess_sec)

    # ----------------------------------------------------------------- auth
    def presented(request: Request) -> str:
        h = request.headers.get("authorization", "")
        if h.lower().startswith("bearer "):
            return h[7:].strip()
        return request.query_params.get("token") or request.cookies.get("zt_token") or ""

    def check_token(tok: str, ip: str) -> bool:
        now = time.time()
        recent = [t for t in fails.get(ip, []) if now - t < 300]
        fails[ip] = recent
        if len(recent) >= 10:
            raise HTTPException(429, "Too many failed attempts. Wait a few minutes.")
        ok = bool(tok) and hmac.compare_digest(tok.encode(), token.encode())
        if not ok:
            fails[ip].append(now)
        return ok

    def auth(request: Request) -> None:
        # Signed-in session (email/password or Google) or the service token.
        if read_session(request.cookies):
            return
        tok = presented(request)
        if tok:
            if hmac.compare_digest(tok.encode(), token.encode()):
                return
            if session_email(tok):   # app may present its signed session as Bearer
                return
        raise HTTPException(401, "Sign in required.")

    def session_email(raw: str) -> str | None:
        """Email from a valid signed session string, else None."""
        if not raw or "." not in raw:
            return None
        payload, sig = raw.split(".", 1)
        want = hmac.new(sess_sec.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, want):
            return None
        try:
            d = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except (ValueError, TypeError):
            return None
        if d.get("exp", 0) < time.time():
            return None
        return d.get("email")

    def read_session(cookies) -> str | None:
        """Email from a valid signed session cookie, else None."""
        return session_email(cookies.get("zt_session") or "")

    def session_string(addr: str) -> str:
        """Build the signed session string (also stored as the zt_session cookie)."""
        payload = base64.urlsafe_b64encode(
            json.dumps({"email": addr, "exp": time.time() + 86400 * 30}).encode()).decode().rstrip("=")
        sig = hmac.new(sess_sec.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return payload + "." + sig

    def issue_session(response: Response, addr: str) -> None:
        response.set_cookie("zt_session", session_string(addr), httponly=True, samesite="strict", max_age=86400 * 30)

    api = Depends(auth)

    @app.exception_handler(ValueError)
    @app.exception_handler(BotError)
    @app.exception_handler(SecretStoreError)
    @app.exception_handler(ConnectorError)
    @app.exception_handler(ComputerError)
    @app.exception_handler(BrowserError)
    @app.exception_handler(ProviderError)
    async def _bad(_req: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=400)

    # --------------------------------------------------------------- basics
    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "name": APP_NAME, "version": VERSION}

    @app.post("/api/login")
    def login(request: Request, response: Response, body: dict = Body(...)) -> dict:
        ip = request.client.host if request.client else "?"
        addr = str(body.get("email", "")).strip().lower()
        cred = str(body.get("password", "") or body.get("token", "")).strip()
        if not auth_mod.verify_password(addr, cred):
            if not check_token(cred, ip):   # service token still works for the desktop/API
                raise HTTPException(401, "That email or password is not right.")
        issue_session(response, addr or auth_mod.email() or "owner")
        return {"ok": True, "email": addr or auth_mod.email(),
                "session": session_string(addr or auth_mod.email() or "owner")}

    @app.get("/api/auth_status")
    def auth_status() -> dict:
        return {"needs_setup": not auth_mod.has_account(), "signed_in": False,
                "email": auth_mod.email(), "google_ready": bool(auth_mod.google_email())}

    @app.post("/api/signup")
    def signup(request: Request, response: Response, body: dict = Body(...)) -> dict:
        if auth_mod.has_account():
            raise HTTPException(403, "This app already has an account. Sign in instead.")
        try:
            auth_mod.create_account(str(body.get("email", "")), str(body.get("password", "")))
        except ValueError as e:
            raise HTTPException(400, str(e))
        issue_session(response, auth_mod.email())
        return {"ok": True, "email": auth_mod.email()}

    @app.post("/api/google")
    def google_login(request: Request, response: Response, body: dict = Body(...)) -> dict:
        if not auth_mod.has_account():
            raise HTTPException(403, "Create your account first (email + password), then sign in with Google.")
        cid = str(eng.settings.get("auth.google_client_id") or "")
        if not cid:
            raise HTTPException(403, "Google sign-in is not set up on this server.")
        try:
            addr = auth_mod.verify_google(str(body.get("id_token", "")), cid)
        except ValueError as e:
            raise HTTPException(401, str(e))
        if not auth_mod.google_email():
            # First Google sign-in binds that address as the owner's.
            auth_mod.set_google_email(addr)
        issue_session(response, addr)
        return {"ok": True, "email": addr, "session": session_string(addr)}

    @app.post("/api/logout")
    def logout(response: Response) -> dict:
        response.delete_cookie("zt_session")
        return {"ok": True}

    @app.post("/api/account/delete", dependencies=[api])
    def account_delete(response: Response, body: dict = Body(default={})) -> dict:
        want = str(body.get("confirm", "")).strip().lower()
        if not want or want != (auth_mod.email() or "").strip().lower():
            raise HTTPException(400, "Type your email address to confirm deleting the account.")
        auth_mod.delete_account()
        response.delete_cookie("zt_session")
        return {"ok": True, "deleted": True}

    @app.post("/api/password", dependencies=[api])
    def change_password(body: dict = Body(...)) -> dict:
        try:
            auth_mod.set_password(str(body.get("password", "")))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True}

    @app.get("/api/bootstrap", dependencies=[api])
    def bootstrap(request: Request) -> dict:
        _note_app(request)
        bots = eng.bots.list()
        return {"bots": bots, "groups": eng.messaging.list_groups(), "status": eng.status(), "approvals": eng.approvals.list("pending"),
                "profiles": _profiles(), "templates": TEMPLATES, "settings": _public_settings(), "last_event": eng.events.last_id(),
                "presets": PRESETS, "handoffs": eng.messaging.list_handoffs(status="open", limit=50)}

    def _profiles() -> list[dict]:
        out = []
        allowed = eng.admin.allowed_providers()
        for pid, p in eng.settings.profiles().items():
            if allowed is not None and pid not in allowed:
                continue
            out.append({**p, "id": pid, "key_set": secrets.has_secret(f"provider:{pid}"), "preset": pid in PROVIDER_PRESETS})
        return out

    def _public_settings() -> dict:
        s = eng.settings.all()
        s.pop("providers", None)
        s["secrets_backend"] = secrets.backend_available()
        s["google_client_id"] = eng.settings.get("auth.google_client_id", "") or ""
        return s

    @app.get("/api/status", dependencies=[api])
    def status() -> dict:
        return eng.status()

    # ----------------------------------------------------------------- bots
    @app.get("/api/bots", dependencies=[api])
    def bots_list() -> list[dict]:
        rows = eng.bots.list()
        # Include the main thread id so the phone can open a chat instantly
        # instead of waiting for a second round-trip before the screen appears.
        for b in rows:
            try:
                b["thread_id"] = eng.threads.main_thread(b["id"])["id"]
            except Exception:
                b["thread_id"] = ""
        return rows

    @app.post("/api/bots", dependencies=[api])
    def bots_create(body: dict = Body(...)) -> dict:
        bot = eng.bots.create(body.get("name", ""), job=body.get("job", ""), instructions=body.get("instructions", ""), emoji=body.get("emoji", "🤖"),
                              **{k: v for k, v in body.items() if k in ("profile", "model", "approval_mode", "step_limit", "net_mode", "net_allow", "net_deny", "grants", "proactive", "template", "avatar")})
        eng.threads.main_thread(bot["id"])
        return bot

    @app.post("/api/bots/from_template", dependencies=[api])
    def bots_from_template(body: dict = Body(...)) -> dict:
        return eng.create_from_template(body["template"], body.get("name") or None)

    @app.post("/api/teams", dependencies=[api])
    def teams(body: dict = Body(default={})) -> dict:
        return eng.create_team(body.get("templates"), body.get("group_name", "Ops room"))

    @app.get("/api/templates", dependencies=[api])
    def templates() -> list[dict]:
        return TEMPLATES

    @app.get("/api/bots/{bot_id}", dependencies=[api])
    def bots_get(bot_id: str) -> dict:
        b = eng.bots.get(bot_id)
        if not b:
            raise HTTPException(404, "No such Bot.")
        b["network_layers"] = eng.net.describe(bot_id)
        b["rules"] = eng.approvals.rules(bot_id)
        return b

    @app.put("/api/bots/{bot_id}", dependencies=[api])
    def bots_update(bot_id: str, body: dict = Body(...)) -> dict:
        return eng.bots.update(bot_id, **body)

    @app.delete("/api/bots/{bot_id}", dependencies=[api])
    def bots_delete(bot_id: str) -> dict:
        eng.turns.stop_bot(bot_id)
        eng.computer.browser.drop_screen(bot_id)
        eng.bots.delete(bot_id)
        return {"ok": True}

    @app.post("/api/bots/{bot_id}/stop", dependencies=[api])
    def bots_stop(bot_id: str) -> dict:
        return {"stopped": eng.turns.stop_bot(bot_id)}

    @app.get("/api/bots/{bot_id}/export", dependencies=[api])
    def bots_export(bot_id: str, memory: bool = False) -> Response:
        data = packages.export_bot(eng, bot_id, include_memory=memory)
        name = re.sub(r"[^\w\-]+", "_", (eng.bots.get(bot_id) or {}).get("name", "bot"))
        return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}.gbbot"'})

    @app.post("/api/bots/import", dependencies=[api])
    async def bots_import(request: Request) -> dict:
        data = await request.body()
        return await asyncio.to_thread(packages.import_bot, eng, data, request.query_params.get("name"))

    @app.get("/api/rules", dependencies=[api])
    def rules_list(bot_id: str = "") -> list[dict]:
        return eng.approvals.rules(bot_id or None)

    @app.post("/api/rules", dependencies=[api])
    def rules_create(body: dict = Body(default={})) -> dict:
        bot_id = str(body.get("bot_id", "")).strip()
        category = str(body.get("category", "")).strip()
        if not bot_id or not category:
            raise HTTPException(400, "bot_id and category are required.")
        return eng.approvals.create_rule(bot_id, category, str(body.get("pattern", "*")))

    @app.delete("/api/rules/{rid}", dependencies=[api])
    def rule_delete(rid: int) -> dict:
        eng.approvals.delete_rule(rid)
        return {"ok": True}

    # -------------------------------------------------------------- threads
    @app.get("/api/bots/{bot_id}/threads", dependencies=[api])
    def threads_for_bot(bot_id: str) -> list[dict]:
        main = eng.threads.main_thread(bot_id)
        rows = eng.threads.list_for_bot(bot_id)
        for r in rows:
            r["main"] = r["id"] == main["id"]
        return rows

    @app.post("/api/bots/{bot_id}/threads", dependencies=[api])
    def threads_create(bot_id: str, body: dict = Body(default={})) -> dict:
        return eng.threads.create(bot_id, body.get("title") or "New thread")

    @app.get("/api/threads/{tid}", dependencies=[api])
    def thread_get(tid: str, limit: int = 400) -> dict:
        th = eng.threads.get(tid)
        if not th:
            raise HTTPException(404, "No such thread.")
        g = eng.messaging.group_by_thread(tid)
        running = [r for r in eng.turns.active() if r["thread_id"] == tid]
        return {"thread": th, "group": g, "items": eng.threads.display(tid, limit), "running": running,
                "approvals": [a for a in eng.approvals.list("pending") if a["thread_id"] == tid]}

    @app.put("/api/threads/{tid}", dependencies=[api])
    def thread_rename(tid: str, body: dict = Body(...)) -> dict:
        eng.threads.rename(tid, body.get("title", ""))
        return {"ok": True}

    @app.delete("/api/threads/{tid}", dependencies=[api])
    def thread_delete(tid: str) -> dict:
        eng.turns.stop_thread(tid)
        th = eng.threads.get(tid)
        if th and th["group_id"]:
            eng.messaging.delete_group(th["group_id"])
        else:
            eng.threads.delete(tid)
        return {"ok": True}

    @app.post("/api/threads/{tid}/messages", dependencies=[api])
    def thread_post(tid: str, body: dict = Body(...)) -> dict:
        # A turn is about to run: hold the VM awake for it (renews at most every
        # five minutes, fire-and-forget, never blocks the send).
        host_mod.lease_soon(900)
        paths_: list[str] = []
        notes: list[str] = []
        for im in (body.get("images") or [])[:6]:
            raw = base64.b64decode(im.get("data", ""))
            if len(raw) > 12 * 1024 * 1024:
                raise ValueError("Attachment is too large (max 12 MB).")
            name = re.sub(r"[^A-Za-z0-9._ -]", "_", str(im.get("name") or "")).strip()[:80]
            mime = str(im.get("type") or "").lower()
            is_image = (mime.startswith("image/") or raw[:3] == b"\xff\xd8\xff"
                        or raw[:8] == b"\x89PNG\r\n\x1a\n" or mime in ("", "application/octet-stream") and raw[:4] == b"GIF8")
            if is_image:
                ext = ".png" if (mime == "image/png" or raw[:8] == b"\x89PNG\r\n\x1a\n") else ".jpg"
                p = paths.screenshots_dir() / f"upload-{int(time.time() * 1000)}-{len(paths_)}{ext}"
                p.write_bytes(raw)
                paths_.append(str(p))
            else:
                # Any other file (PDF, docx, zip…) goes into the shared workspace so
                # the Bot can open it with its normal file tools instead of trying
                # to decode it as an image.
                d = paths.workspace_dir() / "shared" / "uploads"
                d.mkdir(parents=True, exist_ok=True)
                base = os.path.splitext(name)[0] or "file"
                ext = os.path.splitext(name)[1][:12] or ""
                p = d / f"{base[:60]}-{int(time.time() * 1000)}{ext}"
                p.write_bytes(raw)
                rel = p.relative_to(paths.workspace_dir()).as_posix()
                notes.append(f"[Attached file saved to {rel} - open it with read_file, "
                             "or use python_run for binary formats.]")
        text = body.get("text", "")
        if notes:
            text = (text + "\n\n" if text.strip() else "") + "\n".join(notes)
        return eng.send_user_message(tid, text, paths_, parent_id=int(body.get("parent_id") or 0))

    @app.post("/api/bots/pause_all", dependencies=[api])
    def bots_pause_all(body: dict = Body(default={})) -> dict:
        paused = bool(body.get("paused", True))
        n = eng.bots.set_paused_all(paused)
        if paused:
            for r in eng.turns.active():
                eng.turns.stop_bot(r["bot_id"])
        return {"changed": n, "paused": paused}

    @app.post("/api/bots/{bot_id}/duplicate", dependencies=[api])
    def bots_duplicate(bot_id: str, body: dict = Body(default={})) -> dict:
        return eng.bots.duplicate(bot_id, body.get("name"))

    @app.put("/api/pricing", dependencies=[api])
    def pricing_put(body: dict = Body(...)) -> dict:
        eng.usage.pricing.set_prices(body.get("models", {}), body.get("currency"))
        return eng.usage.cost_summary()

    @app.get("/api/threads/{tid}/export", dependencies=[api])
    def thread_export(tid: str) -> Response:
        th = eng.threads.get(tid)
        if not th:
            raise HTTPException(404, "No such thread.")
        name = re.sub(r"[^\w\-]+", "_", th.get("title") or "chat").strip("_")[:40] or "chat"
        return Response(eng.threads.export_markdown(tid).encode("utf-8"), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}.md"'})

    @app.post("/api/threads/{tid}/stop", dependencies=[api])
    def thread_stop(tid: str) -> dict:
        return {"stopped": eng.turns.stop_thread(tid)}

    # ---------------------------------------------------- reactions
    @app.post("/api/messages/{mid}/reactions", dependencies=[api])
    def message_react(mid: int, body: dict = Body(default={})) -> dict:
        return eng.threads.toggle_reaction(mid, body.get("emoji", ""), body.get("author", "user"))

    @app.delete("/api/messages/{mid}/reactions", dependencies=[api])
    def message_unreact(mid: int, emoji: str = "", author: str = "user") -> dict:
        return eng.threads.remove_reaction(mid, emoji, author)

    @app.get("/api/files/shots/{name}")
    def shot(name: str, request: Request) -> Response:
        auth(request)
        p = (paths.screenshots_dir() / Path(name).name)
        if not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "private, max-age=3600"})

    # --------------------------------------------------------------- groups
    @app.get("/api/groups", dependencies=[api])
    def groups_list(pairs: bool = False) -> list[dict]:
        return eng.messaging.list_groups(include_pairs=pairs)

    @app.post("/api/groups", dependencies=[api])
    def groups_create(body: dict = Body(...)) -> dict:
        return eng.messaging.create_group(body["name"], body.get("members", []), body.get("lead", ""), body.get("goal", ""))

    @app.put("/api/groups/{gid}", dependencies=[api])
    def groups_update(gid: str, body: dict = Body(...)) -> dict:
        return eng.messaging.update_group(gid, **body)

    @app.delete("/api/groups/{gid}", dependencies=[api])
    def groups_delete(gid: str) -> dict:
        g = eng.messaging.get_group(gid)
        if g:
            eng.turns.stop_thread(g["thread_id"])
        eng.messaging.delete_group(gid)
        return {"ok": True}

    # ------------------------------------------------------------ approvals
    @app.get("/api/approvals", dependencies=[api])
    def approvals_list(status: str = "pending", bot_id: str | None = None, limit: int = 100) -> list[dict]:
        return eng.approvals.list(None if status == "all" else status, bot_id, limit)

    @app.post("/api/approvals/{aid}/decide", dependencies=[api])
    def approvals_decide(aid: str, body: dict = Body(...)) -> dict:
        r = eng.approvals.decide(aid, bool(body.get("approve")), bool(body.get("remember")), body.get("note", ""), body.get("answer", ""),
                                 full_access=bool(body.get("full_access")))
        if not r:
            raise HTTPException(404, "No such approval.")
        return r

    # --------------------------------------------------------------- memory
    @app.get("/api/bots/{bot_id}/memory", dependencies=[api])
    def memory_list(bot_id: str) -> list[dict]:
        return eng.memory.list(bot_id)

    @app.post("/api/bots/{bot_id}/memory", dependencies=[api])
    def memory_add(bot_id: str, body: dict = Body(...)) -> dict:
        mid = eng.memory.add(bot_id, body.get("kind", "fact"), body.get("text", ""), source="user", verify=bool(body.get("verify")), pinned=bool(body.get("pinned")))
        return {"id": mid}

    @app.put("/api/bots/{bot_id}/memory/{mid}", dependencies=[api])
    def memory_update(bot_id: str, mid: int, body: dict = Body(...)) -> dict:
        return {"ok": eng.memory.update(bot_id, mid, body.get("text"), body.get("kind"), body.get("pinned"))}

    @app.delete("/api/bots/{bot_id}/memory/{mid}", dependencies=[api])
    def memory_delete(bot_id: str, mid: int) -> dict:
        return {"ok": eng.memory.forget(bot_id, mid)}

    @app.get("/api/projects", dependencies=[api])
    def projects_list() -> list[dict]:
        return eng.memory.project_list()

    @app.get("/api/projects/{name}", dependencies=[api])
    def projects_get(name: str) -> dict:
        p = eng.memory.project_get(name)
        if not p:
            raise HTTPException(404)
        return p

    @app.put("/api/projects/{name}", dependencies=[api])
    def projects_put(name: str, body: dict = Body(...)) -> dict:
        return eng.memory.project_write(name, body.get("content", ""), by="user")

    @app.delete("/api/projects/{name}", dependencies=[api])
    def projects_delete(name: str) -> dict:
        eng.memory.project_delete(name)
        return {"ok": True}

    # -------------------------------------------------- handoffs / followups
    @app.get("/api/handoffs", dependencies=[api])
    def handoffs_list(status: str = "open", bot_id: str | None = None) -> list[dict]:
        return eng.messaging.list_handoffs(bot_id, None if status == "all" else status)

    @app.post("/api/handoffs", dependencies=[api])
    def handoffs_create(body: dict = Body(...)) -> dict:
        return eng.messaging.create_handoff("user", body["to_bot"], body["title"], body.get("brief", ""))

    @app.put("/api/handoffs/{hid}", dependencies=[api])
    def handoffs_update(hid: str, body: dict = Body(...)) -> dict:
        return eng.messaging.update_handoff(hid, "user", body["status"], body.get("note", ""))

    @app.get("/api/followups", dependencies=[api])
    def followups_list(bot_id: str | None = None) -> list[dict]:
        return eng.messaging.list_followups(bot_id)

    @app.delete("/api/followups/{fid}", dependencies=[api])
    def followups_cancel(fid: str) -> dict:
        eng.messaging.cancel_followup(fid)
        return {"ok": True}

    # ------------------------------------------------------------- computer
    @app.get("/api/computer", dependencies=[api])
    def computer_status() -> dict:
        d = eng.computer.status()
        d["terminal"] = list(eng.computer.terminal_log)[-30:]
        return d

    @app.post("/api/computer/restart", dependencies=[api])
    def computer_restart() -> dict:
        """Recover: close every browser screen and start the browser cleanly again."""
        try:
            for b in eng.bots.list():
                try:
                    eng.computer.browser.drop_screen(b["id"])
                except Exception:  # noqa: BLE001
                    pass
            eng.computer.browser.restart()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Could not restart the browser: {e}")
        return {"ok": True, "restarted": True}

    @app.post("/api/computer/reset", dependencies=[api])
    def computer_reset() -> dict:
        """Reset: recover the browser and clear the terminal history / takeovers."""
        try:
            for b in eng.bots.list():
                try:
                    eng.computer.browser.drop_screen(b["id"])
                    if eng.computer.takeover_active(b["id"]):
                        eng.computer.takeover_end(b["id"])
                except Exception:  # noqa: BLE001
                    pass
            eng.computer.browser.restart()
            try:
                eng.computer.terminal_log.clear()
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Could not reset the computer: {e}")
        return {"ok": True, "reset": True}

    @app.get("/api/bots/{bot_id}/screen.jpg")
    def screen_jpg(bot_id: str, request: Request, q: int = 60) -> Response:
        auth(request)
        scr = eng.computer.browser.screen(bot_id)
        data, _ = eng.computer.browser.call(scr.screenshot, False, max(20, min(q, 90)), False, timeout=30)
        url = scr.page.url if scr.page is not None and not scr.page.is_closed() else ""
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Page-Url": base64.b64encode(url.encode()).decode()})

    @app.post("/api/bots/{bot_id}/screen/input", dependencies=[api])
    def screen_input(bot_id: str, body: dict = Body(...)) -> dict:
        return eng.computer.browser.call(eng.computer.browser.screen(bot_id).user_input, body, timeout=40)

    @app.post("/api/bots/{bot_id}/takeover", dependencies=[api])
    def takeover(bot_id: str, body: dict = Body(...)) -> dict:
        action = body.get("action")
        comp = eng.computer
        if action == "start":
            if not comp.takeover_active(bot_id):
                comp.takeover_begin(bot_id, "You are in control")
        elif action == "handback":
            for a in eng.approvals.list("pending", bot_id):
                if a["category"] in ("takeover", "login") and a["tool"] == "request_takeover":
                    eng.approvals.decide(a["id"], True, note=body.get("note", "Handed back from the browser view"))
            if comp.takeover_active(bot_id):
                comp.takeover_end(bot_id)
        else:
            raise ValueError("action must be start or handback")
        return {"active": comp.takeover_active(bot_id)}

    @app.post("/api/bots/{bot_id}/record", dependencies=[api])
    def record(bot_id: str, body: dict = Body(...)) -> dict:
        comp = eng.computer
        a = body.get("action")
        if a == "start":
            if not comp.takeover_active(bot_id):
                comp.takeover_begin(bot_id, "Follow-along: you are showing the Bot a job")
            return comp.rec_start(bot_id, body.get("name", "Untitled demo"))
        if a == "note":
            comp.rec_note(bot_id, body.get("text", ""))
            return {"ok": True}
        if a == "pause":
            scr = comp.browser.screen(bot_id)
            if scr.recording is not None:
                scr.recording["paused"] = bool(body.get("paused", True))
            return {"ok": True}
        if a == "stop":
            return comp.rec_stop(bot_id) or {}
        raise ValueError("action must be start, note, pause or stop")

    @app.get("/api/recordings", dependencies=[api])
    def recordings(bot_id: str | None = None) -> list[dict]:
        return eng.computer.recordings(bot_id)

    @app.post("/api/recordings/{rid}/draft_skill", dependencies=[api])
    def draft_skill(rid: str, body: dict = Body(default={})) -> dict:
        rec = eng.computer.recording(rid)
        bot = eng.bots.get(rec["bot_id"]) if rec else None
        if not rec or not bot:
            raise ValueError("Unknown recording.")
        return skills_mod.draft_from_recording(eng, bot, rec, body.get("name"))

    @app.delete("/api/recordings/{rid}", dependencies=[api])
    def recording_delete(rid: str) -> dict:
        eng.db.execute("DELETE FROM recordings WHERE id=?", (rid,))
        return {"ok": True}

    def ws_path(p: str) -> Path:
        full, inside = eng.computer.resolve(p)
        if not inside:
            raise ValueError("That path is outside the shared workspace.")
        return full

    @app.get("/api/computer/files", dependencies=[api])
    def files_list(path: str = ".") -> dict:
        ws_path(path)
        return {"path": eng.computer.rel(eng.computer.resolve(path)[0]), "items": eng.computer.list_dir(path)}

    @app.get("/api/computer/file", dependencies=[api])
    def file_get(path: str, download: bool = False) -> Response:
        f = ws_path(path)
        if not f.is_file():
            raise HTTPException(404)
        if download:
            return FileResponse(f, filename=f.name)
        text, trunc = eng.computer.read_file(path, 400_000)
        return JSONResponse({"path": path, "content": text, "truncated": trunc})

    @app.put("/api/computer/file", dependencies=[api])
    def file_put(body: dict = Body(...)) -> dict:
        ws_path(body["path"])
        eng.computer.write_file(body["path"], body.get("content", ""))
        return {"ok": True}

    @app.post("/api/computer/mkdir", dependencies=[api])
    def mkdir(body: dict = Body(...)) -> dict:
        ws_path(body["path"]).mkdir(parents=True, exist_ok=True)
        return {"ok": True}

    @app.delete("/api/computer/file", dependencies=[api])
    def file_delete(path: str) -> dict:
        ws_path(path)
        eng.computer.delete(path)
        return {"ok": True}

    @app.post("/api/computer/upload", dependencies=[api])
    async def file_upload(request: Request, path: str) -> dict:
        f = ws_path(path)
        data = await request.body()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        return {"ok": True, "size": len(data)}

    @app.post("/api/computer/terminal", dependencies=[api])
    def terminal(body: dict = Body(...)) -> dict:
        ws_path(body.get("cwd") or ".")
        return eng.computer.run_command(body["command"], body.get("cwd"), int(body.get("timeout", 120)), body.get("shell", "default"), who="you")

    # --------------------------------------------------------------- skills
    @app.get("/api/skills", dependencies=[api])
    def skills_list() -> list[dict]:
        return eng.skills.list()

    @app.get("/api/skills/{name}", dependencies=[api])
    def skills_get(name: str) -> dict:
        s = eng.skills.get(name)
        if not s:
            raise HTTPException(404)
        return s

    @app.put("/api/skills/{name}", dependencies=[api])
    def skills_put(name: str, body: dict = Body(...)) -> dict:
        return eng.skills.save(name, body["raw"])

    @app.post("/api/skills/{name}/status", dependencies=[api])
    def skills_status(name: str, body: dict = Body(...)) -> dict:
        s = eng.skills.set_status(name, body["status"])
        if not s:
            raise HTTPException(404)
        return s

    @app.delete("/api/skills/{name}", dependencies=[api])
    def skills_delete(name: str) -> dict:
        return {"ok": eng.skills.delete(name)}

    @app.post("/api/skills/{name}/test", dependencies=[api])
    def skills_test(name: str, body: dict = Body(...)) -> dict:
        return eng.test_skill(body["bot_id"], name, bool(body.get("dry_run", True)))

    # -------------------------------------------------------------- routines
    @app.get("/api/routines", dependencies=[api])
    def routines_list(bot_id: str | None = None) -> list[dict]:
        return eng.routines.list(bot_id)

    @app.post("/api/routines", dependencies=[api])
    def routines_create(body: dict = Body(...)) -> dict:
        return eng.routines.create(body["bot_id"], body.get("name", "Routine"), body["cron"], body.get("skill", ""), body.get("prompt", ""),
                                   bool(body.get("enabled", True)), bool(body.get("dry_run", False)), body.get("notify", "always"),
                                   bool(body.get("catch_up", False)),
                                   ceiling_items=int(body.get("ceiling_items") or 0),
                                   anomaly_pct=int(body.get("anomaly_pct") or 0),
                                   kill_condition=str(body.get("kill_condition") or ""))

    @app.put("/api/routines/{rid}", dependencies=[api])
    def routines_update(rid: str, body: dict = Body(...)) -> dict:
        return eng.routines.update(rid, **body)

    @app.delete("/api/routines/{rid}", dependencies=[api])
    def routines_delete(rid: str) -> dict:
        eng.routines.delete(rid)
        return {"ok": True}

    @app.post("/api/routines/{rid}/run", dependencies=[api])
    def routines_run(rid: str) -> dict:
        eng.routines.run_now(rid)
        return {"ok": True}

    @app.get("/api/routine_runs", dependencies=[api])
    def routine_runs(routine_id: str | None = None, bot_id: str | None = None, limit: int = 50) -> list[dict]:
        return eng.routines.runs(routine_id, bot_id, limit)

    # ------------------------------------------------------ event triggers
    @app.get("/api/triggers", dependencies=[api])
    def triggers_list(bot_id: str | None = None) -> list[dict]:
        return eng.triggers.list(bot_id)

    @app.post("/api/triggers", dependencies=[api])
    def triggers_create(body: dict = Body(...)) -> dict:
        return eng.triggers.create(body["bot_id"], body.get("name", "Trigger"), body.get("prompt", ""),
                                   bool(body.get("enabled", True)))

    @app.put("/api/triggers/{tid}", dependencies=[api])
    def triggers_update(tid: str, body: dict = Body(...)) -> dict:
        return eng.triggers.update(tid, **body)

    @app.post("/api/triggers/{tid}/rotate", dependencies=[api])
    def triggers_rotate(tid: str) -> dict:
        return eng.triggers.rotate(tid)

    @app.delete("/api/triggers/{tid}", dependencies=[api])
    def triggers_delete(tid: str) -> dict:
        eng.triggers.delete(tid)
        return {"ok": True}

    @app.post("/api/hooks/{token}")
    async def hooks_fire(token: str, request: Request) -> dict:
        """Public webhook: authenticated by the secret token in the URL, not a session cookie."""
        payload: dict = {}
        try:
            raw = await request.body()
            if raw:
                try:
                    parsed = json.loads(raw)
                    payload = parsed if isinstance(parsed, dict) else {"data": parsed}
                except ValueError:
                    payload = {"raw": raw.decode("utf-8", "replace")[:4000]}
        except Exception:  # noqa: BLE001
            payload = {}
        return eng.triggers.fire(token, payload)

    # ------------------------------------------------------ plugins and MCP
    @app.get("/api/plugins", dependencies=[api])
    def plugins_list() -> dict:
        return {"plugins": eng.plugins.list(), "catalog": eng.plugins.catalog(), "install_allowed": eng.admin.plugin_install_allowed()}

    @app.post("/api/plugins/install", dependencies=[api])
    def plugins_install(body: dict = Body(...)) -> dict:
        return eng.plugins.install(body["source"])

    @app.delete("/api/plugins/{pid}", dependencies=[api])
    def plugins_remove(pid: str) -> dict:
        eng.plugins.uninstall(pid)
        return {"ok": True}

    @app.put("/api/plugins/{pid}/config", dependencies=[api])
    def plugins_config(pid: str, body: dict = Body(...)) -> dict:
        eng.plugins.set_config(pid, body)
        return eng.plugins.info(pid)

    @app.post("/api/plugins/{pid}/test", dependencies=[api])
    def plugins_test(pid: str) -> dict:
        return {"message": eng.plugins.test(pid)}

    @app.post("/api/plugins/{pid}/connect", dependencies=[api])
    def plugins_connect(pid: str, request: Request, mobile: int = 0) -> dict:
        # Vendors must be able to reach the callback: use the public tunnel URL when
        # this request came through one, not the loopback address.
        d = eng.plugins.defs.get(pid)
        provider = ((d.oauth if d else None) or {}).get("provider")
        # Google plugins sign in with the server's Google app (the same client as
        # "Continue with Google"), whose only registered redirect URI is
        # /auth/google-app/cb. One callback, one console entry, no extra setup.
        path = "/auth/google-app/cb" if provider == "google" else "/oauth/callback"
        return {"url": eng.plugins.begin_oauth(pid, _pub_origin(request) + path, mobile=bool(mobile))}

    @app.post("/api/plugins/{pid}/disconnect", dependencies=[api])
    def plugins_disconnect(pid: str) -> dict:
        eng.plugins.clear_secrets(pid)
        return eng.plugins.info(pid)

    @app.post("/api/plugins/rest", dependencies=[api])
    def plugins_rest(body: dict = Body(...)) -> dict:
        return eng.plugins.add_rest(body)

    # ------------------------------------------------- plugin items (emails, etc.)
    # Phone-app CRUD for plugin data items (e.g. Gmail messages). The server holds
    # the plugin credentials, so the phone can list and delete items without going
    # through the bot agent.
    def _gmail_headers(pid: str) -> dict:
        cid, csec = google_client(eng.plugins, pid)
        return {"Authorization": "Bearer " + eng.plugins.google.access_token(pid, cid, csec)}

    @app.get("/api/plugins/{pid}/items", dependencies=[api])
    def plugins_items_list(pid: str, request: Request, query: str = "", max_results: int = 10) -> dict:
        import httpx
        d = eng.plugins.defs.get(pid)
        if not d:
            raise HTTPException(404, "Unknown plugin.")
        if not eng.plugins.configured(pid):
            raise HTTPException(400, "Plugin not configured. Press Connect first.")
        if d.id != "gmail":
            raise HTTPException(400, "Item listing not supported for this plugin.")
        base = "https://gmail.googleapis.com/gmail/v1/users/me"
        headers = _gmail_headers(pid)
        r = httpx.get(f"{base}/messages", headers=headers,
                      params={"q": query, "maxResults": min(max_results, 25)}, timeout=30)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text[:400])
        res = r.json()
        out = []
        for m in res.get("messages", []):
            rr = httpx.get(f"{base}/messages/{m['id']}", headers=headers,
                           params={"format": "metadata", "metadataHeaders": ["From", "To", "Subject", "Date"]}, timeout=30)
            if rr.status_code >= 400:
                continue
            dd = rr.json()
            hd = {x["name"]: x["value"] for x in dd.get("payload", {}).get("headers", [])}
            out.append({"id": dd["id"], "thread_id": dd.get("threadId"), "from": hd.get("From"),
                        "to": hd.get("To"), "subject": hd.get("Subject"), "date": hd.get("Date"),
                        "snippet": dd.get("snippet"), "labels": dd.get("labelIds")})
        return {"items": out, "plugin": pid}

    @app.delete("/api/plugins/{pid}/items/{item_id}", dependencies=[api])
    def plugins_item_delete(pid: str, item_id: str) -> dict:
        import httpx
        d = eng.plugins.defs.get(pid)
        if not d:
            raise HTTPException(404, "Unknown plugin.")
        if not eng.plugins.configured(pid):
            raise HTTPException(400, "Plugin not configured.")
        if d.id != "gmail":
            raise HTTPException(400, "Item delete not supported for this plugin.")
        base = "https://gmail.googleapis.com/gmail/v1/users/me"
        r = httpx.delete(f"{base}/messages/{item_id}", headers=_gmail_headers(pid), timeout=30)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text[:400])
        return {"ok": True, "deleted": item_id}

    # ------- phone app: over-the-air JS updates (expo-updates protocol, self-hosted)
    _updates_dir = paths.data_dir() / "updates"

    def _note_update(request: Request, what: str) -> None:
        """Keep a tiny trail of what the phone asks for — the update check is silent
        otherwise, and "it never updates" is impossible to debug without it."""
        try:
            line = " ".join([
                time.strftime("%Y-%m-%d %H:%M:%S"),
                what,
                (request.client.host if request.client else "-"),
                "platform=" + request.headers.get("expo-platform", "-"),
                "runtime=" + request.headers.get("expo-runtime-version", "-"),
                "current=" + (request.headers.get("expo-current-update-id", "-") or "-")[:8],
                "ua=" + (request.headers.get("user-agent", "-") or "-")[:50],
            ])
            _updates_dir.mkdir(parents=True, exist_ok=True)
            with open(_updates_dir / "requests.log", "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass

    def _note_app(request: Request) -> None:
        """Which bundle is the phone actually running? The app sends its version
        and expo-updates id on every API call; log only when it changes, so the
        answer to "I updated but nothing changed" is in one file."""
        try:
            app = request.headers.get("x-zb-app", "")
            ota = request.headers.get("x-zb-ota", "")
            if not app and not ota:
                return
            key = app + "|" + ota
            if _note_app.last == key:
                return
            _note_app.last = key
            line = " ".join([time.strftime("%Y-%m-%d %H:%M:%S"), "app", (request.client.host if request.client else "-"),
                             "version=" + (app or "-"), "ota=" + (ota or "-")[:8],
                             "ua=" + (request.headers.get("user-agent", "-") or "-")[:40]])
            _updates_dir.mkdir(parents=True, exist_ok=True)
            with open(_updates_dir / "requests.log", "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
    _note_app.last = ""

    @app.get("/updates/manifest")
    def updates_manifest(request: Request) -> Response:
        f = _updates_dir / "manifest.json"
        _note_update(request, "manifest" if f.is_file() else "manifest-missing")
        if not f.is_file():
            raise HTTPException(404, "No update published yet.")
        return Response(f.read_bytes(), media_type="application/json", headers={
            "expo-protocol-version": "1",
            "expo-sfv-version": "0",
            "expo-manifest-filters": "",
            "expo-server-defined-headers": "",
            "cache-control": "private, max-age=0",
        })

    @app.get("/updates/assets/{name}")
    def updates_asset(name: str, request: Request) -> Response:
        f = (_updates_dir / "assets" / Path(name).name)
        _note_update(request, "asset:" + Path(name).name[:24] + ("" if f.is_file() else ":missing"))
        if not f.is_file():
            raise HTTPException(404, "No such asset.")
        return FileResponse(f, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    # ------------------------------------------------------ phone diagnostics
    # A release build has no console, so a crash on a handset is invisible from
    # here. The app posts its stack trace to this endpoint; Settings →
    # Diagnostics shows the same records, and they land in logs/client.log.
    @app.post("/api/client-log", dependencies=[api])
    def client_log_post(request: Request, body: dict = Body(default={})) -> dict:
        try:
            d = paths.logs_dir()
            d.mkdir(parents=True, exist_ok=True)
            rec = {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "ip": (request.client.host if request.client else "-"),
                "kind": str(body.get("kind") or "error")[:40],
                "app": str(body.get("app") or "")[:40],
                "code": body.get("code") or 0,
                "ota": str(body.get("ota") or "")[:40],
                "message": str(body.get("message") or "")[:1200],
                "stack": str(body.get("stack") or "")[:8000],
                "extra": body.get("extra") or "",
                "crumbs": body.get("crumbs") if isinstance(body.get("crumbs"), list) else [],
            }
            with open(d / "client.log", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 - diagnostics must never fail the app
            pass
        return {"ok": True}

    @app.get("/api/client-log", dependencies=[api])
    def client_log_get(limit: int = 40) -> dict:
        items: list[dict] = []
        try:
            f = paths.logs_dir() / "client.log"
            if f.is_file():
                for line in f.read_text(encoding="utf-8").splitlines()[-max(1, min(limit, 500)):]:
                    try:
                        items.append(json.loads(line))
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass
        return {"items": items}

    @app.get("/oauth/callback", response_class=HTMLResponse)
    def oauth_callback(code: str = "", state: str = "", error: str = "") -> str:
        if error or not code:
            return f"<h3>Sign-in failed</h3><p>{error or 'No code returned.'}</p>"
        mobile = bool((eng.plugins.pending.get(state) or {}).get("mobile"))
        try:
            pid = eng.plugins.finish_oauth(state, code)
            eng.events.publish("plugins", change="connected", plugin_id=pid)
            if mobile:
                deep = f"zerothebot://plugins/go?pid={pid}"
                return (f"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                        f"<body style='font-family:-apple-system,sans-serif;background:#111;color:#eee;padding:40px;text-align:center'>"
                        f"<h2>Connected ✔</h2><p style='opacity:.8'>Taking you back to {APP_NAME}…</p>"
                        f"<p><a href='{deep}' style='display:inline-block;margin-top:12px;padding:12px 22px;border-radius:999px;"
                        f"background:#25d366;color:#04110a;font-weight:700;text-decoration:none'>Open {APP_NAME}</a></p>"
                        f"<script>setTimeout(function(){{location.replace('{deep}');}},150);</script></body>")
            return f"<body style='font-family:sans-serif;background:#111;color:#eee;padding:40px'><h2>Connected ✔</h2><p>You can close this tab and return to {APP_NAME}.</p></body>"
        except Exception as e:  # noqa: BLE001
            return f"<h3>Sign-in failed</h3><p>{e}</p>"

    _GOOGLE_BODY_CSS = ("font-family:-apple-system,sans-serif;background:#111;color:#eee;"
                        "display:flex;align-items:center;justify-content:center;min-height:92vh;margin:0")

    @app.get("/auth/google-login", response_class=HTMLResponse)
    def google_login_page() -> str:
        cid = eng.settings.get("auth.google_client_id") or ""
        head = "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        if not cid:
            return (f"{head}<body style='{_GOOGLE_BODY_CSS}'><p style='max-width:22em;text-align:center'>"
                    "Google sign-in is not set up on this server yet. Ask the owner to add a Google Client ID.</p></body>")
        return f"""{head}
<style>body{{{_GOOGLE_BODY_CSS}}} .box{{text-align:center}}</style>
<script src="https://accounts.google.com/gsi/client" async defer></script>
<body><div class='box'>
<div id="gbtn"></div>
<script>
google.accounts.id.initialize({{ client_id: '{cid}',
  ux_mode: 'redirect',
  login_uri: '/auth/google-callback' }});
google.accounts.id.renderButton(document.getElementById('gbtn'),
  {{ theme: 'filled_blue', size: 'large', shape: 'pill', text: 'continue_with', width: 260 }});
</script>
</div></body>"""

    @app.post("/auth/google-callback", response_class=HTMLResponse)
    async def google_callback(request: Request) -> str:
        """GIS redirect mode posts the credential here (popup-free, works in embedded webviews)."""
        try:
            form = await request.form()
            cred = str(form.get("credential") or "")
        except Exception:  # noqa: BLE001
            cred = ""
        if not auth_mod.has_account():
            return "<h3>Create your account first (email + password), then sign in with Google.</h3>"
        try:
            addr = auth_mod.verify_google(cred, str(eng.settings.get("auth.google_client_id") or ""))
        except ValueError as e:
            return f"<h3>Sign-in failed</h3><p>{e}</p>"
        if not auth_mod.google_email():
            auth_mod.set_google_email(addr)
        tok = session_string(addr)
        return (f"<!doctype html><meta charset='utf-8'><body style='{_GOOGLE_BODY_CSS}'><p>Signing you in…</p>"
                f"<script>location.replace('/auth/google-done?tok=' + encodeURIComponent('{tok}'));</script></body>")

    @app.get("/auth/google-done", response_class=HTMLResponse)
    def google_done() -> str:
        return (f"<!doctype html><meta charset='utf-8'><body style='{_GOOGLE_BODY_CSS}'><p>"
                f"Signed in. Return to the {APP_NAME} app.</p></body>")

    # ------- phone app: real system-browser Google sign-in (webviews are blocked by Google policy)
    def _pub_origin(request: Request) -> str:
        """Public https origin of this service (tunnel-aware).

        Public hostnames are always https: tunnels usually terminate TLS, and a
        mismatch here makes Google reject the flow with redirect_uri_mismatch.
        """
        ov = str(eng.settings.get("auth.public_base_url") or "").rstrip("/")
        if ov:
            return ov
        host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "127.0.0.1:8765")
        name = host.split(":")[0]
        public = "." in name and not name.replace(".", "").isdigit() and name != "localhost"
        proto = "https" if public else (request.headers.get("x-forwarded-proto") or "http")
        return f"{proto}://{host}"

    def _g_state(secret: str) -> str:
        return hmac.new(secret.encode(), b"google-app-oauth", hashlib.sha256).hexdigest()

    @app.get("/auth/google-app/start")
    def google_app_start(request: Request):
        cid = str(eng.settings.get("auth.google_client_id") or "")
        secret = str(eng.settings.get("auth.google_client_secret") or "")
        head = "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        if not cid or not secret:
            return HTMLResponse(f"{head}<body style='{_GOOGLE_BODY_CSS}'><p style='max-width:22em;text-align:center'>"
                                "Google sign-in is not set up on this server yet (client id + secret needed).</p></body>")
        q = {
            "client_id": cid,
            "redirect_uri": _pub_origin(request) + "/auth/google-app/cb",
            "response_type": "code",
            "scope": "openid email",
            "state": _g_state(secret),
            "prompt": "select_account",
        }
        return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(q), status_code=307)

    @app.get("/auth/google-app/cb", response_class=HTMLResponse)
    async def google_app_cb(request: Request, code: str = "", state: str = "", error: str = "") -> str:
        cid = str(eng.settings.get("auth.google_client_id") or "")
        secret = str(eng.settings.get("auth.google_client_secret") or "")
        head = "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"

        def page(title: str, line: str, deep: str = "") -> str:
            extra = ""
            if deep:
                extra = (f"<p><a href='{deep}' style='display:inline-block;margin-top:14px;padding:12px 22px;border-radius:999px;"
                         f"background:#25d366;color:#04110a;font-weight:700;text-decoration:none'>Open {APP_NAME}</a></p>"
                         f"<p style='font-size:13px;opacity:.7'>If nothing happens, tap the green button.</p>"
                         f"<script>setTimeout(function(){{location.replace('{deep}');}},150);</script>")
            return (f"{head}<body style='{_GOOGLE_BODY_CSS}'><div style='max-width:24em;text-align:center;margin:0 auto'>"
                    f"<h3>{title}</h3><p>{line}</p>{extra}</div></body>")

        if error or not code:
            return page("Sign-in cancelled", f"{error or 'No code returned from Google.'}<br>Go back to the app and try again.")
        pend = eng.plugins.pending.get(state) if state else None
        if pend:
            # A plugin Connect (Gmail/Calendar/Docs) came back through this
            # callback: finish the token exchange and hand control to the app.
            try:
                pid = eng.plugins.finish_oauth(state, code)
            except Exception as e:  # noqa: BLE001
                return page("Sign-in failed", str(e))
            eng.events.publish("plugins", change="connected", plugin_id=pid)
            pdef = eng.plugins.defs.get(pid)
            deep = f"zerothebot://plugins/go?pid={pid}" if pend.get("mobile") else ""
            return page("Connected ✔", f"{pdef.name if pdef else pid} is connected. You can close this tab.", deep)
        if not secret or state != _g_state(secret):
            return page("Sign-in expired", "Go back to the app and tap Continue with Google again.")
        try:
            import urllib.request as _ureq
            form = urlencode({
                "code": code, "client_id": cid, "client_secret": secret,
                "redirect_uri": _pub_origin(request) + "/auth/google-app/cb",
                "grant_type": "authorization_code",
            }).encode()
            r = json.loads(_ureq.urlopen(_ureq.Request("https://oauth2.googleapis.com/token", data=form), timeout=30).read())
            addr = auth_mod.verify_google(str(r.get("id_token") or ""), cid)
        except Exception as e:  # noqa: BLE001
            return page("Sign-in failed", f"{e}<br>Go back to the app and try again.")
        if not auth_mod.has_account():
            return page("No account yet", f"Create your {APP_NAME} account first (email + password), then sign in with Google.")
        if not auth_mod.google_email():
            auth_mod.set_google_email(addr)
        tok = session_string(addr)
        deep = f"zerothebot://auth/google-app/go?tok={tok}&email={quote(addr)}"
        return page("Signed in", f"{addr}<br>Taking you back to the app…", deep)

    @app.get("/api/mcp", dependencies=[api])
    def mcp_list() -> list[dict]:
        return eng.mcp.configs()

    @app.post("/api/mcp", dependencies=[api])
    def mcp_save(body: dict = Body(...)) -> dict:
        if body.get("import_json"):
            return {"servers": eng.mcp.import_json(body["import_json"])}
        return eng.mcp.save(body)

    @app.delete("/api/mcp/{sid}", dependencies=[api])
    def mcp_delete(sid: str) -> dict:
        eng.mcp.delete(sid)
        return {"ok": True}

    @app.post("/api/mcp/{sid}/restart", dependencies=[api])
    def mcp_restart(sid: str) -> dict:
        eng.mcp.restart(sid)
        return {"ok": True}

    # --------------------------------------------------------------- MCP HTTP proxy
    # Proxies StreamableHTTP MCP requests to locally-running MCP servers
    # (e.g. the QuickBooks MCP server on port 3333) so they are reachable
    # over the public HTTPS URL without exposing extra ports.
    @app.post("/api/mcp-proxy/{mcp_id:path}")
    async def mcp_http_proxy(request: Request, mcp_id: str):
        """Proxy MCP HTTP requests to a local MCP server.

        mcp_id is the server name (e.g. "quickbooks"). The port is resolved
        from MCP_PROXY_PORTS env var: a JSON map like {"quickbooks": 3333}.
        """
        try:
            return await _mcp_proxy_inner(request, mcp_id)
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise HTTPException(500, f"Proxy error: {type(e).__name__}: {e}")

    async def _mcp_proxy_inner(request: Request, mcp_id: str):
        import json as _json
        ports_raw = os.environ.get("MCP_PROXY_PORTS", "{}")
        try:
            ports = _json.loads(ports_raw)
        except Exception:
            ports = {}
        port = ports.get(mcp_id)
        if not port:
            # Strip trailing /mcp if present (clients may call /mcp-proxy/quickbooks/mcp)
            stripped = mcp_id.removesuffix("/mcp") if mcp_id.endswith("/mcp") else mcp_id
            port = ports.get(stripped)
        if not port:
            raise HTTPException(404, f"No MCP proxy port configured for '{mcp_id}'")

        target_url = f"http://127.0.0.1:{port}/mcp"
        body = await request.body()

        # Use curl subprocess for reliable MCP SSE proxying
        import subprocess
        cmd = [
            "curl", "-s", "-X", request.method,
            "-H", "Content-Type: application/json",
            "-H", "Accept: application/json, text/event-stream",
            "-H", "MCP-Protocol-Version: 2024-11-05",
        ]
        if body:
            cmd.extend(["-d", body.decode() if isinstance(body, bytes) else body])
        cmd.append(target_url)

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            resp_data = result.stdout
            status = result.returncode if result.returncode == 0 else 500
        except subprocess.TimeoutExpired:
            raise HTTPException(504, f"MCP server '{mcp_id}' timed out")
        except Exception as e:
            raise HTTPException(500, f"Proxy error: {type(e).__name__}: {e}")

        resp_headers = {"content-type": "application/json, text/event-stream"}

        return Response(
            content=resp_data,
            status_code=status,
            headers=resp_headers,
            media_type="application/json",
        )

    @app.post("/api/test-simple")
    async def test_simple():
        return {"ok": True}

    @app.post("/api/test-simple")
    async def test_simple():
        return {"ok": True}

    # ----------------------------------------------------- settings / secrets
    @app.get("/api/settings", dependencies=[api])
    def settings_get() -> dict:
        return _public_settings()

    @app.put("/api/settings", dependencies=[api])
    def settings_put(body: dict = Body(...)) -> dict:
        old_headless = eng.settings.get("computer.headless")
        for k, v in body.items():
            if k in ("providers",) or k.startswith("plugin_cfg"):
                continue
            eng.settings.set(k, v)
        if eng.settings.get("computer.headless") != old_headless:
            eng.computer.browser.restart()
        return _public_settings()

    @app.get("/api/providers", dependencies=[api])
    def providers_list() -> list[dict]:
        return _profiles()

    @app.put("/api/providers/{pid}", dependencies=[api])
    def providers_put(pid: str, body: dict = Body(...)) -> dict:
        allowed = eng.admin.allowed_providers()
        if allowed is not None and pid not in allowed:
            raise ValueError("This provider is not allowed by your administrator.")
        profs = dict(eng.settings.get("providers", {}) or {})
        cur = dict(profs.get(pid) or PROVIDER_PRESETS.get(pid, {}))
        for k in ("label", "kind", "base_url", "model", "vision", "needs_key"):
            if k in body:
                cur[k] = body[k]
        if cur.get("kind") not in ("anthropic", "openai"):
            raise ValueError("kind must be 'anthropic' or 'openai' (any OpenAI-compatible endpoint).")
        cur.setdefault("label", pid)
        profs[pid] = cur
        eng.settings.set("providers", profs)
        if body.get("api_key"):
            secrets.set_secret(f"provider:{pid}", body["api_key"])
        if body.get("make_default"):
            eng.settings.set("default_profile", pid)
        return next(p for p in _profiles() if p["id"] == pid)

    @app.delete("/api/providers/{pid}", dependencies=[api])
    def providers_delete(pid: str) -> dict:
        profs = dict(eng.settings.get("providers", {}) or {})
        profs.pop(pid, None)
        eng.settings.set("providers", profs)
        secrets.delete_secret(f"provider:{pid}")
        return {"ok": True}

    @app.post("/api/providers/{pid}/test", dependencies=[api])
    def providers_test(pid: str, body: dict = Body(default={})) -> dict:
        """Detect the model list; with full=True also validate the chosen model and run a real chat call."""
        prof = eng.settings.profile(pid)
        mdl = prof.get("model") or ""
        prov = make_provider(eng.settings, pid, mdl or "test")
        if prov.profile.get("needs_key", True) and not prov.api_key:
            raise ValueError("No API key saved for this provider yet.")
        out: dict = {"models": [], "warnings": [], "chat": None}
        try:
            out["models"] = prov.list_models()
        except ProviderError as e:
            if not body.get("full"):
                raise
            out["warnings"].append(f"Could not list models: {friendly_error(e)}")
        out["warnings"] += model_warnings(mdl, out["models"])
        if body.get("full"):
            if not mdl:
                out["warnings"].append("No default model chosen yet — pick one in the Default model box.")
            else:
                try:
                    r = prov.stream("Reply with the single word OK.", [{"role": "user", "content": [{"type": "text", "text": "ping"}]}],
                                    [], lambda _t: None, lambda: False, max_tokens=20)
                    out["chat"] = {"ok": True, "reply": (r.text or "").strip()[:120]}
                except ProviderError as e:
                    out["chat"] = {"ok": False, "kind": e.kind, "error": friendly_error(e)}
        return out

    @app.post("/api/voice/transcribe", dependencies=[api])
    async def voice_transcribe(request: Request, bot_id: str = "") -> dict:
        """Speech to text through the Bot's own provider (keys never leave the service)."""
        from core import voice as voice_core

        form = await request.form()
        up = form.get("file")
        data = await up.read() if up is not None else b""
        if not data:
            raise ValueError("No audio received.")
        bot = eng.bots.get(bot_id) if bot_id else None
        pid = (bot or {}).get("profile", "")
        if not pid:
            raise ValueError("That Bot has no provider set.")
        prov = make_provider(eng.settings, pid, (bot or {}).get("model") or "voice")
        base = prov.profile.get("base_url") or "https://api.openai.com/v1"
        model = (bot or {}).get("model") or ""
        kind = pid if pid in ("openai", "groq") else (getattr(prov, "kind", "") or "")
        stt = voice_core.pick_stt_model(kind, model if "whisper" in model.lower() else "")
        return {"text": voice_core.transcribe(base, prov.api_key, data, stt)}

    @app.put("/api/secrets/{name}", dependencies=[api])
    def secret_put(name: str, body: dict = Body(...)) -> dict:
        if not name.startswith(SECRET_PREFIXES):
            raise ValueError("Unsupported secret name.")
        value = str(body.get("value", ""))
        if value:
            secrets.set_secret(name, value)
        else:
            secrets.delete_secret(name)
        return {"ok": True, "set": bool(value)}

    @app.get("/api/usage", dependencies=[api])
    def usage() -> dict:
        return eng.usage.summary()

    @app.get("/api/network", dependencies=[api])
    def network(bot_id: str | None = None) -> list[dict]:
        return eng.net.describe(bot_id)

    @app.get("/api/admin", dependencies=[api])
    def admin() -> dict:
        eng.admin.reload()
        return eng.admin.summary()

    # ----------------------------------------------------------- action log
    @app.get("/api/actions", dependencies=[api])
    def actions(bot_id: str | None = None, limit: int = 200, offset: int = 0, tool: str | None = None, status: str | None = None) -> list[dict]:
        return eng.actions.list(bot_id, limit, offset, tool, status)

    @app.get("/api/actions/export", dependencies=[api])
    def actions_export(bot_id: str | None = None, format: str = "json") -> Response:
        data, mime, name = eng.actions.export(bot_id, format)
        return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    # --------------------------------------------------------- instabox host
    # The machine this service runs on: live CPU/RAM, console log, OOM kills,
    # ports, the desktop and the platform's own state. The phone gets one
    # compact snapshot so "the bot is offline" has an answer.
    @app.get("/api/host", dependencies=[api])
    def host_health(refresh: bool = False) -> dict:
        return host_mod.health(refresh=refresh)

    @app.post("/api/host/wake", dependencies=[api])
    def host_wake() -> dict:
        try:
            return host_mod.wake()
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/host/lease", dependencies=[api])
    def host_lease(body: dict = Body(default={})) -> dict:
        try:
            return host_mod.lease(int(body.get("ttl") or 900))
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/host/desktop", dependencies=[api])
    def host_desktop(body: dict = Body(default={})) -> dict:
        """Start the machine's desktop if needed, then mint a short-lived link.

        The link is view-only by default and expires; it is not an API key and
        cannot call the API, so it is safe to hand to the phone's WebView.
        """
        try:
            host_mod.desktop_start()
            link = host_mod.desktop_link(bool(body.get("viewOnly", True)), int(body.get("ttl") or 3600))
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, **link}

    @app.delete("/api/host/desktop", dependencies=[api])
    def host_desktop_revoke() -> dict:
        try:
            return host_mod.desktop_revoke()
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/host/type", dependencies=[api])
    def host_type(body: dict = Body(default={})) -> dict:
        """Type into the focused desktop window (phone live view fallback)."""
        try:
            return host_mod.desktop_type(str(body.get("text") or ""), bool(body.get("enter")),
                                         int(body.get("delayMs") or 18))
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/host/key", dependencies=[api])
    def host_key(body: dict = Body(default={})) -> dict:
        """Press one key (Return, Tab, Escape, ctrl+a...) on the desktop."""
        try:
            return host_mod.desktop_key(str(body.get("key") or ""))
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/host/files", dependencies=[api])
    def host_files(path: str = "/data/home") -> dict:
        """Absolute-path browsing through Instabox (the workspace has its own,
        faster endpoints; this one reaches logs, builds and /data)."""
        try:
            return host_mod.list_files(path)
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/host/file", dependencies=[api])
    def host_file(path: str, download: bool = True) -> Response:
        """One file from the machine's disk, straight from Instabox.

        `download=0` returns text for the in-app preview; otherwise the bytes
        are streamed with a filename so Android saves them.
        """
        if not path.startswith("/"):
            raise HTTPException(400, "Use an absolute path.")
        try:
            raw = host_mod.read_bytes(path)
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))
        name = path.rstrip("/").rsplit("/", 1)[-1] or "file"
        if download:
            return Response(raw, media_type="application/octet-stream",
                            headers={"Content-Disposition": f'attachment; filename="{name}"'})
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(415, "That file is not text; download it instead.")
        return JSONResponse({"path": path, "content": text[:400_000],
                             "truncated": len(text) > 400_000})

    @app.post("/api/host/upload-url", dependencies=[api])
    def host_upload_url(body: dict = Body(...)) -> dict:
        """A signed URL the phone can PUT a large file to, without proxying it
        through this server."""
        try:
            return host_mod.upload_url(body["path"], int(body.get("expires") or 900))
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    # ------------------------------------------------------------ snapshots
    @app.get("/api/host/snapshots", dependencies=[api])
    def host_snapshots() -> dict:
        try:
            return {"snapshots": host_mod.snapshots()}
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/host/snapshots", dependencies=[api])
    def host_snapshot_save(body: dict = Body(default={})) -> dict:
        try:
            return host_mod.snapshot_create(body.get("name") or "", body.get("description") or "",
                                            bool(body.get("rootOnly")), int(body.get("wait") or 45))
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/host/snapshots/{sid}", dependencies=[api])
    def host_snapshot_delete(sid: str) -> dict:
        try:
            return host_mod.snapshot_delete(sid)
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/host/restore", dependencies=[api])
    def host_restore(body: dict = Body(...)) -> dict:
        """Destructive: the machine's disks go back to the snapshot. The phone
        must send the machine name as `confirm`."""
        try:
            name = (host_mod.machine().get("name") or "").lower()
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))
        if (body.get("confirm") or "").strip().lower() != name:
            raise HTTPException(400, f"Type the machine name ({name}) to confirm the restore.")
        try:
            return host_mod.restore(body.get("snapshotId") or "", body.get("state") or "running")
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/host/fork", dependencies=[api])
    def host_fork(body: dict = Body(default={})) -> dict:
        try:
            return host_mod.fork(body.get("name") or "")
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    # ---------------------------------------------------------------- ports
    @app.get("/api/host/ports", dependencies=[api])
    def host_ports() -> dict:
        try:
            return {"ports": host_mod.ports()}
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/host/ports", dependencies=[api])
    def host_port_publish(body: dict = Body(...)) -> dict:
        try:
            return host_mod.port_publish(int(body["port"]), body.get("name") or "",
                                         body.get("access") or "public")
        except (KeyError, ValueError, TypeError):
            raise HTTPException(400, "Send a port number.")
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/host/ports/{port}", dependencies=[api])
    def host_port_unpublish(port: int) -> dict:
        try:
            return host_mod.port_unpublish(port)
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    # -------------------------------------------------------------- secrets
    @app.get("/api/host/secrets", dependencies=[api])
    def host_secrets() -> dict:
        """Three stores in one view: Instabox org Secrets (values never leave
        the platform), this machine's selection, and ZerotheBot's own vault."""
        org = host_mod.org_secrets()
        return {
            "instabox": org,
            "machine": host_mod.machine_secrets(),
            "local": secrets.list_secrets(),
            "env": secrets.env_secret_names(),
            "vault_backend": secrets.backend_available(),
        }

    @app.put("/api/host/secrets/{name}", dependencies=[api])
    def host_secret_put(name: str, body: dict = Body(...)) -> dict:
        try:
            return host_mod.secret_set(name, body.get("value") or "",
                                       body.get("delivery") or "proxy",
                                       body.get("hosts") or [])
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/host/secrets/{name}", dependencies=[api])
    def host_secret_delete(name: str) -> dict:
        try:
            return host_mod.secret_delete(name)
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/host/secrets/apply", dependencies=[api])
    def host_secrets_apply(body: dict = Body(default={})) -> dict:
        try:
            return host_mod.machine_secrets_select(body.get("all"), body.get("env"))
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    # ZerotheBot's own encrypted vault: the keys Bots use for plugins and tools.
    @app.put("/api/host/vault/{name}", dependencies=[api])
    def host_vault_put(name: str, body: dict = Body(...)) -> dict:
        value = (body.get("value") or "").strip()
        if not name.strip() or not value:
            raise HTTPException(400, "A name and a value are required.")
        try:
            secrets.set_secret(name.strip(), value)
        except SecretStoreError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "name": name.strip()}

    @app.delete("/api/host/vault/{name}", dependencies=[api])
    def host_vault_delete(name: str) -> dict:
        secrets.delete_secret(name)
        return {"ok": True}

    # ----------------------------------------------------------------- exec
    @app.post("/api/host/exec", dependencies=[api])
    def host_exec(body: dict = Body(...)) -> StreamingResponse:
        """Run a shell command on the machine and stream stdout/stderr as SSE.

        The phone reads this with a plain XHR so output appears as it happens.
        """
        cmd = (body.get("cmd") or "").strip()
        if not cmd:
            raise HTTPException(400, "Command is empty.")
        cwd = body.get("cwd") or None
        timeout = int(body.get("timeout") or 60)

        def gen():
            yield "retry: 3000\n\n"
            try:
                for ev in host_mod.exec_stream(cmd, cwd, timeout):
                    yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'], default=str)}\n\n"
            except host_mod.HostError as e:
                yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/host/exec/history", dependencies=[api])
    def host_exec_history(limit: int = 20) -> dict:
        try:
            return {"commands": host_mod.exec_history(limit)}
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/host/exec/history/{eid}", dependencies=[api])
    def host_exec_one(eid: str) -> dict:
        try:
            return host_mod.exec_get(eid)
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    # ------------------------------------------------------------- template
    @app.get("/api/host/template", dependencies=[api])
    def host_template() -> dict:
        try:
            return host_mod.template_plan()
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/host/template/refresh", dependencies=[api])
    def host_template_refresh(body: dict = Body(default={})) -> dict:
        try:
            return host_mod.template_refresh(body.get("confirm") or "", body.get("name") or "")
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/host/webhook/register", dependencies=[api])
    def host_webhook_register() -> dict:
        try:
            return host_mod.register_webhook(host_mod.public_url() + "/api/instabox/events")
        except host_mod.HostError as e:
            raise HTTPException(400, str(e))

    _hook_seen: dict[str, float] = {}

    @app.post("/api/instabox/events")
    async def instabox_hook(request: Request) -> dict:
        """Signed platform events: the machine paused, stopped, errored or woke.

        This is the one API route without the app token: Instabox proves itself
        with a Standard Webhooks signature instead.
        """
        raw = await request.body()
        try:
            evt = host_mod.verify_webhook(request.headers, raw)
        except host_mod.HostError as e:
            raise HTTPException(401, str(e))
        eid = str(evt.get("id") or "")
        now = time.time()
        for k, t in list(_hook_seen.items()):
            if now - t > 3600:
                _hook_seen.pop(k, None)
        if eid and eid in _hook_seen:
            return {"ok": True, "duplicate": True}
        if eid:
            _hook_seen[eid] = now
        kind = str(evt.get("type") or "")
        data = evt.get("data") or {}
        sb = data.get("sandbox") or {}
        reason = evt.get("reason") or data.get("reason") or ""
        name = sb.get("name") or "ZerotheBot server"
        title = body = ""
        urgent = False
        if kind == "sandbox.suspended":
            title, body = "Server paused", f"{name} paused ({reason or 'idle'}). It wakes on the next message."
        elif kind == "sandbox.stopped":
            title, body, urgent = "Server stopped", f"{name} stopped ({reason or 'unknown'}).", True
        elif kind == "sandbox.error":
            title, body, urgent = "Server error", f"{name} is in an error state.", True
        elif kind == "sandbox.started":
            title, body = "Server awake", f"{name} is running again."
        elif kind == "sandbox.deleted":
            title, body, urgent = "Server deleted", f"{name} was deleted.", True
        if title:
            eng.notify("host", None, None, title, body, urgent=urgent)
        if kind in ("sandbox.error", "sandbox.stopped"):
            try:
                host_mod.wake()
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True}

    # --------------------------------------------------------- notifications
    @app.get("/api/notifications", dependencies=[api])
    def notifications(limit: int = 50, after: int = 0, unread_only: bool = False) -> list[dict]:
        lim = max(1, min(200, limit))
        sql = "SELECT n.*, COALESCE(b.name,'') AS bot_name FROM notifications n LEFT JOIN bots b ON b.id=n.bot_id"
        conds, args = [], []
        if after:
            conds.append("n.id>?")
            args.append(int(after))
        if unread_only:
            conds.append("n.read=0")
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY n.id DESC LIMIT ?"
        args.append(lim)
        return eng.db.query(sql, tuple(args))

    @app.post("/api/notifications/test", dependencies=[api])
    def notifications_test() -> dict:
        eng.notify("test", None, None, f"{APP_NAME} test", "Notifications are working.", urgent=True)
        return {"ok": True}

    @app.post("/api/notifications/read", dependencies=[api])
    def notifications_read(body: dict = Body(default={})) -> dict:
        before = body.get("before_id")
        if before:
            eng.db.execute("UPDATE notifications SET read=1 WHERE read=0 AND id<=?", (int(before),))
        else:
            eng.db.execute("UPDATE notifications SET read=1 WHERE read=0")
        return {"ok": True}

    @app.post("/api/notifications/{nid}/read", dependencies=[api])
    def notification_read(nid: int) -> dict:
        eng.db.execute("UPDATE notifications SET read=1 WHERE id=?", (nid,))
        return {"ok": True}

    # -------------------------------------------------------------- mobile
    # System routes must be mounted BEFORE the catch-all static handler below.
    register_system_routes(app, eng, api)

    @app.get("/api/mobile", dependencies=[api])
    def mobile(request: Request) -> dict:
        port = int(eng.settings.get("mobile.port", 8765))
        host = eng.settings.get("mobile.host", "127.0.0.1")
        ips = lan_addresses()
        return {"host": host, "port": port, "lan": host != "127.0.0.1",
                "urls": [f"http://{ip}:{port}/?token={token}" for ip in ips], "local_url": f"http://127.0.0.1:{port}/?token={token}",
                "token": token, "ntfy": {"url": eng.settings.get("notifications.ntfy_url"), "topic": eng.settings.get("notifications.ntfy_topic")}}

    @app.post("/api/service/stop", dependencies=[api])
    def service_stop() -> dict:
        import os
        import threading
        threading.Timer(0.5, lambda: os._exit(0)).start()
        return {"ok": True}

    # --------------------------------------------------------------- events
    @app.get("/api/events", dependencies=[api])
    async def events(request: Request, since: int = 0, kind: str = "desktop") -> StreamingResponse:
        sid, q = eng.events.subscribe(kind, since)

        async def gen():
            loop = asyncio.get_running_loop()
            try:
                yield "retry: 2000\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await loop.run_in_executor(None, lambda: q.get(timeout=10))
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    yield f"id: {ev['id']}\ndata: {json.dumps(ev, default=str)}\n\n"
            finally:
                eng.events.unsubscribe(sid)
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/poll", dependencies=[api])
    def poll(after: int = 0) -> dict:
        evs = eng.events.since(after)
        return {"events": evs[-200:], "last": evs[-1]["id"] if evs else after}

    # -------------------------------------------------------------- the PWA
    web = paths.resource_root() / "web"

    @app.get("/")
    def index(request: Request) -> Response:
        t = request.query_params.get("token")
        if t:
            ip = request.client.host if request.client else "?"
            if check_token(t, ip):
                r = RedirectResponse("/", status_code=303)
                r.set_cookie("zt_token", token, httponly=True, samesite="strict", max_age=86400 * 365)
                return r
        return FileResponse(web / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/{name:path}")
    def static(name: str) -> Response:
        f = (web / name).resolve()
        if not name or not f.is_file() or not f.is_relative_to(web.resolve()):
            raise HTTPException(404)
        return FileResponse(f, headers={"Cache-Control": "no-cache"})

    # While any Bot turn is running, renew the activity lease so a long answer
    # or a slow tool is never cut off by the platform's idle pause.
    def _lease_loop() -> None:
        while True:
            try:
                if host_mod.configured() and eng.turns.active():
                    host_mod.lease(900)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(120)

    if host_mod.configured():
        threading.Thread(target=_lease_loop, daemon=True, name="host-lease").start()

    return app
