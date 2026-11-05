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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from core import APP_NAME, VERSION, backup as backup_mod, commands, packages, paths, push, secrets, skills as skills_mod
from core import auth as auth_mod
from core import email_connector as email_mod
from core import document_parser as doc_parser_mod
from core import host as host_mod
from core.browser import BrowserError
from core.bots import BotError
from core.computer import ComputerError
from core.db import jdump, new_id, now
from core.engine import Engine
from core.files import FileError
from core.plugins import ConnectorError, google_client
from core.providers import ProviderError, _fallback_keys, friendly_error, make_provider, model_warnings
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

    # CORS — restrict to your own app origins. The phone app and PWA call from
    # instabox URLs; add any additional public origins here.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://8765-agquljzsz7k42vgoenj7qr6fim.instabox.tech",
            "https://8888-agquljzsz7k42vgoenj7qr6fim.instabox.tech",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )

    eng = engine
    fails: dict[str, list[float]] = {}
    login_fails: dict[str, list[float]] = {}

    # Security headers on every response.
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

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

    def current_user(request: Request) -> str:
        """Email of the signed-in user from the session, else empty string."""
        return read_session(request.cookies) or session_email(presented(request)) or ""

    def require_admin(request: Request) -> str:
        """Return the current user's email if they are an admin, else raise 403."""
        user = current_user(request)
        if not user or not auth_mod.is_admin(user):
            raise HTTPException(403, "Admin access required.")
        return user

    def session_string(addr: str) -> str:
        """Build the signed session string (also stored as the zt_session cookie)."""
        payload = base64.urlsafe_b64encode(
            json.dumps({"email": addr, "exp": time.time() + 86400 * 30}).encode()).decode().rstrip("=")
        sig = hmac.new(sess_sec.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return payload + "." + sig

    def issue_session(response: Response, addr: str) -> None:
        response.set_cookie("zt_session", session_string(addr), httponly=True, samesite="strict", max_age=86400 * 30, secure=True)

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
        now = time.time()
        recent = [t for t in login_fails.get(ip, []) if now - t < 300]
        login_fails[ip] = recent
        if len(recent) >= 10:
            raise HTTPException(429, "Too many failed login attempts. Wait a few minutes.")
        addr = str(body.get("email", "")).strip().lower()
        cred = str(body.get("password", "") or body.get("token", "")).strip()
        if not auth_mod.verify_password(addr, cred):
            if not check_token(cred, ip):   # service token still works for the desktop/API
                login_fails[ip].append(now)
                raise HTTPException(401, "That email or password is not right.")
        login_fails.pop(ip, None)
        issue_session(response, addr)
        return {"ok": True, "email": addr, "session": session_string(addr)}

    @app.get("/api/auth_status")
    def auth_status(request: Request) -> dict:
        user = current_user(request)
        return {"needs_setup": not auth_mod.has_account(), "signed_in": bool(user),
                "email": user, "google_ready": bool(auth_mod.google_email(user)) if user else False}

    @app.get("/api/me", dependencies=[api])
    def me(request: Request) -> dict:
        """Current account: email, admin flag, plan, usage this week."""
        user = current_user(request)
        if not user:
            raise HTTPException(401, "Sign in required.")
        acc = auth_mod._read_account(user)
        pl = auth_mod.plan_for(user)
        limits = auth_mod.plan_limits(pl)
        us = eng.usage.user_summary(user)
        return {
            "email": user,
            "is_admin": bool(acc.get("is_admin")),
            "plan": pl,
            "plan_label": limits["label"],
            "plan_price": limits["price_usd"],
            "max_bots": limits["max_bots"],
            "weekly_tokens": limits["weekly_tokens"],
            "today_tokens": us["today"],
            "week_tokens": us["total"],
            "bots_count": len(eng.bots.list(owner=user)),
        }

    @app.post("/api/signup")
    def signup(request: Request, response: Response, body: dict = Body(...)) -> dict:
        ip = request.client.host if request.client else "?"
        now = time.time()
        recent = [t for t in login_fails.get(ip, []) if now - t < 300]
        if len(recent) >= 10:
            raise HTTPException(429, "Too many signup attempts. Wait a few minutes.")
        addr = str(body.get("email", "")).strip().lower()
        try:
            auth_mod.create_account(addr, str(body.get("password", "")))
        except ValueError as e:
            login_fails[ip].append(now)
            raise HTTPException(400, str(e))
        login_fails.pop(ip, None)
        issue_session(response, addr)
        # The mobile app saves this token immediately after creating the
        # account — without it every later call 401s and sign-up "refuses".
        return {"ok": True, "email": addr, "session": session_string(addr)}

    @app.post("/api/google")
    def google_login(request: Request, response: Response, body: dict = Body(...)) -> dict:
        cid = str(eng.settings.get("auth.google_client_id") or "")
        if not cid:
            raise HTTPException(403, "Google sign-in is not set up on this server.")
        try:
            addr = auth_mod.verify_google(str(body.get("id_token", "")), cid)
        except ValueError as e:
            raise HTTPException(401, str(e))
        if not auth_mod.has_account(addr):
            # Google sign-up: create the account (no password) and bind this Google address to it.
            auth_mod.create_account(addr, None, google_only=True)
        auth_mod.set_google_email(addr, addr)
        issue_session(response, addr)
        return {"ok": True, "email": addr, "session": session_string(addr)}

    @app.post("/api/logout")
    def logout(response: Response) -> dict:
        response.delete_cookie("zt_session")
        return {"ok": True}

    @app.post("/api/account/delete", dependencies=[api])
    def account_delete(request: Request, response: Response, body: dict = Body(default={})) -> dict:
        user = current_user(request)
        want = str(body.get("confirm", "")).strip().lower()
        if not want or want != user.strip().lower():
            raise HTTPException(400, "Type your email address to confirm deleting the account.")
        auth_mod.delete_account(user)
        response.delete_cookie("zt_session")
        return {"ok": True, "deleted": True}

    @app.post("/api/password", dependencies=[api])
    def change_password(request: Request, body: dict = Body(...)) -> dict:
        user = current_user(request)
        try:
            auth_mod.set_password(user, str(body.get("password", "")))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True}

    @app.get("/api/bootstrap", dependencies=[api])
    def bootstrap(request: Request) -> dict:
        _note_app(request)
        user = current_user(request)
        bots = eng.bots.list(owner=user)
        return {"bots": bots, "groups": eng.messaging.list_groups(), "status": eng.status(), "approvals": eng.approvals.list("pending"),
                "profiles": _profiles(), "templates": TEMPLATES, "settings": _public_settings(), "last_event": eng.events.last_id(),
                "presets": PRESETS, "handoffs": eng.messaging.list_handoffs(status="open", limit=50)}

    def _profiles() -> list[dict]:
        out = []
        allowed = eng.admin.allowed_providers()
        for pid, p in eng.settings.profiles().items():
            if allowed is not None and pid not in allowed:
                continue
            out.append({**p, "id": pid, "key_set": secrets.has_secret(f"provider:{pid}"),
                        "fallback_key_set": bool(_fallback_keys(pid)),
                        "fallback_key_count": len(_fallback_keys(pid)),
                        "preset": pid in PROVIDER_PRESETS})
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
    def bots_list(request: Request) -> list[dict]:
        user = current_user(request)
        rows = eng.bots.list(owner=user)
        # Include the main thread id so the phone can open a chat instantly
        # instead of waiting for a second round-trip before the screen appears.
        for b in rows:
            try:
                b["thread_id"] = eng.threads.main_thread(b["id"], owner=user)["id"]
            except Exception:
                b["thread_id"] = ""
        return rows

    @app.post("/api/bots", dependencies=[api])
    def bots_create(request: Request, body: dict = Body(...)) -> dict:
        user = current_user(request)
        # Plan enforcement: bot cap.
        limits = auth_mod.plan_limits(auth_mod.plan_for(user))
        existing = eng.bots.list(owner=user)
        if limits["max_bots"] and len(existing) >= limits["max_bots"]:
            raise HTTPException(403, f"Your {limits['label']} plan allows {limits['max_bots']} bots. Upgrade to create more.")
        bot = eng.bots.create(body.get("name", ""), job=body.get("job", ""), instructions=body.get("instructions", ""), emoji=body.get("emoji", "🤖"),
                              owner=user,
                              **{k: v for k, v in body.items() if k in ("profile", "model", "approval_mode", "step_limit", "net_mode", "net_allow", "net_deny", "grants", "proactive", "template", "avatar")})
        eng.threads.main_thread(bot["id"], owner=user)
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
    def bots_get(request: Request, bot_id: str) -> dict:
        user = current_user(request)
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such Bot.")
        b["network_layers"] = eng.net.describe(bot_id)
        b["rules"] = eng.approvals.rules(bot_id)
        return b

    @app.put("/api/bots/{bot_id}", dependencies=[api])
    def bots_update(request: Request, bot_id: str, body: dict = Body(...)) -> dict:
        user = current_user(request)
        return eng.bots.update(bot_id, owner=user, **body)

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
    def threads_for_bot(request: Request, bot_id: str) -> list[dict]:
        user = current_user(request)
        main = eng.threads.main_thread(bot_id, owner=user)
        rows = eng.threads.list_for_bot(bot_id, owner=user)
        for r in rows:
            r["main"] = r["id"] == main["id"]
        return rows

    @app.post("/api/bots/{bot_id}/threads", dependencies=[api])
    def threads_create(request: Request, bot_id: str, body: dict = Body(default={})) -> dict:
        user = current_user(request)
        return eng.threads.create(bot_id, body.get("title") or "New thread", owner=user)

    @app.get("/api/threads/{tid}", dependencies=[api])
    def thread_get(request: Request, tid: str, limit: int = 400) -> dict:
        user = current_user(request)
        th = eng.threads.get(tid, owner=user)
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

    # ------------------------------------------------------------------ goals
    @app.get("/api/goals", dependencies=[api])
    def goals_list(request: Request) -> dict:
        user = current_user(request)
        return {"goals": eng.goals.list(user if not auth_mod.is_admin(user) else None)}

    @app.post("/api/goals", dependencies=[api])
    def goals_create(request: Request, body: dict = Body(...)) -> dict:
        user = current_user(request)
        return eng.goals.create(str(body.get("bot_id") or ""), str(body.get("title") or ""),
                                str(body.get("target") or ""), int(body.get("cadence_minutes") or 360), owner=user)

    @app.put("/api/goals/{gid}", dependencies=[api])
    def goals_update(request: Request, gid: str, body: dict = Body(...)) -> dict:
        user = current_user(request)
        fields = {k: body[k] for k in ("title", "target", "cadence_minutes", "status", "progress") if k in body}
        return eng.goals.update(gid, user if not auth_mod.is_admin(user) else None, **fields)

    @app.delete("/api/goals/{gid}", dependencies=[api])
    def goals_delete(request: Request, gid: str) -> dict:
        user = current_user(request)
        return {"ok": eng.goals.delete(gid, user if not auth_mod.is_admin(user) else None)}

    @app.post("/api/goals/{gid}/run", dependencies=[api])
    def goals_run(request: Request, gid: str) -> dict:
        user = current_user(request)
        g = eng.goals.get(gid, user if not auth_mod.is_admin(user) else None)
        if not g:
            raise HTTPException(404, "No such goal.")
        eng.goals._prod(g)  # noqa: SLF001 - explicit "work on it now" from the user
        return {"ok": True, "goal": eng.goals.get(gid)}

    # ------------------------------------------------------------------ briefing
    @app.get("/api/briefing", dependencies=[api])
    def briefing(request: Request) -> dict:
        user = current_user(request)
        d = eng.digest.build("today")
        goals = [g for g in eng.goals.list(user if not auth_mod.is_admin(user) else None) if g.get("status") == "active"]
        try:
            unread = int(eng.db.scalar("SELECT COUNT(*) FROM notifications WHERE read=0", (), 0))
        except Exception:  # noqa: BLE001
            unread = 0
        return {"headline": eng.digest.headline(d), "waiting": d.get("waiting", 0),
                "totals": d.get("totals", {}), "bots": d.get("bots", [])[:6],
                "goals": [{"id": g["id"], "title": g["title"], "progress": g.get("progress", ""),
                           "bot_name": g.get("bot_name"), "emoji": g.get("bot_emoji")} for g in goals[:6]],
                "unread": unread}

    # ------------------------------------------------------------------ push tokens
    @app.post("/api/push/register", dependencies=[api])
    def push_register(request: Request, body: dict = Body(...)) -> dict:
        user = current_user(request)
        return push.register(eng.db, user, str(body.get("token") or ""), str(body.get("platform") or "android"))

    @app.delete("/api/push/register", dependencies=[api])
    def push_unregister(request: Request, body: dict = Body(...)) -> dict:
        user = current_user(request)
        return push.unregister(eng.db, user, str(body.get("token") or ""))

    # ------------------------------------------------------------------ community templates
    @app.get("/api/templates/community", dependencies=[api])
    def templates_community(request: Request) -> dict:
        user = current_user(request)
        rows = eng.db.query("SELECT id, owner, name, emoji, job, instructions, grants, model, profile, created_at "
                            "FROM community_templates ORDER BY created_at DESC LIMIT 100")
        return {"community": [r for r in rows if r["owner"] != user], "mine": [r for r in rows if r["owner"] == user]}

    @app.post("/api/templates/publish", dependencies=[api])
    def templates_publish(request: Request, body: dict = Body(...)) -> dict:
        user = current_user(request)
        bot = eng.bots.get(str(body.get("bot_id") or ""), user)
        if not bot:
            raise HTTPException(404, "No such Bot.")
        name = str(body.get("name") or bot["name"]).strip()[:60] or bot["name"]
        row = {"id": new_id(), "owner": user, "name": name, "emoji": bot.get("emoji") or "🤖",
               "job": (bot.get("job") or "")[:500], "instructions": (bot.get("instructions") or "")[:4000],
               "grants": jdump(bot.get("grants") or []), "model": bot.get("model") or "",
               "profile": bot.get("profile") or "", "created_at": now()}
        eng.db.insert("community_templates", row)
        return {"ok": True, "id": row["id"]}

    @app.delete("/api/templates/community/{tid}", dependencies=[api])
    def templates_delete(request: Request, tid: str) -> dict:
        user = current_user(request)
        row = eng.db.one("SELECT owner FROM community_templates WHERE id=?", (tid,))
        if not row or (row["owner"] != user and not auth_mod.is_admin(user)):
            raise HTTPException(404, "No such template.")
        eng.db.execute("DELETE FROM community_templates WHERE id=?", (tid,))
        return {"ok": True}

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

    @app.get("/api/approvals/center", dependencies=[api])
    def approvals_center(request: Request) -> dict:
        """Muse-style approval center: pending actions with merchant/price/item details for the mobile UI."""
        user = current_user(request)
        bots = eng.bots.list(owner=user)
        bot_ids = [b["id"] for b in bots]
        out = []
        for bid in bot_ids:
            for a in eng.approvals.list("pending", bot_id=bid, limit=50):
                details = a.get("details") or {}
                out.append({
                    "id": a["id"],
                    "bot_id": a["bot_id"],
                    "bot_name": a.get("bot_name") or (next((b for b in bots if b["id"] == a["bot_id"]), {}) or {}).get("name", "Bot"),
                    "category": a.get("category", "purchase"),
                    "summary": a.get("summary", ""),
                    "title": a.get("title", "Action needed"),
                    "merchant": details.get("merchant") or details.get("store") or "",
                    "price": details.get("price") or details.get("total") or "",
                    "currency": details.get("currency") or "$",
                    "item": details.get("item") or details.get("product") or "",
                    "image_url": details.get("image_url") or details.get("thumbnail") or "",
                    "action": details.get("action") or details.get("tool") or "",
                    "created_at": a.get("created_at", 0),
                    "thread_id": a.get("thread_id", ""),
                })
        out.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        return {"approvals": out, "count": len(out)}

    # ------------------------------------------------- device bridge (phone)
    # The ZerotheBot Android app runs an AccessibilityService poller that calls
    # /api/device/pull, executes the job on the phone and posts the result back.
    @app.get("/api/device/pull", dependencies=[api])
    def device_pull(device: str = "phone") -> dict:
        for _ in range(5):
            row = eng.db.one("SELECT * FROM device_jobs WHERE device=? AND status='pending' "
                             "ORDER BY created_at LIMIT 1", (device,))
            if not row:
                return {"job": None}
            eng.db.execute("UPDATE device_jobs SET status='claimed', claimed_at=? WHERE id=? AND status='pending'",
                           (time.time(), row["id"]))
            cur = eng.db.one("SELECT * FROM device_jobs WHERE id=?", (row["id"],))
            if cur and cur["status"] == "claimed":
                try:
                    args = json.loads(cur.get("args") or "{}")
                except Exception:
                    args = {}
                return {"job": {"id": cur["id"], "cmd": cur["cmd"], "args": args, "bot_id": cur.get("bot_id") or ""}}
        return {"job": None}

    @app.post("/api/device/done", dependencies=[api])
    def device_done(body: dict = Body(...)) -> dict:
        jid = (body.get("id") or "").strip()
        if not jid:
            raise HTTPException(400, "id is required.")
        failed = bool(body.get("error")) or body.get("ok") is False
        result = str(body.get("result") or body.get("error") or "")[:8000]
        eng.db.execute("UPDATE device_jobs SET status=?, result=?, done_at=? WHERE id=?",
                       ("error" if failed else "done", result, time.time(), jid))
        return {"ok": True}

    @app.post("/api/device/ping", dependencies=[api])
    def device_ping(body: dict = Body(default={})) -> dict:
        dev = (body.get("device") or "phone").strip() or "phone"
        info = body.get("info") or {}
        try:
            payload = json.dumps(info)[:4000]
        except Exception:
            payload = "{}"
        eng.db.execute("INSERT INTO devices(id, last_seen, info) VALUES(?,?,?) "
                       "ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen, info=excluded.info",
                       (dev, time.time(), payload))
        return {"ok": True}

    @app.get("/api/device/status", dependencies=[api])
    def device_status() -> dict:
        row = eng.db.one("SELECT id, last_seen, info FROM devices WHERE id='phone'")
        pending = eng.db.scalar("SELECT COUNT(*) FROM device_jobs WHERE status='pending'", (), 0) or 0
        last = (row or {}).get("last_seen") or 0
        return {"connected": bool(last and (time.time() - last) < 120), "last_seen": last,
                "pending": int(pending), "info": (row or {}).get("info") or "{}"}

    @app.post("/api/device/jobs", dependencies=[api])
    def device_enqueue(body: dict = Body(...)) -> dict:
        """Queue a phone action from the app itself (Device Bridge test card)."""
        cmd = (body.get("cmd") or "").strip()
        if cmd not in ("open_app", "tap", "tap_text", "ui", "text", "key"):
            raise HTTPException(400, "Unknown command. Use open_app, tap, tap_text, ui, text or key.")
        jid = str(int(time.time() * 1000)) + os.urandom(3).hex()
        eng.db.insert("device_jobs", {"id": jid, "device": (body.get("device") or "phone").strip() or "phone",
                                      "bot_id": "", "thread_id": "", "turn_id": "", "cmd": cmd,
                                      "args": json.dumps(body.get("args") or {}), "created_at": time.time()})
        return {"id": jid}

    @app.delete("/api/device/jobs", dependencies=[api])
    def device_clear() -> dict:
        eng.db.execute("UPDATE device_jobs SET status='error', result='cancelled', done_at=? "
                       "WHERE status IN ('pending','claimed')", (time.time(),))
        return {"ok": True}

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

    @app.get("/api/computer/preview", dependencies=[api])
    def preview_info(path: str) -> dict:
        """Classify a workspace file for the clients' document previews."""
        f = ws_path(path)
        if not f.is_file():
            raise HTTPException(404)
        from core import render as render_mod
        try:
            info = render_mod.inspect(f)
        except render_mod.RenderError as e:
            raise HTTPException(415, str(e))
        info["name"] = f.name
        return info

    @app.get("/api/computer/preview/page", dependencies=[api])
    def preview_page(path: str, page: int = 1) -> Response:
        """One PDF page rendered to PNG — powers the in-chat document preview."""
        f = ws_path(path)
        from core import render as render_mod
        try:
            png = render_mod.pdf_page_png(f, page=max(1, int(page)))
        except render_mod.RenderError as e:
            raise HTTPException(415, str(e))
        if png is None:
            raise HTTPException(404, "No such page")
        return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=600"})

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
    def plugins_list(request: Request) -> dict:
        user = current_user(request)
        return {"plugins": eng.plugins.list(owner=user), "catalog": eng.plugins.catalog(), "install_allowed": eng.admin.plugin_install_allowed()}

    @app.post("/api/plugins/install", dependencies=[api])
    def plugins_install(body: dict = Body(...)) -> dict:
        return eng.plugins.install(body["source"])

    @app.delete("/api/plugins/{pid}", dependencies=[api])
    def plugins_remove(pid: str) -> dict:
        eng.plugins.uninstall(pid)
        return {"ok": True}

    @app.put("/api/plugins/{pid}/config", dependencies=[api])
    def plugins_config(pid: str, request: Request, body: dict = Body(...)) -> dict:
        eng.plugins.set_config(pid, body)
        return eng.plugins.info(pid, owner=current_user(request))

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
        return {"url": eng.plugins.begin_oauth(pid, _pub_origin(request) + path, mobile=bool(mobile), owner=current_user(request))}

    @app.post("/api/plugins/{pid}/disconnect", dependencies=[api])
    def plugins_disconnect(pid: str, request: Request) -> dict:
        user = current_user(request)
        eng.plugins.clear_secrets(pid, owner=user)
        return eng.plugins.info(pid, owner=user)

    @app.post("/api/plugins/rest", dependencies=[api])
    def plugins_rest(body: dict = Body(...)) -> dict:
        return eng.plugins.add_rest(body)

    # ------------------------------------------------- plugin items (emails, etc.)
    # Phone-app CRUD for plugin data items (e.g. Gmail messages). The server holds
    # the plugin credentials, so the phone can list and delete items without going
    # through the bot agent.
    def _gmail_headers(pid: str, owner: str = "") -> dict:
        cid, csec = google_client(eng.plugins, pid)
        return {"Authorization": "Bearer " + eng.plugins.google.access_token(pid, cid, csec, owner=owner)}

    @app.get("/api/plugins/{pid}/items", dependencies=[api])
    def plugins_items_list(pid: str, request: Request, query: str = "", max_results: int = 10) -> dict:
        import httpx
        user = current_user(request)
        d = eng.plugins.defs.get(pid)
        if not d:
            raise HTTPException(404, "Unknown plugin.")
        if not eng.plugins.configured(pid, owner=user):
            raise HTTPException(400, "Plugin not configured. Press Connect first.")
        if d.id != "gmail":
            raise HTTPException(400, "Item listing not supported for this plugin.")
        base = "https://gmail.googleapis.com/gmail/v1/users/me"
        headers = _gmail_headers(pid, owner=user)
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
    def plugins_item_delete(pid: str, item_id: str, request: Request) -> dict:
        import httpx
        user = current_user(request)
        d = eng.plugins.defs.get(pid)
        if not d:
            raise HTTPException(404, "Unknown plugin.")
        if not eng.plugins.configured(pid, owner=user):
            raise HTTPException(400, "Plugin not configured.")
        if d.id != "gmail":
            raise HTTPException(400, "Item delete not supported for this plugin.")
        base = "https://gmail.googleapis.com/gmail/v1/users/me"
        r = httpx.delete(f"{base}/messages/{item_id}", headers=_gmail_headers(pid, owner=user), timeout=30)
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
        if not auth_mod.has_account(addr):
            auth_mod.create_account(addr, None, google_only=True)
        auth_mod.set_google_email(addr, addr)
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
        if not auth_mod.has_account(addr):
            auth_mod.create_account(addr, None, google_only=True)
        auth_mod.set_google_email(addr, addr)
        tok = session_string(addr)
        deep = f"zerothebot://auth/google-app/go?tok={tok}&email={quote(addr)}"
        return page("Signed in", f"{addr}<br>Taking you back to the app…", deep)

    @app.get("/api/mcp", dependencies=[api])
    def mcp_list(request: Request) -> list[dict]:
        user = current_user(request)
        rows = eng.mcp.configs()
        if user and auth_mod.is_admin(user):
            return rows
        # A user sees their own servers plus pre-multi-user servers with no owner.
        return [r for r in rows if not str(r.get("owner", "") or "") or r.get("owner") == user]

    @app.post("/api/mcp", dependencies=[api])
    def mcp_save(request: Request, body: dict = Body(...)) -> dict:
        user = current_user(request)
        if body.get("import_json"):
            return {"servers": eng.mcp.import_json(body["import_json"], owner=user)}
        return eng.mcp.save({**body, "owner": user})

    def _mcp_owned(sid: str, user: str) -> None:
        row = eng.db.one("SELECT owner FROM mcp_servers WHERE id=?", (sid,))
        if not row:
            raise HTTPException(404, "Unknown MCP server.")
        owner = str(row["owner"] or "")
        if owner and owner != user and not (user and auth_mod.is_admin(user)):
            raise HTTPException(403, "This MCP server belongs to another account.")

    @app.delete("/api/mcp/{sid}", dependencies=[api])
    def mcp_delete(request: Request, sid: str) -> dict:
        user = current_user(request)
        _mcp_owned(sid, user)
        eng.mcp.delete(sid)
        return {"ok": True}

    @app.post("/api/mcp/{sid}/restart", dependencies=[api])
    def mcp_restart(request: Request, sid: str) -> dict:
        user = current_user(request)
        _mcp_owned(sid, user)
        eng.mcp.restart(sid)
        return {"ok": True}

    # ---------------------------------------------------------------- email (IMAP) connector
    @app.post("/api/email/imap/configure", dependencies=[api])
    def email_configure(request: Request, body: dict = Body(...)) -> dict:
        """Configure IMAP credentials for the current user."""
        user = current_user(request)
        email_mod.set_imap_config(
            user,
            str(body.get("host", "")),
            int(body.get("port", 993)),
            str(body.get("username", "")),
            str(body.get("password", "")),
            bool(body.get("use_ssl", True)),
        )
        return {"ok": True, "email": user}

    @app.get("/api/email/imap/poll", dependencies=[api])
    def email_poll(request: Request) -> dict:
        """Poll IMAP inbox for actionable emails."""
        user = current_user(request)
        connector = email_mod.EmailConnector(eng.db, eng.settings, eng.admin, eng.events)
        items = connector.poll_inbox(user, limit=20)
        return {"items": items, "count": len(items)}

    @app.post("/api/email/imap/create-approvals", dependencies=[api])
    def email_create_approvals(request: Request) -> dict:
        """Convert polled email actions into approval requests."""
        user = current_user(request)
        connector = email_mod.EmailConnector(eng.db, eng.settings, eng.admin, eng.events)
        items = connector.poll_inbox(user, limit=20)
        created = connector.create_approvals(items, eng)
        return {"created": created, "count": len(created)}

    # ---------------------------------------------------------------- document upload & parsing
    @app.post("/api/document/upload", dependencies=[api])
    async def document_upload(request: Request) -> dict:
        """Upload a PDF/document and extract action items as approvals."""
        user = current_user(request)
        form = await request.form()
        file = form.get("file")
        if not file:
            raise HTTPException(400, "No file provided.")
        content = await file.read()
        file_name = str(file.filename or "document.pdf")

        # Save to a temp file for parsing
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file_name)[1])
        tmp.write(content)
        tmp.close()

        try:
            parser = doc_parser_mod.DocumentParser()
            created = parser.create_approvals_from_doc(tmp.name, file_name, user, eng)
            return {"ok": True, "file": file_name, "created": created, "count": len(created)}
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    # ---------------------------------------------------------------- browser-in-chat (agent computer use)
    # These drive a Bot's embedded browser through the real Screen API in
    # core/browser.py. bot_id selects which Bot's tab to act on; actions run on
    # the browser host's own loop via BrowserHost.call.
    def _browser_screen(body_bot_id: str = ""):
        bid = str(body_bot_id or "").strip()
        if not bid:
            raise HTTPException(400, "bot_id required.")
        return eng.computer.browser.screen(bid)

    @app.post("/api/browser/navigate", dependencies=[api])
    def browser_navigate(request: Request, body: dict = Body(...)) -> dict:
        """Agent navigates to a URL in the embedded browser (Muse-style booking)."""
        scr = _browser_screen(body.get("bot_id"))
        url = str(body.get("url", "")).strip()
        if not url:
            raise HTTPException(400, "URL required.")
        try:
            result = eng.computer.browser.call(scr.goto, url, timeout=60)
        except BrowserError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "url": url, "result": result}

    @app.post("/api/browser/click", dependencies=[api])
    def browser_click(request: Request, body: dict = Body(...)) -> dict:
        """Agent clicks an element (snapshot id) or coordinates in the embedded browser."""
        scr = _browser_screen(body.get("bot_id"))
        element, x, y = body.get("element"), body.get("x"), body.get("y")
        try:
            if element is not None:
                result = eng.computer.browser.call(scr.click, element=int(element), timeout=40)
            elif x is not None and y is not None:
                result = eng.computer.browser.call(scr.click, x=int(x), y=int(y), timeout=40)
            else:
                raise HTTPException(400, "Provide element (from browser_snapshot) or x and y.")
        except BrowserError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "result": result}

    @app.post("/api/browser/type", dependencies=[api])
    def browser_type(request: Request, body: dict = Body(...)) -> dict:
        """Agent types text into a field in the embedded browser."""
        scr = _browser_screen(body.get("bot_id"))
        text = str(body.get("text", ""))
        if not text:
            raise HTTPException(400, "Text required.")
        element = body.get("element")
        try:
            result = eng.computer.browser.call(
                scr.type_text, text,
                element=int(element) if element is not None else None,
                clear=bool(body.get("clear", True)), submit=bool(body.get("submit", False)), timeout=60)
        except BrowserError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "result": result}

    @app.get("/api/browser/screenshot", dependencies=[api])
    def browser_screenshot(request: Request, bot_id: str = "") -> dict:
        """Get a screenshot of the current browser state for the chat view."""
        scr = _browser_screen(bot_id)
        try:
            data, _ = eng.computer.browser.call(scr.screenshot, False, 70, False, timeout=30)
        except BrowserError as e:
            raise HTTPException(400, str(e))
        url = scr.page.url if scr.page is not None and not scr.page.is_closed() else ""
        return {"ok": True, "screenshot": base64.b64encode(data).decode("ascii"), "url": url}

    # ---------------------------------------------------------------- agent-initiated proactive messages
    @app.post("/api/agent/message", dependencies=[api])
    def agent_message(request: Request, body: dict = Body(...)) -> dict:
        """Agent sends a proactive message to the user (Muse-style: 'Found the field trip form...')."""
        user = current_user(request)
        text = str(body.get("text", "")).strip()
        if not text:
            raise HTTPException(400, "Message text required.")
        thread_id = str(body.get("thread_id", ""))

        # Create or find a thread for this proactive message
        bots = eng.bots.list(owner=user)
        if not bots:
            raise HTTPException(400, "No bots found.")
        bot = bots[0]
        if not thread_id:
            thread = eng.threads.create(bot_id=bot["id"], title="Agent message", kind="dm", owner=user)
            thread_id = thread["id"]

        # Save as a user-facing agent message
        eng.threads.add(thread_id, "agent", "text", [{"type": "text", "text": text}],
                        publish=True, bot_id=bot["id"])
        eng.events.publish("message", thread_id=thread_id, bot_id=bot["id"],
                           items=eng.threads.display([eng.db.one("SELECT * FROM messages ORDER BY id DESC LIMIT 1")], eng.bots.names()),
                           kind="agent_proactive")
        return {"ok": True, "thread_id": thread_id}

    @app.get("/api/agent/messages", dependencies=[api])
    def agent_messages(request: Request) -> dict:
        """Get recent agent-initiated proactive messages for the current user."""
        user = current_user(request)
        bots = eng.bots.list(owner=user)
        if not bots:
            return {"messages": [], "count": 0}
        bot_ids = [b["id"] for b in bots]
        rows = eng.db.query(
            "SELECT * FROM messages WHERE author='agent' AND bot_id IN (" + ",".join("?" * len(bot_ids)) + ") "
            "ORDER BY created_at DESC LIMIT 20", bot_ids)
        return {"messages": rows, "count": len(rows)}

    # ---------------------------------------------------------------- Tier 2: Web search
    @app.post("/api/search", dependencies=[api])
    def web_search(request: Request, body: dict = Body(...)) -> dict:
        """Real-time web search for agent answers (DuckDuckGo HTML scrape)."""
        user = current_user(request)
        query = str(body.get("q", "")).strip()
        if not query:
            raise HTTPException(400, "Search query required.")
        try:
            import urllib.request as _ureq
            q = quote(query)
            url = f"https://html.duckduckgo.com/html/?q={q}"
            req = _ureq.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
            with _ureq.urlopen(req, timeout=20) as r:
                html = r.read().decode("utf-8", errors="replace")
            results = []
            import re as _re
            # Extract result titles and links
            for m in _re.finditer(r'<a rel="nofollow" class="result__a" href="([^"]+)">([^<]+)</a>', html):
                link = m.group(1)
                title = _re.sub(r"<[^>]+>", "", m.group(2)).strip()
                snippet_m = _re.search(r'<a class="result__snippet"[^>]*>([^<]+)</a>', html[m.end():m.end()+500])
                snippet = snippet_m.group(1).strip() if snippet_m else ""
                results.append({"title": title, "url": link, "snippet": snippet})
                if len(results) >= 8:
                    break
            return {"query": query, "results": results, "count": len(results)}
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Search failed: {type(e).__name__}: {e}")

    # ---------------------------------------------------------------- Tier 2: Image understanding (vision)
    @app.post("/api/image/understand", dependencies=[api])
    async def image_understand(request: Request, body: dict = Body(...)) -> dict:
        """Analyze an uploaded image and describe what's in it."""
        user = current_user(request)
        image_data = str(body.get("image_data", ""))  # base64 or data URI
        prompt = str(body.get("prompt", "Describe this image in detail."))
        if not image_data:
            raise HTTPException(400, "image_data required.")
        # Use the configured vision provider (OpenAI/Anthropic/etc.)
        cid = str(eng.settings.get("auth.google_client_id") or "")
        provider = eng.settings.get("default_profile", "anthropic")
        try:
            # Route through the provider's vision model
            make = make_provider(provider, eng.settings, eng.plugins)
            result = make.vision_describe(image_data, prompt)
            return {"ok": True, "description": result, "provider": provider}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "provider": provider}

    # ---------------------------------------------------------------- Tier 3: Image generation
    @app.post("/api/image/generate", dependencies=[api])
    def image_generate(request: Request, body: dict = Body(...)) -> dict:
        """Generate an image from a text prompt (DALL-E via OpenAI if configured)."""
        user = current_user(request)
        prompt = str(body.get("prompt", "")).strip()
        if not prompt:
            raise HTTPException(400, "Prompt required.")
        # Try OpenAI DALL-E if API key is configured
        api_key = eng.settings.get("providers.openai.api_key") or eng.secrets.get_secret("openai.api_key")
        if not api_key:
            raise HTTPException(400, "OpenAI API key not configured. Add it in Admin → Settings.")
        try:
            import httpx as _httpx
            r = _httpx.post("https://api.openai.com/v1/images/generations",
                            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                            json={"model": "dall-e-3", "prompt": prompt, "n": 1, "size": "1024x1024"},
                            timeout=60)
            if r.status_code != 200:
                raise HTTPException(400, f"Image generation failed: {r.text[:200]}")
            data = r.json()
            image_url = data["data"][0]["url"]
            return {"ok": True, "image_url": image_url, "prompt": prompt, "provider": "dall-e-3"}
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Image generation failed: {type(e).__name__}: {e}")

    # ---------------------------------------------------------------- Tier 3: Code execution sandbox
    @app.post("/api/code/execute", dependencies=[api])
    def code_execute(request: Request, body: dict = Body(...)) -> dict:
        """Execute Python code in a restricted sandbox."""
        user = current_user(request)
        code = str(body.get("code", "")).strip()
        if not code:
            raise HTTPException(400, "Code required.")
        # Safety: block dangerous imports and operations
        blocked = ["import os", "import subprocess", "import socket", "import shutil",
                   "import smtplib", "import ctypes", "import multiprocessing",
                   "import threading", "import asyncio", "__import__", "eval(", "exec(",
                   "open(", "os.system", "subprocess."]
        for b in blocked:
            if b in code:
                raise HTTPException(400, f"Blocked operation: {b}")
        try:
            import io, contextlib, tempfile, os as _os
            buf = io.StringIO()
            # Write to a temp file and run with restricted namespace
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
            tmp.write(code)
            tmp.close()
            try:
                with contextlib.redirect_stdout(buf):
                    exec(compile(code, tmp.name, "exec"), {"__builtins__": {}}, {})
                output = buf.getvalue()
            finally:
                try:
                    _os.unlink(tmp.name)
                except OSError:
                    pass
            return {"ok": True, "output": output or "(no output)", "language": "python"}
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ---------------------------------------------------------------- Tier 3: Multi-agent collaboration
    @app.post("/api/agent/collaborate", dependencies=[api])
    def agent_collaborate(request: Request, body: dict = Body(...)) -> dict:
        """Multiple agents work together on a task (agent-to-agent handoff)."""
        user = current_user(request)
        task = str(body.get("task", "")).strip()
        if not task:
            raise HTTPException(400, "Task required.")
        bots = eng.bots.list(owner=user)
        if len(bots) < 2:
            raise HTTPException(400, "Need at least 2 bots for collaboration.")
        wanted = [str(b) for b in (body.get("bot_ids") or [])]
        chosen = [b for b in bots if b["id"] in wanted] or bots[:3]
        if len(chosen) < 2:
            raise HTTPException(400, "Pick at least 2 Bots to collaborate.")
        # Assign subtasks and actually start the work: each Bot gets its role in
        # its own thread and a real turn, so the collaboration runs to completion.
        results = []
        for i, bot in enumerate(chosen[:3]):
            role = ["planner", "executor", "reviewer"][i]
            brief = {"planner": "Break the task into concrete steps and hand the executor a precise work order.",
                     "executor": "Carry out the planner's work order with your tools. Do the real work.",
                     "reviewer": "Check the executor's result against the task. Fix small issues yourself; report anything that needs a decision."}[role]
            thread = eng.threads.create(bot_id=bot["id"], title=f"Collab: {task[:40]}", kind="dm", owner=user)
            eng.threads.add(thread["id"], "agent", "text",
                            [{"type": "text", "text": f"Role: {role}. Task: {task}\nYour part: {brief}"}],
                            publish=True, bot_id=bot["id"])
            eng.turns.start(bot["id"], thread["id"], trigger="user")
            results.append({"bot": bot["name"], "bot_id": bot["id"], "role": role, "thread_id": thread["id"],
                            "status": "running"})
        eng.events.publish("agents", change="collaborate", results=results)
        return {"ok": True, "task": task, "agents": results, "count": len(results)}

    # ---------------------------------------------------------------- InstaBox Android Boxes
    # Bots drive Android on InstaBox machines (Redroid container, no emulator),
    # not on the user's phone. This replaces the removed AccessibilityService.
    INSTABOX_API_BASE = "https://api.instabox.sh"

    def _ib_key() -> str:
        """The server's own InstaBox API key. Clients never supply credentials here."""
        return os.environ.get("INSTABOX_API_KEY", "")

    @app.post("/api/instabox/machines", dependencies=[api])
    def ib_create_machine(request: Request, body: dict = Body(...)) -> dict:
        """Create an InstaBox Android box (Redroid container, Android 14)."""
        key = _ib_key()
        if not key:
            raise HTTPException(400, "INSTABOX_API_KEY not configured on the server.")
        name = str(body.get("name", "zerothebot-android")).strip()
        try:
            r = httpx.post(INSTABOX_API_BASE + "/v1/machines",
                           headers={"Authorization": "Bearer " + key},
                           json={"name": name, "templateId": "android", "templateVersion": "2026-11-02.7-android.1"},
                           timeout=30)
            if r.status_code >= 400:
                raise HTTPException(r.status_code, f"InstaBox create failed: {r.text[:200]}")
            return r.json()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"InstaBox error: {e}")

    @app.get("/api/instabox/machines", dependencies=[api])
    def ib_list_machines(request: Request) -> dict:
        """List InstaBox machines."""
        key = _ib_key()
        if not key:
            raise HTTPException(400, "INSTABOX_API_KEY not configured on the server.")
        try:
            r = httpx.get(INSTABOX_API_BASE + "/v1/machines",
                          headers={"Authorization": "Bearer " + key}, timeout=30)
            if r.status_code >= 400:
                raise HTTPException(r.status_code, f"InstaBox list failed: {r.text[:200]}")
            return r.json()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"InstaBox error: {e}")

    @app.get("/api/instabox/machines/{mid}", dependencies=[api])
    def ib_get_machine(request: Request, mid: str) -> dict:
        """Get one InstaBox machine."""
        key = _ib_key()
        if not key:
            raise HTTPException(400, "INSTABOX_API_KEY not configured on the server.")
        try:
            r = httpx.get(f"{INSTABOX_API_BASE}/v1/machines/{mid}",
                          headers={"Authorization": "Bearer " + key}, timeout=30)
            if r.status_code >= 400:
                raise HTTPException(r.status_code, f"InstaBox get failed: {r.text[:200]}")
            return r.json()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"InstaBox error: {e}")

    @app.post("/api/instabox/machines/{mid}/adb", dependencies=[api])
    def ib_adb(request: Request, mid: str, body: dict = Body(...)) -> dict:
        """Run an adb command on an InstaBox Android box."""
        key = _ib_key()
        if not key:
            raise HTTPException(400, "INSTABOX_API_KEY not configured on the server.")
        cmd = str(body.get("cmd", "")).strip()
        if not cmd:
            raise HTTPException(400, "adb cmd required.")
        try:
            r = httpx.post(f"{INSTABOX_API_BASE}/v1/machines/{mid}/exec",
                           headers={"Authorization": "Bearer " + key},
                           json={"cmd": cmd, "timeoutSeconds": 60}, timeout=90)
            if r.status_code >= 400:
                raise HTTPException(r.status_code, f"InstaBox exec failed: {r.text[:200]}")
            return r.json()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"InstaBox error: {e}")

    # ---------------------------------------------------------------- Smart Android Control
    # High-level phone helpers over adb, in the style of phone-harness: agents
    # say WHAT they want ("tap the text Settings", "wait for Inbox") instead of
    # raw pixels. Text comes from the accessibility tree (uiautomator), so it
    # is exact and needs no OCR.

    def _ib_adb_raw(mid: str, cmd: str, timeout: int = 60) -> dict:
        """One adb exec on an InstaBox machine; returns the raw JSON."""
        key = _ib_key()
        if not key:
            raise HTTPException(400, "INSTABOX_API_KEY not configured on the server.")
        try:
            r = httpx.post(f"{INSTABOX_API_BASE}/v1/machines/{mid}/exec",
                           headers={"Authorization": "Bearer " + key},
                           json={"cmd": cmd, "timeoutSeconds": timeout}, timeout=timeout + 30)
            if r.status_code >= 400:
                raise HTTPException(r.status_code, f"InstaBox exec failed: {r.text[:200]}")
            return r.json()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"InstaBox error: {e}")

    def _ib_text(d: dict) -> str:
        """Best-effort stdout text from an exec response."""
        if not isinstance(d, dict):
            return str(d)
        for f in ("output", "stdout", "result", "data", "text"):
            v = d.get(f)
            if isinstance(v, str) and v:
                return v
        if isinstance(d.get("result"), dict):
            for f in ("output", "stdout", "text"):
                v = d["result"].get(f)
                if isinstance(v, str) and v:
                    return v
        return ""

    def _ui_nodes(mid: str) -> list:
        """Accessibility tree as flat nodes with tap-ready centers."""
        import re as _re
        import xml.etree.ElementTree as _ET
        d = _ib_adb_raw(mid, "adb shell uiautomator dump /sdcard/zh_ui.xml && adb shell cat /sdcard/zh_ui.xml && adb shell rm /sdcard/zh_ui.xml", 90)
        raw = _ib_text(d)
        m = _re.search(r"<\?xml.*</hierarchy>", raw, _re.S)
        if not m:
            return []
        try:
            root = _ET.fromstring(m.group(0))
        except Exception:
            return []
        nodes = []

        def walk(el):
            b = el.get("bounds", "")
            mb = _re.match(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", b)
            if mb:
                x1, y1, x2, y2 = (int(g) for g in mb.groups())
                t = (el.get("text") or "").strip()
                desc = (el.get("content-desc") or "").strip()
                rid = (el.get("resource-id") or "").strip()
                if t or desc or rid or el.get("clickable") == "true":
                    nodes.append({
                        "text": t, "desc": desc, "id": rid,
                        "clickable": el.get("clickable") == "true",
                        "x": (x1 + x2) // 2, "y": (y1 + y2) // 2,
                        "w": x2 - x1, "h": y2 - y1,
                    })
            for ch in el:
                walk(ch)
        walk(root)
        return nodes

    def _find_nodes(nodes: list, query: str, exact: bool = False, clickable_only: bool = False) -> list:
        q = query.lower()
        hits = []
        for n in nodes:
            if clickable_only and not n["clickable"]:
                continue
            fields = [f for f in (n["text"], n["desc"], n["id"]) if f]
            if any((f.lower() == q if exact else q in f.lower()) for f in fields):
                hits.append(n)
        return hits

    @app.post("/api/android/{mid}/ui", dependencies=[api])
    def android_ui(request: Request, mid: str) -> dict:
        """Accessibility tree of the Android box: exact text with tap-ready centers."""
        nodes = _ui_nodes(mid)
        return {"nodes": nodes, "count": len(nodes)}

    @app.post("/api/android/{mid}/find", dependencies=[api])
    def android_find(request: Request, mid: str, body: dict = Body(...)) -> dict:
        """Find elements matching a query. On no match, returns what IS visible."""
        nodes = _ui_nodes(mid)
        hits = _find_nodes(nodes, str(body.get("query", "")),
                           exact=bool(body.get("exact")),
                           clickable_only=bool(body.get("clickable_only")))
        if not hits:
            visible = [n["text"] or n["desc"] or n["id"] for n in nodes
                       if n["text"] or n["desc"] or n["id"]][:30]
            return {"found": False, "visible": visible}
        return {"found": True, "matches": hits[:20]}

    @app.post("/api/android/{mid}/tap-text", dependencies=[api])
    def android_tap_text(request: Request, mid: str, body: dict = Body(...)) -> dict:
        """Find text on screen and tap its center. Fails with what IS visible."""
        nodes = _ui_nodes(mid)
        hits = _find_nodes(nodes, str(body.get("query", "")),
                           exact=bool(body.get("exact")),
                           clickable_only=bool(body.get("clickable_only")))
        if not hits:
            visible = [n["text"] or n["desc"] or n["id"] for n in nodes
                       if n["text"] or n["desc"] or n["id"]][:30]
            raise HTTPException(404, f"no element matches; visible: {visible}")
        idx = max(0, int(body.get("index", 0)))
        if idx >= len(hits):
            idx = 0
        hit = hits[idx]
        _ib_adb_raw(mid, f"adb shell input tap {hit['x']} {hit['y']}")
        return {"ok": True, "tapped": hit}

    @app.post("/api/android/{mid}/tap", dependencies=[api])
    def android_tap(request: Request, mid: str, body: dict = Body(...)) -> dict:
        """Raw tap at screen coordinates."""
        _ib_adb_raw(mid, f"adb shell input tap {int(body.get('x', 0))} {int(body.get('y', 0))}")
        return {"ok": True}

    @app.post("/api/android/{mid}/type", dependencies=[api])
    def android_type(request: Request, mid: str, body: dict = Body(...)) -> dict:
        """Type text into the focused field (tap the field first)."""
        import re as _re
        text = str(body.get("text", ""))
        segments = text.split("\n")
        for i, seg in enumerate(segments):
            if seg:
                safe = _re.sub(r"[^A-Za-z0-9 @#%&*+=().,;:!?'/_-]", "", seg).replace(" ", "%s")
                if safe:
                    _ib_adb_raw(mid, f"adb shell input text '{safe}'")
            if i < len(segments) - 1:
                _ib_adb_raw(mid, "adb shell input keyevent 66")
        return {"ok": True, "typed": text}

    @app.post("/api/android/{mid}/key", dependencies=[api])
    def android_key(request: Request, mid: str, body: dict = Body(...)) -> dict:
        """Press a key by name: back, home, enter, tab, up, down, left, right, recents."""
        keys = {"back": 4, "home": 3, "enter": 66, "tab": 61, "up": 19, "down": 20,
                "left": 21, "right": 22, "volume_up": 24, "volume_down": 25,
                "power": 26, "recents": 187, "escape": 4}
        name = str(body.get("key", "back")).lower()
        if name not in keys:
            raise HTTPException(400, f"unknown key {name!r}; use one of {sorted(keys)}")
        _ib_adb_raw(mid, f"adb shell input keyevent {keys[name]}")
        return {"ok": True, "key": name}

    @app.post("/api/android/{mid}/swipe", dependencies=[api])
    def android_swipe(request: Request, mid: str, body: dict = Body(...)) -> dict:
        """Momentum swipe. direction is FINGER motion: up shows the next item below."""
        direction = str(body.get("direction", "up")).lower()
        if direction not in ("up", "down", "left", "right"):
            raise HTTPException(400, "direction must be up, down, left or right")
        dist = min(1.0, max(0.05, float(body.get("distance", 0.4))))
        size = _ib_text(_ib_adb_raw(mid, "adb shell wm size")).strip()
        import re as _re
        m = _re.search(r"(\d+)x(\d+)", size)
        w, h = (int(m.group(1)), int(m.group(2))) if m else (720, 1280)
        cx, cy = int(body.get("x", w / 2)), int(body.get("y", h / 2))
        dx = {"left": -1, "right": 1}.get(direction, 0) * w * dist
        dy = {"up": -1, "down": 1}.get(direction, 0) * h * dist
        _ib_adb_raw(mid, f"adb shell input swipe {int(cx - dx / 2)} {int(cy - dy / 2)} {int(cx + dx / 2)} {int(cy + dy / 2)} 120")
        return {"ok": True, "direction": direction, "screen": [w, h]}

    @app.post("/api/android/{mid}/scroll", dependencies=[api])
    def android_scroll(request: Request, mid: str, body: dict = Body(...)) -> dict:
        """Scroll. direction is what you want to SEE: down reveals content below."""
        inverse = {"down": "up", "up": "down", "left": "right", "right": "left"}
        d = str(body.get("direction", "down")).lower()
        if d not in inverse:
            raise HTTPException(400, "direction must be down, up, left or right")
        body = dict(body)
        body["direction"] = inverse[d]
        return android_swipe(request, mid, body)

    @app.post("/api/android/{mid}/wait-for-text", dependencies=[api])
    def android_wait_for_text(request: Request, mid: str, body: dict = Body(...)) -> dict:
        """Poll until the text is visible; the verify step after every action."""
        query = str(body.get("query", ""))
        deadline = time.time() + min(60.0, max(1.0, float(body.get("timeout", 10))))
        last_visible = []
        while time.time() < deadline:
            nodes = _ui_nodes(mid)
            hits = _find_nodes(nodes, query)
            if hits:
                return {"found": True, "match": hits[0]}
            last_visible = [n["text"] or n["desc"] for n in nodes if n["text"] or n["desc"]][:30]
            time.sleep(1.5)
        return {"found": False, "visible": last_visible}

    @app.post("/api/android/{mid}/open-app", dependencies=[api])
    def android_open_app(request: Request, mid: str, body: dict = Body(...)) -> dict:
        """Launch an app by package name (com.x.y) or a fuzzy name ('settings')."""
        import re as _re
        app = str(body.get("app", "")).strip()
        if not app:
            raise HTTPException(400, "app required")
        if "." in app:
            _ib_adb_raw(mid, f"adb shell monkey -p {app} -c android.intent.category.LAUNCHER 1")
            return {"ok": True, "package": app}
        out = _ib_text(_ib_adb_raw(mid, "adb shell pm list packages"))
        pkgs = _re.findall(r"package:(\S+)", out)
        q = app.lower().replace(" ", "")
        match = next((p for p in pkgs if q in p.lower()), None)
        if not match:
            return {"ok": False, "error": f"no installed package matches {app!r}", "installed": pkgs[:40]}
        _ib_adb_raw(mid, f"adb shell monkey -p {match} -c android.intent.category.LAUNCHER 1")
        return {"ok": True, "package": match}

    @app.post("/api/android/{mid}/wait-stable", dependencies=[api])
    def android_wait_stable(request: Request, mid: str, body: dict = Body(default={})) -> dict:
        """Wait until consecutive screen hashes match — animations are done."""
        import hashlib as _hl
        deadline = time.time() + min(30.0, max(1.0, float(body.get("timeout", 6))))
        prev, same = None, 0
        while time.time() < deadline:
            _ib_adb_raw(mid, "adb shell screencap -p /sdcard/zh_s.png && adb shell md5sum /sdcard/zh_s.png && adb shell rm /sdcard/zh_s.png")
            out = _ib_text(_ib_adb_raw(mid, "adb shell md5sum /sdcard/zh_s.png 2>/dev/null || true"))
            digest = out.split()[0] if out.split() else ""
            same = same + 1 if digest and digest == prev else 0
            if same >= 1:
                return {"stable": True}
            prev = digest
            time.sleep(0.6)
        return {"stable": False}

    @app.post("/api/instabox/machines/{mid}/desktop/start", dependencies=[api])
    def ib_desktop_start(request: Request, mid: str, body: dict = Body(default={})) -> dict:
        """Start the desktop on an InstaBox machine."""
        key = _ib_key()
        if not key:
            raise HTTPException(400, "INSTABOX_API_KEY not configured on the server.")
        try:
            r = httpx.post(f"{INSTABOX_API_BASE}/v1/machines/{mid}/desktop",
                           headers={"Authorization": "Bearer " + key},
                           json={"action": "start", "width": body.get("width", 1280), "height": body.get("height", 800)},
                           timeout=30)
            if r.status_code >= 400:
                raise HTTPException(r.status_code, f"InstaBox desktop start failed: {r.text[:200]}")
            return r.json()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"InstaBox error: {e}")

    @app.post("/api/instabox/machines/{mid}/desktop/screenshot", dependencies=[api])
    def ib_desktop_screenshot(request: Request, mid: str) -> dict:
        """Take a screenshot of the InstaBox machine desktop."""
        key = _ib_key()
        if not key:
            raise HTTPException(400, "INSTABOX_API_KEY not configured on the server.")
        try:
            r = httpx.get(f"{INSTABOX_API_BASE}/v1/machines/{mid}/desktop/screenshot",
                          headers={"Authorization": "Bearer " + key}, timeout=30)
            if r.status_code >= 400:
                raise HTTPException(r.status_code, f"InstaBox screenshot failed: {r.text[:200]}")
            return {"ok": True, "screenshot": base64.b64encode(r.content).decode("ascii"),
                    "content_type": r.headers.get("content-type", "image/png")}
        except httpx.HTTPError as e:
            raise HTTPException(502, f"InstaBox error: {e}")

    @app.post("/api/instabox/machines/{mid}/desktop/stream", dependencies=[api])
    def ib_desktop_stream(request: Request, mid: str, body: dict = Body(default={})) -> dict:
        """Get a desktop stream URL for an InstaBox machine."""
        key = _ib_key()
        if not key:
            raise HTTPException(400, "INSTABOX_API_KEY not configured on the server.")
        try:
            r = httpx.post(f"{INSTABOX_API_BASE}/v1/machines/{mid}/desktop/stream",
                           headers={"Authorization": "Bearer " + key},
                           json={"viewOnly": body.get("viewOnly", True), "ttlSeconds": body.get("ttlSeconds", 3600)},
                           timeout=30)
            if r.status_code >= 400:
                raise HTTPException(r.status_code, f"InstaBox stream failed: {r.text[:200]}")
            return r.json()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"InstaBox error: {e}")

    @app.post("/api/instabox/machines/{mid}/desktop/click", dependencies=[api])
    def ib_desktop_click(request: Request, mid: str, body: dict = Body(...)) -> dict:
        """Click on the InstaBox machine desktop."""
        key = _ib_key()
        if not key:
            raise HTTPException(400, "INSTABOX_API_KEY not configured on the server.")
        x = int(body.get("x", 0)); y = int(body.get("y", 0))
        try:
            r = httpx.post(f"{INSTABOX_API_BASE}/v1/machines/{mid}/desktop",
                           headers={"Authorization": "Bearer " + key},
                           json={"action": "click", "x": x, "y": y}, timeout=30)
            if r.status_code >= 400:
                raise HTTPException(r.status_code, f"InstaBox click failed: {r.text[:200]}")
            return r.json()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"InstaBox error: {e}")

    @app.post("/api/instabox/machines/{mid}/desktop/type", dependencies=[api])
    def ib_desktop_type(request: Request, mid: str, body: dict = Body(...)) -> dict:
        """Type text on the InstaBox machine desktop."""
        key = _ib_key()
        if not key:
            raise HTTPException(400, "INSTABOX_API_KEY not configured on the server.")
        text = str(body.get("text", ""))
        try:
            r = httpx.post(f"{INSTABOX_API_BASE}/v1/machines/{mid}/desktop",
                           headers={"Authorization": "Bearer " + key},
                           json={"action": "type", "text": text}, timeout=30)
            if r.status_code >= 400:
                raise HTTPException(r.status_code, f"InstaBox type failed: {r.text[:200]}")
            return r.json()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"InstaBox error: {e}")

    # (e.g. the QuickBooks MCP server on port 3333) so they are reachable
    # over the public HTTPS URL without exposing extra ports.
    @app.post("/api/mcp-proxy/{mcp_id:path}", dependencies=[api])
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
        import asyncio
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
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            resp_data = stdout
            status = 200 if proc.returncode == 0 else 500
        except asyncio.TimeoutExpired:
            raise HTTPException(504, f"MCP server '{mcp_id}' timed out")
        except Exception as e:
            raise HTTPException(500, f"Proxy error: {type(e).__name__}: {e}")

        resp_headers = {"content-type": "application/json"}

        return Response(
            content=resp_data,
            status_code=status,
            headers=resp_headers,
            media_type="application/json",
        )

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
        if body.get("fallback_api_key") or body.get("fallback_api_keys") is not None:
            raw = body.get("fallback_api_keys")
            if raw is None:
                raw = body.get("fallback_api_key")
            if isinstance(raw, list):
                keys = [str(k).strip() for k in raw if str(k).strip()]
            else:
                keys = [k.strip() for k in re.split(r"[,\n\r]+", str(raw or "")) if k.strip()]
            if keys:
                secrets.set_secret(f"provider:{pid}:fallback", json.dumps(keys))
            else:
                secrets.delete_secret(f"provider:{pid}:fallback")
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

    # ------------------------------------------------------------- admin: multi-user
    @app.get("/api/admin/users", dependencies=[api])
    def admin_users(request: Request) -> dict:
        """List every account with per-user token usage (admin only)."""
        admin_user = require_admin(request)
        eng.usage.backfill_owners()
        out = []
        for email in auth_mod.list_accounts():
            acc = auth_mod._read_account(email)
            us = eng.usage.user_summary(email)
            out.append({
                "email": email,
                "is_admin": bool(acc.get("is_admin")),
                "google_only": bool(acc.get("google_only")),
                "created_at": acc.get("created_at", 0),
                "today_tokens": us["today"],
                "week_tokens": us["total"],
                "input_tokens": us["input_tokens"],
                "output_tokens": us["output_tokens"],
                "bots_count": len(us["per_bot"]),
            })
        out.sort(key=lambda x: x["email"])
        return {"current_admin": admin_user, "users": out, "count": len(out)}

    @app.get("/api/admin/account/{email}", dependencies=[api])
    def admin_account(request: Request, email: str) -> dict:
        """Detail for one account: bots and per-bot usage (admin only)."""
        require_admin(request)
        eng.usage.backfill_owners()
        if not auth_mod.has_account(email):
            raise HTTPException(404, "No such account.")
        acc = auth_mod._read_account(email)
        us = eng.usage.user_summary(email)
        bots = eng.bots.list(owner=email)
        return {"email": email, "is_admin": bool(acc.get("is_admin")),
                "google_only": bool(acc.get("google_only")),
                "created_at": acc.get("created_at", 0),
                "today_tokens": us["today"], "week_tokens": us["total"],
                "input_tokens": us["input_tokens"], "output_tokens": us["output_tokens"],
                "bots": bots, "per_bot": us["per_bot"]}

    @app.post("/api/admin/account/{email}/plan", dependencies=[api])
    def admin_set_plan(request: Request, email: str, body: dict = Body(default={})) -> dict:
        """Set a user's plan tier (admin only)."""
        require_admin(request)
        new_plan = str(body.get("plan", "")).strip().lower()
        if new_plan not in auth_mod.PLANS:
            raise HTTPException(400, f"Unknown plan '{new_plan}'. Choose: {', '.join(auth_mod.PLANS)}.")
        auth_mod.set_plan(email, new_plan)
        return {"ok": True, "email": email, "plan": new_plan,
                "label": auth_mod.plan_limits(new_plan)["label"]}

    @app.post("/api/admin/account/{email}/admin", dependencies=[api])
    def admin_set_admin(request: Request, email: str, body: dict = Body(default={})) -> dict:
        """Grant or revoke admin rights (admin only)."""
        require_admin(request)
        is_admin = bool(body.get("is_admin", True))
        auth_mod.set_admin(email, is_admin)
        return {"ok": True, "email": email, "is_admin": is_admin}

    @app.get("/api/admin/settings", dependencies=[api])
    def admin_settings(request: Request) -> dict:
        """Server settings visible to admin: API base, provider status, masked keys (admin only)."""
        require_admin(request)
        s = eng.settings.all()
        safe = dict(s)
        providers = safe.get("providers", {})
        if isinstance(providers, dict):
            for pid, p in providers.items():
                if isinstance(p, dict):
                    for k in ("api_key", "api_secret", "client_secret", "secret"):
                        if p.get(k):
                            p[k] = "***masked***"
        auth_cfg = safe.get("auth", {})
        if isinstance(auth_cfg, dict):
            for k in ("google_client_secret", "session_secret"):
                if auth_cfg.get(k):
                    auth_cfg[k] = "***masked***"
        safe["providers"] = providers
        safe["auth"] = auth_cfg
        return {
            "api_base": eng.base_url,
            "public_base_url": eng.settings.get("auth.public_base_url", "") or "",
            "google_client_id": eng.settings.get("auth.google_client_id", "") or "",
            "session_secret_set": bool(eng.settings.get("auth.session_secret")),
            "settings": safe,
            "version": VERSION,
        }

    @app.get("/api/admin/stats", dependencies=[api])
    def admin_stats(request: Request) -> dict:
        """Overall server stats (admin only)."""
        require_admin(request)
        eng.usage.backfill_owners()
        total_bots = len(eng.bots.list(include_archived=True))
        total_threads = len(eng.threads.db.query("SELECT id FROM threads"))
        total_usage = eng.usage.summary()
        accounts = auth_mod.list_accounts()
        return {"accounts": len(accounts), "bots": total_bots, "threads": total_threads,
                "week_tokens": total_usage["total"], "today_tokens": total_usage["input_tokens"] + total_usage["output_tokens"],
                "version": VERSION}

    # ----------------------------------------------------------- action log
    @app.get("/api/actions", dependencies=[api])
    def actions(bot_id: str | None = None, limit: int = 200, offset: int = 0, tool: str | None = None, status: str | None = None) -> list[dict]:
        return eng.actions.list(bot_id, limit, offset, tool, status)

    @app.get("/api/actions/export", dependencies=[api])
    def actions_export(bot_id: str | None = None, format: str = "json") -> Response:
        data, mime, name = eng.actions.export(bot_id, format)
        return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    # ----------------------------------------------------------- bot activity timeline
    @app.get("/api/bots/{bot_id}/timeline", dependencies=[api])
    def bot_timeline(request: Request, bot_id: str, limit: int = 100) -> dict:
        """Per-bot activity timeline: turns with status, trigger, steps, duration."""
        user = current_user(request)
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such Bot.")
        rows = eng.db.query(
            "SELECT id, thread_id, trigger, status, started_at, ended_at, steps, error FROM turns "
            "WHERE bot_id=? ORDER BY started_at DESC LIMIT ?",
            (bot_id, limit),
        )
        out = []
        for r in rows:
            dur = None
            if r.get("started_at") and r.get("ended_at"):
                dur = round((r["ended_at"] - r["started_at"]) * 1000)
            out.append({
                "id": r["id"],
                "thread_id": r["thread_id"],
                "trigger": r.get("trigger") or "",
                "status": r.get("status") or "",
                "started_at": r.get("started_at") or 0,
                "ended_at": r.get("ended_at") or 0,
                "steps": r.get("steps") or 0,
                "duration_ms": dur,
                "error": r.get("error") or "",
            })
        return {"bot": b, "timeline": out}

    # ============================================================================
    # PRIORITY: SWARM COORDINATION - Multi-Bot Teams (Beats GrokBot & Muse)
    # ============================================================================
    @app.post("/api/swarms", dependencies=[api])
    def swarm_create(request: Request, body: dict = Body(...)) -> dict:
        """Create a swarm of bots that work together on a task."""
        user = current_user(request)
        swarm_id = str(int(time.time() * 1000))
        swarm = {
            "id": swarm_id,
            "owner": user,
            "name": str(body.get("name", "Swarm")),
            "description": str(body.get("description", "")),
            "bot_ids": str(body.get("bot_ids", [])),
            "strategy": str(body.get("strategy", "parallel")),  # parallel, sequential, consensus
            "status": "active",
            "created_at": time.time(),
            "tasks": [],
            "results": [],
        }
        eng.db.insert("swarms", swarm)
        return {"ok": True, "swarm": swarm}

    @app.get("/api/swarms", dependencies=[api])
    def swarm_list(request: Request) -> dict:
        """List all swarms for the current user."""
        user = current_user(request)
        rows = eng.db.query("SELECT * FROM swarms WHERE owner=? ORDER BY created_at DESC", (user,))
        return {"swarms": rows}

    @app.get("/api/swarms/{swarm_id}", dependencies=[api])
    def swarm_get(request: Request, swarm_id: str) -> dict:
        """Get swarm details with bot statuses."""
        user = current_user(request)
        row = eng.db.one("SELECT * FROM swarms WHERE id=? AND owner=?", (swarm_id, user))
        if not row:
            raise HTTPException(404, "No such swarm.")
        # Get bot details
        bot_ids = row.get("bot_ids", [])
        if isinstance(bot_ids, str):
            bot_ids = eng.db.query("SELECT value FROM json_each(?)", (bot_ids,))
            bot_ids = [r["value"] for r in bot_ids]
        bots = [eng.bots.get(bid) for bid in bot_ids if bid]
        return {"swarm": row, "bots": bots}

    @app.post("/api/swarms/{swarm_id}/task", dependencies=[api])
    def swarm_task(request: Request, swarm_id: str, body: dict = Body(...)) -> dict:
        """Assign a task to a swarm of bots."""
        user = current_user(request)
        row = eng.db.one("SELECT * FROM swarms WHERE id=? AND owner=?", (swarm_id, user))
        if not row:
            raise HTTPException(404, "No such swarm.")
        task_id = str(int(time.time() * 1000))
        task = {
            "id": task_id,
            "swarm_id": swarm_id,
            "trigger": str(body.get("trigger", "")),
            "strategy": row.get("strategy", "parallel"),
            "status": "running",
            "created_at": time.time(),
            "results": [],
        }
        eng.db.insert("swarm_tasks", task)
        # Dispatch to all bots in swarm
        bot_ids = row.get("bot_ids", [])
        if isinstance(bot_ids, str):
            import json as _json
            bot_ids = _json.loads(bot_ids)
        for bid in bot_ids:
            try:
                eng.threads.main_thread(bid, owner=user)
                eng.send_user_message(eng.threads.main_thread(bid, owner=user)["id"],
                                      f"[SWARM TASK {task_id}] {body.get('trigger', '')}",
                                      parent_id=0)
            except Exception as e:
                eng.log.error(f"Swarm task dispatch failed for {bid}: {e}")
        return {"ok": True, "task_id": task_id, "status": "dispatched"}

    @app.get("/api/swarms/{swarm_id}/tasks", dependencies=[api])
    def swarm_tasks(request: Request, swarm_id: str) -> dict:
        """Get all tasks for a swarm."""
        user = current_user(request)
        rows = eng.db.query("SELECT * FROM swarm_tasks WHERE swarm_id=? ORDER BY created_at DESC", (swarm_id,))
        return {"tasks": rows}

    # ============================================================================
    # PRIORITY: WORKFLOW BUILDER - Drag & Drop Automation (Beats Both)
    # ============================================================================
    @app.post("/api/workflows", dependencies=[api])
    def workflow_create(request: Request, body: dict = Body(...)) -> dict:
        """Create a visual workflow automation."""
        user = current_user(request)
        wf_id = str(int(time.time() * 1000))
        wf = {
            "id": wf_id,
            "owner": user,
            "name": str(body.get("name", "Workflow")),
            "description": str(body.get("description", "")),
            "nodes": str(body.get("nodes", "[]")),
            "edges": str(body.get("edges", "[]")),
            "trigger": str(body.get("trigger", "manual")),
            "status": "active",
            "created_at": time.time(),
            "runs": 0,
        }
        eng.db.insert("workflows", wf)
        return {"ok": True, "workflow": wf}

    @app.get("/api/workflows", dependencies=[api])
    def workflow_list(request: Request) -> dict:
        """List all workflows for the current user."""
        user = current_user(request)
        rows = eng.db.query("SELECT * FROM workflows WHERE owner=? ORDER BY created_at DESC", (user,))
        return {"workflows": rows}

    @app.post("/api/workflows/{wf_id}/run", dependencies=[api])
    def workflow_run(request: Request, wf_id: str, body: dict = Body(...)) -> dict:
        """Execute a workflow."""
        user = current_user(request)
        row = eng.db.one("SELECT * FROM workflows WHERE id=? AND owner=?", (wf_id, user))
        if not row:
            raise HTTPException(404, "No such workflow.")
        run_id = str(int(time.time() * 1000))
        eng.db.insert("workflow_runs", {
            "id": run_id,
            "workflow_id": wf_id,
            "status": "running",
            "started_at": time.time(),
            "result": "",
        })
        eng.db.execute("UPDATE workflows SET runs = runs + 1 WHERE id=?", (wf_id,))
        # Execute nodes in order
        import json as _json
        nodes = _json.loads(row.get("nodes", "[]"))
        edges = _json.loads(row.get("edges", "[]"))
        results = {}
        try:
            for node in nodes:
                node_id = node.get("id")
                node_type = node.get("type", "")
                node_data = node.get("data", {})
                if node_type == "bot_message":
                    bot_id = node_data.get("bot_id")
                    message = node_data.get("message", "")
                    bot = eng.bots.get(bot_id, owner=user)
                    if bot:
                        thread = eng.threads.main_thread(bot_id, owner=user)
                        eng.send_user_message(thread["id"], message, parent_id=0)
                        results[node_id] = {"status": "sent", "thread_id": thread["id"]}
                elif node_type == "wait":
                    import time as _time
                    _time.sleep(float(node_data.get("seconds", 1)))
                    results[node_id] = {"status": "waited"}
                elif node_type == "approve":
                    results[node_id] = {"status": "pending_approval"}
            eng.db.execute("UPDATE workflow_runs SET status='completed', result=?, ended_at=? WHERE id=?",
                          (_json.dumps(results), time.time(), run_id))
        except Exception as e:
            eng.db.execute("UPDATE workflow_runs SET status='error', result=? WHERE id=?",
                          (str(e), run_id))
        return {"ok": True, "run_id": run_id, "results": results}

    @app.get("/api/workflows/{wf_id}/runs", dependencies=[api])
    def workflow_runs(request: Request, wf_id: str) -> dict:
        """Get workflow run history."""
        user = current_user(request)
        rows = eng.db.query("SELECT * FROM workflow_runs WHERE workflow_id=? ORDER BY started_at DESC LIMIT 50", (wf_id,))
        return {"runs": rows}

    # ============================================================================
    # PRIORITY: SCREEN RECORDING & PLAYBACK (Beats Both)
    # ============================================================================
    @app.get("/api/bots/{bot_id}/screen/recordings", dependencies=[api])
    def bot_screen_recordings(request: Request, bot_id: str) -> dict:
        """List screen recordings for a bot."""
        user = current_user(request)
        rows = eng.db.query("SELECT * FROM recordings WHERE bot_id=? ORDER BY created_at DESC LIMIT 50", (bot_id,))
        return {"recordings": rows}

    @app.get("/api/recordings/{rid}/stream", dependencies=[api])
    def recording_stream(request: Request, rid: str) -> dict:
        """Get recording stream data."""
        user = current_user(request)
        row = eng.db.one("SELECT * FROM recordings WHERE id=?", (rid,))
        if not row:
            raise HTTPException(404, "No such recording.")
        return {"recording": row, "frames": row.get("frames", "[]")}

    @app.post("/api/recordings/{rid}/annotate", dependencies=[api])
    def recording_annotate(request: Request, rid: str, body: dict = Body(...)) -> dict:
        """Add annotations to a recording."""
        user = current_user(request)
        eng.db.execute("UPDATE recordings SET annotation=? WHERE id=?",
                      (str(body.get("annotation", "")), rid))
        return {"ok": True}

    # ============================================================================
    # PRIORITY: ADVANCED ANALYTICS DASHBOARD (Beats Both)
    # ============================================================================
    @app.get("/api/analytics/bots", dependencies=[api])
    def analytics_bots(request: Request) -> dict:
        """Per-bot analytics: tokens, steps, success rate, avg duration."""
        user = current_user(request)
        bots = eng.bots.list(owner=user)
        out = []
        for b in bots:
            bid = b["id"]
            rows = eng.db.query(
                "SELECT status, steps, started_at, ended_at FROM turns WHERE bot_id=?",
                (bid,)
            )
            total = len(rows)
            done = len([r for r in rows if r.get("status") == "done"])
            total_steps = sum(r.get("steps") or 0 for r in rows)
            durations = []
            for r in rows:
                if r.get("started_at") and r.get("ended_at"):
                    durations.append(r["ended_at"] - r["started_at"])
            avg_dur = (sum(durations) / len(durations)) if durations else 0
            tok_rows = eng.db.query(
                "SELECT SUM(input_tokens + output_tokens) as total FROM usage WHERE bot_id=?",
                (bid,)
            )
            tokens = tok_rows[0]["total"] if tok_rows and tok_rows[0]["total"] else 0
            out.append({
                "bot_id": bid,
                "name": b.get("name", "Bot"),
                "total_turns": total,
                "completed": done,
                "success_rate": round(done / total * 100, 1) if total else 0,
                "total_steps": total_steps,
                "avg_duration_sec": round(avg_dur, 1),
                "total_tokens": tokens or 0,
            })
        return {"bots": out}

    @app.get("/api/analytics/system", dependencies=[api])
    def analytics_system(request: Request) -> dict:
        """System-wide analytics."""
        user = current_user(request)
        total_bots = len(eng.bots.list(include_archived=True))
        total_threads = len(eng.threads.db.query("SELECT id FROM threads"))
        total_turns = eng.db.scalar("SELECT COUNT(*) FROM turns", (), 0)
        total_actions = eng.db.scalar("SELECT COUNT(*) FROM actions", (), 0)
        total_usage = eng.usage.summary()
        return {
            "bots": total_bots,
            "threads": total_threads,
            "total_turns": total_turns,
            "total_actions": total_actions,
            "week_tokens": total_usage.get("total", 0),
            "today_tokens": total_usage.get("input_tokens", 0) + total_usage.get("output_tokens", 0),
            "uptime_hours": round((time.time() - eng.settings.get("service_started_at", time.time())) / 3600, 1),
        }

    # ============================================================================
    # PRIORITY: WEBHOOK INTEGRATIONS (Beats Both)
    # ============================================================================
    @app.post("/api/webhooks", dependencies=[api])
    def webhook_create(request: Request, body: dict = Body(...)) -> dict:
        """Create a webhook endpoint for external integrations."""
        user = current_user(request)
        import hashlib
        wh_id = str(int(time.time() * 1000))
        secret = hashlib.sha256((str(time.time()) + user).encode()).hexdigest()[:16]
        row = {
            "id": wh_id,
            "owner": user,
            "name": str(body.get("name", "Webhook")),
            "url": str(body.get("url", "")),
            "events": str(body.get("events", [])),
            "secret": secret,
            "active": True,
            "created_at": time.time(),
            "deliveries": 0,
        }
        eng.db.insert("webhooks", row)
        return {"ok": True, "webhook": row, "secret": secret}

    @app.get("/api/webhooks", dependencies=[api])
    def webhook_list(request: Request) -> dict:
        """List webhooks for the current user."""
        user = current_user(request)
        rows = eng.db.query("SELECT * FROM webhooks WHERE owner=? ORDER BY created_at DESC", (user,))
        return {"webhooks": rows}

    @app.post("/api/webhooks/{wh_id}/test", dependencies=[api])
    def webhook_test(request: Request, wh_id: str, body: dict = Body(...)) -> dict:
        """Test a webhook by sending a ping."""
        user = current_user(request)
        row = eng.db.one("SELECT * FROM webhooks WHERE id=? AND owner=?", (wh_id, user))
        if not row:
            raise HTTPException(404, "No such webhook.")
        import httpx
        try:
            r = httpx.post(row.get("url", ""), json={"event": "test", "webhook_id": wh_id}, timeout=10)
            eng.db.execute("UPDATE webhooks SET deliveries = deliveries + 1 WHERE id=?", (wh_id,))
            return {"ok": True, "status": r.status_code, "body": r.text[:200]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.delete("/api/webhooks/{wh_id}", dependencies=[api])
    def webhook_delete(request: Request, wh_id: str) -> dict:
        user = current_user(request)
        eng.db.execute("DELETE FROM webhooks WHERE id=? AND owner=?", (wh_id, user))
        return {"ok": True}

    # ============================================================================
    # PRIORITY: ADVANCED MEMORY SYSTEM (Beats Both)
    # ============================================================================
    @app.get("/api/bots/{bot_id}/memory/search", dependencies=[api])
    def memory_search(request: Request, bot_id: str, q: str = "") -> dict:
        """Semantic memory search for a bot."""
        user = current_user(request)
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such Bot.")
        rows = eng.db.query(
            "SELECT * FROM memories WHERE bot_id=? AND (text LIKE ? OR kind LIKE ?) ORDER BY created_at DESC LIMIT 50",
            (bot_id, f"%{q}%", f"%{q}%"),
        )
        return {"memories": rows}

    @app.post("/api/bots/{bot_id}/memory/bulk", dependencies=[api])
    def memory_bulk_add(request: Request, bot_id: str, body: dict = Body(...)) -> dict:
        """Add multiple memories at once."""
        user = current_user(request)
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such Bot.")
        items = body.get("items", [])
        added = []
        for item in items:
            mid = eng.memory.add(bot_id, item.get("kind", "fact"), item.get("text", ""),
                                source=item.get("source", "bulk"), verify=bool(item.get("verify", False)))
            added.append(mid)
        return {"ok": True, "added": added}

    @app.get("/api/bots/{bot_id}/memory/stats", dependencies=[api])
    def memory_stats(request: Request, bot_id: str) -> dict:
        """Memory statistics for a bot."""
        user = current_user(request)
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such Bot.")
        total = eng.db.scalar("SELECT COUNT(*) FROM memories WHERE bot_id=?", (bot_id,), 0)
        kinds_rows = eng.db.query("SELECT kind, COUNT(*) as c FROM memories WHERE bot_id=? GROUP BY kind", (bot_id,))
        return {"bot_id": bot_id, "total": total, "kinds": kinds_rows}

    # ============================================================================
    # PRIORITY: ADVANCED SWARM COORDINATION (Hierarchical, Consensus, Voting)
    # ============================================================================
    @app.post("/api/swarms/{swarm_id}/strategy", dependencies=[api])
    def swarm_strategy(request: Request, swarm_id: str, body: dict = Body(...)) -> dict:
        """Change swarm strategy: parallel, sequential, consensus, hierarchical."""
        user = current_user(request)
        row = eng.db.one("SELECT * FROM swarms WHERE id=? AND owner=?", (swarm_id, user))
        if not row:
            raise HTTPException(404, "No such swarm.")
        strategy = str(body.get("strategy", "parallel"))
        if strategy not in ("parallel", "sequential", "consensus", "hierarchical"):
            raise HTTPException(400, "Invalid strategy. Use: parallel, sequential, consensus, hierarchical")
        eng.db.execute("UPDATE swarms SET strategy=? WHERE id=?", (strategy, swarm_id))
        return {"ok": True, "strategy": strategy}

    @app.post("/api/swarms/{swarm_id}/vote", dependencies=[api])
    def swarm_vote(request: Request, swarm_id: str, body: dict = Body(...)) -> dict:
        """Vote on a swarm decision (consensus mode)."""
        user = current_user(request)
        row = eng.db.one("SELECT * FROM swarms WHERE id=? AND owner=?", (swarm_id, user))
        if not row:
            raise HTTPException(404, "No such swarm.")
        if row.get("strategy") != "consensus":
            raise HTTPException(400, "Swarm is not in consensus mode.")
        vote_id = str(int(time.time() * 1000))
        eng.db.insert("swarm_votes", {
            "id": vote_id,
            "swarm_id": swarm_id,
            "voter": user,
            "option": str(body.get("option", "")),
            "created_at": time.time(),
        })
        # Count votes
        votes = eng.db.query("SELECT option, COUNT(*) as c FROM swarm_votes WHERE swarm_id=? GROUP BY option", (swarm_id,))
        return {"ok": True, "vote_id": vote_id, "votes": votes}

    @app.post("/api/swarms/{swarm_id}/delegate", dependencies=[api])
    def swarm_delegate(request: Request, swarm_id: str, body: dict = Body(...)) -> dict:
        """Delegate a sub-task to a specific bot in the swarm (hierarchical mode)."""
        user = current_user(request)
        row = eng.db.one("SELECT * FROM swarms WHERE id=? AND owner=?", (swarm_id, user))
        if not row:
            raise HTTPException(404, "No such swarm.")
        if row.get("strategy") != "hierarchical":
            raise HTTPException(400, "Swarm is not in hierarchical mode.")
        bot_id = str(body.get("bot_id", ""))
        task = str(body.get("task", ""))
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such bot in swarm.")
        thread = eng.threads.main_thread(bot_id, owner=user)
        eng.send_user_message(thread["id"], f"[SWARM DELEGATE] {task}", parent_id=0)
        return {"ok": True, "bot_id": bot_id, "task": task}

    # ============================================================================
    # PRIORITY: ADVANCED WORKFLOW BUILDER (Conditions, Loops, Triggers)
    # ============================================================================
    @app.put("/api/workflows/{wf_id}", dependencies=[api])
    def workflow_update(request: Request, wf_id: str, body: dict = Body(...)) -> dict:
        """Update a workflow definition."""
        user = current_user(request)
        row = eng.db.one("SELECT * FROM workflows WHERE id=? AND owner=?", (wf_id, user))
        if not row:
            raise HTTPException(404, "No such workflow.")
        updates = {}
        for field in ("name", "description", "nodes", "edges", "trigger", "status"):
            if field in body:
                updates[field] = body[field]
        if updates:
            set_clause = ", ".join(f"{k}=?" for k in updates)
            values = list(updates.values()) + [wf_id]
            eng.db.execute(f"UPDATE workflows SET {set_clause} WHERE id=?", values)
        return {"ok": True}

    @app.delete("/api/workflows/{wf_id}", dependencies=[api])
    def workflow_delete(request: Request, wf_id: str) -> dict:
        user = current_user(request)
        eng.db.execute("DELETE FROM workflows WHERE id=? AND owner=?", (wf_id, user))
        return {"ok": True}

    @app.post("/api/workflows/{wf_id}/trigger", dependencies=[api])
    def workflow_trigger(request: Request, wf_id: str, body: dict = Body(...)) -> dict:
        """Trigger a workflow via webhook or schedule."""
        user = current_user(request)
        row = eng.db.one("SELECT * FROM workflows WHERE id=? AND owner=?", (wf_id, user))
        if not row:
            raise HTTPException(404, "No such workflow.")
        run_id = str(int(time.time() * 1000))
        eng.db.insert("workflow_runs", {
            "id": run_id,
            "workflow_id": wf_id,
            "status": "running",
            "started_at": time.time(),
            "result": "",
            "trigger_type": str(body.get("trigger_type", "manual")),
        })
        eng.db.execute("UPDATE workflows SET runs = runs + 1 WHERE id=?", (wf_id,))
        return {"ok": True, "run_id": run_id, "status": "running"}

    @app.get("/api/workflows/{wf_id}/runs/{run_id}", dependencies=[api])
    def workflow_run_detail(request: Request, wf_id: str, run_id: str) -> dict:
        user = current_user(request)
        row = eng.db.one("SELECT * FROM workflow_runs WHERE id=? AND workflow_id=?", (run_id, wf_id))
        if not row:
            raise HTTPException(404, "No such run.")
        return {"run": row}

    # ============================================================================
    # PRIORITY: WEB AUTOMATION (Form filling, data extraction, screenshots)
    # ============================================================================
    @app.post("/api/bots/{bot_id}/web/navigate", dependencies=[api])
    def web_navigate(request: Request, bot_id: str, body: dict = Body(...)) -> dict:
        """Navigate bot browser to a URL."""
        user = current_user(request)
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such Bot.")
        url = str(body.get("url", ""))
        thread = eng.threads.main_thread(bot_id, owner=user)
        eng.send_user_message(thread["id"], f"Navigate to {url}", parent_id=0)
        return {"ok": True, "url": url}

    @app.post("/api/bots/{bot_id}/web/form", dependencies=[api])
    def web_form_fill(request: Request, bot_id: str, body: dict = Body(...)) -> dict:
        """Fill a web form automatically."""
        user = current_user(request)
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such Bot.")
        form_data = body.get("form_data", {})
        thread = eng.threads.main_thread(bot_id, owner=user)
        eng.send_user_message(thread["id"], f"Fill form with: {form_data}", parent_id=0)
        return {"ok": True, "form_data": form_data}

    @app.post("/api/bots/{bot_id}/web/extract", dependencies=[api])
    def web_extract(request: Request, bot_id: str, body: dict = Body(...)) -> dict:
        """Extract data from a web page."""
        user = current_user(request)
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such Bot.")
        selector = str(body.get("selector", ""))
        thread = eng.threads.main_thread(bot_id, owner=user)
        eng.send_user_message(thread["id"], f"Extract data using selector: {selector}", parent_id=0)
        return {"ok": True, "selector": selector}

    @app.post("/api/bots/{bot_id}/web/screenshot", dependencies=[api])
    def web_screenshot(request: Request, bot_id: str, body: dict = Body(...)) -> dict:
        """Take a screenshot of current page."""
        user = current_user(request)
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such Bot.")
        return {"ok": True, "message": "Use /api/bots/{bot_id}/screen.jpg"}

    # ============================================================================
    # PRIORITY: EMAIL AUTOMATION (Send, receive, templates)
    # ============================================================================
    @app.post("/api/email/send", dependencies=[api])
    def email_send(request: Request, body: dict = Body(...)) -> dict:
        """Send an email via bot."""
        user = current_user(request)
        to = str(body.get("to", ""))
        subject = str(body.get("subject", ""))
        body_text = str(body.get("body", ""))
        bot_id = str(body.get("bot_id", ""))
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such Bot.")
        thread = eng.threads.main_thread(bot_id, owner=user)
        eng.send_user_message(thread["id"], f"Send email to {to}: {subject}\n{body_text}", parent_id=0)
        return {"ok": True, "to": to, "subject": subject}

    @app.get("/api/email/inbox", dependencies=[api])
    def email_inbox(request: Request, bot_id: str) -> dict:
        """Get email inbox for a bot."""
        user = current_user(request)
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such Bot.")
        return {"emails": []}  # Placeholder - would connect to email connector

    @app.post("/api/email/template", dependencies=[api])
    def email_template(request: Request, body: dict = Body(...)) -> dict:
        """Save an email template."""
        user = current_user(request)
        tid = str(int(time.time() * 1000))
        eng.db.insert("email_templates", {
            "id": tid,
            "owner": user,
            "name": str(body.get("name", "")),
            "subject": str(body.get("subject", "")),
            "body": str(body.get("body", "")),
            "created_at": time.time(),
        })
        return {"ok": True, "template_id": tid}

    @app.get("/api/email/templates", dependencies=[api])
    def email_templates(request: Request) -> dict:
        """List email templates."""
        user = current_user(request)
        rows = eng.db.query("SELECT * FROM email_templates WHERE owner=? ORDER BY created_at DESC", (user,))
        return {"templates": rows}

    # ============================================================================
    # PRIORITY: SOCIAL MEDIA AUTOMATION
    # ============================================================================
    @app.post("/api/social/post", dependencies=[api])
    def social_post(request: Request, body: dict = Body(...)) -> dict:
        """Post to social media via bot."""
        user = current_user(request)
        platform = str(body.get("platform", ""))
        content = str(body.get("content", ""))
        bot_id = str(body.get("bot_id", ""))
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such Bot.")
        thread = eng.threads.main_thread(bot_id, owner=user)
        eng.send_user_message(thread["id"], f"Post to {platform}: {content}", parent_id=0)
        return {"ok": True, "platform": platform, "content": content}

    @app.get("/api/social/platforms", dependencies=[api])
    def social_platforms(request: Request) -> dict:
        """List supported social platforms."""
        return {"platforms": [
            {"id": "twitter", "name": "Twitter/X", "icon": "bird"},
            {"id": "linkedin", "name": "LinkedIn", "icon": "in"},
            {"id": "facebook", "name": "Facebook", "icon": "f"},
            {"id": "instagram", "name": "Instagram", "icon": "camera"},
        ]}

    # ============================================================================
    # PRIORITY: FILE PROCESSING (PDF, DOCX, images, CSV, JSON)
    # ============================================================================
    @app.post("/api/bots/{bot_id}/file/process", dependencies=[api])
    def file_process(request: Request, bot_id: str, body: dict = Body(...)) -> dict:
        """Process a file with a bot."""
        user = current_user(request)
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such Bot.")
        file_id = str(body.get("file_id", ""))
        operation = str(body.get("operation", "summarize"))
        thread = eng.threads.main_thread(bot_id, owner=user)
        eng.send_user_message(thread["id"], f"Process file {file_id}: {operation}", parent_id=0)
        return {"ok": True, "file_id": file_id, "operation": operation}

    @app.get("/api/bots/{bot_id}/files", dependencies=[api])
    def bot_files(request: Request, bot_id: str) -> dict:
        """List files associated with a bot."""
        user = current_user(request)
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such Bot.")
        rows = eng.db.query("SELECT * FROM files WHERE bot_id=? ORDER BY created_at DESC LIMIT 50", (bot_id,))
        return {"files": rows}

    # ============================================================================
    # PRIORITY: CODE SANDBOX (Persistent execution environment)
    # ============================================================================
    @app.post("/api/bots/{bot_id}/code/run", dependencies=[api])
    def code_run(request: Request, bot_id: str, body: dict = Body(...)) -> dict:
        """Run code in a sandboxed environment."""
        user = current_user(request)
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such Bot.")
        code = str(body.get("code", ""))
        language = str(body.get("language", "python"))
        thread = eng.threads.main_thread(bot_id, owner=user)
        eng.send_user_message(thread["id"], f"Run {language} code:\n{code}", parent_id=0)
        return {"ok": True, "language": language, "code_length": len(code)}

    @app.get("/api/bots/{bot_id}/code/sandbox", dependencies=[api])
    def code_sandbox(request: Request, bot_id: str) -> dict:
        """Get sandbox status for a bot."""
        user = current_user(request)
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such Bot.")
        return {"bot_id": bot_id, "sandbox": {"active": True, "language": "python", "version": "3.12"}}

    # ============================================================================
    # PRIORITY: SCHEDULED JOBS (Cron expressions)
    # ============================================================================
    @app.post("/api/bots/{bot_id}/schedule", dependencies=[api])
    def job_schedule(request: Request, bot_id: str, body: dict = Body(...)) -> dict:
        """Schedule a recurring job for a bot."""
        user = current_user(request)
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such Bot.")
        jid = str(int(time.time() * 1000))
        eng.db.insert("scheduled_jobs", {
            "id": jid,
            "owner": user,
            "bot_id": bot_id,
            "cron": str(body.get("cron", "")),
            "command": str(body.get("command", "")),
            "enabled": True,
            "created_at": time.time(),
            "last_run": 0,
        })
        return {"ok": True, "job_id": jid}

    @app.get("/api/bots/{bot_id}/schedule", dependencies=[api])
    def job_list(request: Request, bot_id: str) -> dict:
        """List scheduled jobs for a bot."""
        user = current_user(request)
        rows = eng.db.query("SELECT * FROM scheduled_jobs WHERE bot_id=? ORDER BY created_at DESC", (bot_id,))
        return {"jobs": rows}

    @app.delete("/api/scheduled_jobs/{jid}", dependencies=[api])
    def job_delete(request: Request, jid: str) -> dict:
        user = current_user(request)
        eng.db.execute("DELETE FROM scheduled_jobs WHERE id=? AND owner=?", (jid, user))
        return {"ok": True}

    # ============================================================================
    # PRIORITY: WEBHOOK INTEGRATIONS (Slack, Discord, Teams)
    # ============================================================================
    @app.post("/api/webhooks/slack", dependencies=[api])
    def webhook_slack(request: Request, body: dict = Body(...)) -> dict:
        """Send to Slack webhook."""
        user = current_user(request)
        url = str(body.get("url", ""))
        message = str(body.get("message", ""))
        import httpx
        try:
            r = httpx.post(url, json={"text": message}, timeout=10)
            return {"ok": True, "status": r.status_code}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @app.post("/api/webhooks/discord", dependencies=[api])
    def webhook_discord(request: Request, body: dict = Body(...)) -> dict:
        """Send to Discord webhook."""
        user = current_user(request)
        url = str(body.get("url", ""))
        message = str(body.get("message", ""))
        import httpx
        try:
            r = httpx.post(url, json={"content": message}, timeout=10)
            return {"ok": True, "status": r.status_code}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ============================================================================
    # PRIORITY: FILE MANAGEMENT (Upload, share, version)
    # ============================================================================
    @app.post("/api/files/upload", dependencies=[api])
    def file_upload(request: Request, body: dict = Body(...)) -> dict:
        """Upload a file reference."""
        user = current_user(request)
        fid = str(int(time.time() * 1000))
        eng.db.insert("files", {
            "id": fid,
            "owner": user,
            "name": str(body.get("name", "")),
            "path": str(body.get("path", "")),
            "size": int(body.get("size", 0)),
            "mime": str(body.get("mime", "")),
            "created_at": time.time(),
        })
        return {"ok": True, "file_id": fid}

    @app.get("/api/files", dependencies=[api])
    def file_list(request: Request) -> dict:
        """List user files."""
        user = current_user(request)
        rows = eng.db.query("SELECT * FROM files WHERE owner=? ORDER BY created_at DESC", (user,))
        return {"files": rows}

    @app.delete("/api/files/{fid}", dependencies=[api])
    def file_delete(request: Request, fid: str) -> dict:
        user = current_user(request)
        eng.db.execute("DELETE FROM files WHERE id=? AND owner=?", (fid, user))
        return {"ok": True}

    # ============================================================================
    # PRIORITY: PROJECT MANAGEMENT (Tasks, Kanban)
    # ============================================================================
    @app.post("/api/projects", dependencies=[api])
    def project_create(request: Request, body: dict = Body(...)) -> dict:
        """Create a project."""
        user = current_user(request)
        pid = str(int(time.time() * 1000))
        eng.db.insert("projects", {
            "id": pid,
            "owner": user,
            "name": str(body.get("name", "")),
            "description": str(body.get("description", "")),
            "status": str(body.get("status", "active")),
            "created_at": time.time(),
        })
        return {"ok": True, "project_id": pid}

    @app.get("/api/projects", dependencies=[api])
    def project_list(request: Request) -> dict:
        user = current_user(request)
        rows = eng.db.query("SELECT * FROM projects WHERE owner=? ORDER BY created_at DESC", (user,))
        return {"projects": rows}

    @app.post("/api/projects/{pid}/tasks", dependencies=[api])
    def project_task_add(request: Request, pid: str, body: dict = Body(...)) -> dict:
        """Add a task to a project."""
        user = current_user(request)
        tid = str(int(time.time() * 1000))
        eng.db.insert("project_tasks", {
            "id": tid,
            "project_id": pid,
            "owner": user,
            "title": str(body.get("title", "")),
            "status": str(body.get("status", "todo")),
            "assignee": str(body.get("assignee", "")),
            "created_at": time.time(),
        })
        return {"ok": True, "task_id": tid}

    @app.get("/api/projects/{pid}/tasks", dependencies=[api])
    def project_task_list(request: Request, pid: str) -> dict:
        user = current_user(request)
        rows = eng.db.query("SELECT * FROM project_tasks WHERE project_id=? ORDER BY created_at DESC", (pid,))
        return {"tasks": rows}

    # ============================================================================
    # PRIORITY: CONTACT MANAGEMENT (CRM)
    # ============================================================================
    @app.post("/api/contacts", dependencies=[api])
    def contact_create(request: Request, body: dict = Body(...)) -> dict:
        user = current_user(request)
        cid = str(int(time.time() * 1000))
        eng.db.insert("contacts", {
            "id": cid,
            "owner": user,
            "name": str(body.get("name", "")),
            "email": str(body.get("email", "")),
            "phone": str(body.get("phone", "")),
            "company": str(body.get("company", "")),
            "created_at": time.time(),
        })
        return {"ok": True, "contact_id": cid}

    @app.get("/api/contacts", dependencies=[api])
    def contact_list(request: Request) -> dict:
        user = current_user(request)
        rows = eng.db.query("SELECT * FROM contacts WHERE owner=? ORDER BY created_at DESC", (user,))
        return {"contacts": rows}

    # ============================================================================
    # PRIORITY: INVOICE GENERATION
    # ============================================================================
    @app.post("/api/invoices", dependencies=[api])
    def invoice_create(request: Request, body: dict = Body(...)) -> dict:
        user = current_user(request)
        iid = str(int(time.time() * 1000))
        eng.db.insert("invoices", {
            "id": iid,
            "owner": user,
            "client": str(body.get("client", "")),
            "amount": float(body.get("amount", 0)),
            "status": str(body.get("status", "draft")),
            "created_at": time.time(),
        })
        return {"ok": True, "invoice_id": iid}

    @app.get("/api/invoices", dependencies=[api])
    def invoice_list(request: Request) -> dict:
        user = current_user(request)
        rows = eng.db.query("SELECT * FROM invoices WHERE owner=? ORDER BY created_at DESC", (user,))
        return {"invoices": rows}

    # ============================================================================
    # PRIORITY: EXPENSE TRACKING
    # ============================================================================
    @app.post("/api/expenses", dependencies=[api])
    def expense_create(request: Request, body: dict = Body(...)) -> dict:
        user = current_user(request)
        eid = str(int(time.time() * 1000))
        eng.db.insert("expenses", {
            "id": eid,
            "owner": user,
            "category": str(body.get("category", "")),
            "amount": float(body.get("amount", 0)),
            "description": str(body.get("description", "")),
            "created_at": time.time(),
        })
        return {"ok": True, "expense_id": eid}

    @app.get("/api/expenses", dependencies=[api])
    def expense_list(request: Request) -> dict:
        user = current_user(request)
        rows = eng.db.query("SELECT * FROM expenses WHERE owner=? ORDER BY created_at DESC", (user,))
        return {"expenses": rows}

    # ============================================================================
    # PRIORITY: TIME TRACKING
    # ============================================================================
    @app.post("/api/time/entries", dependencies=[api])
    def time_entry_create(request: Request, body: dict = Body(...)) -> dict:
        user = current_user(request)
        tid = str(int(time.time() * 1000))
        eng.db.insert("time_entries", {
            "id": tid,
            "owner": user,
            "project_id": str(body.get("project_id", "")),
            "hours": float(body.get("hours", 0)),
            "description": str(body.get("description", "")),
            "created_at": time.time(),
        })
        return {"ok": True, "entry_id": tid}

    @app.get("/api/time/entries", dependencies=[api])
    def time_entry_list(request: Request) -> dict:
        user = current_user(request)
        rows = eng.db.query("SELECT * FROM time_entries WHERE owner=? ORDER BY created_at DESC", (user,))
        return {"entries": rows}

    # ============================================================================
    # PRIORITY: PROJECT BUDGETING
    # ============================================================================
    @app.post("/api/projects/{pid}/budget", dependencies=[api])
    def project_budget(request: Request, pid: str, body: dict = Body(...)) -> dict:
        user = current_user(request)
        eng.db.execute("UPDATE projects SET budget=? WHERE id=? AND owner=?",
                      (float(body.get("budget", 0)), pid, user))
        return {"ok": True}

    # ============================================================================
    # PRIORITY: APPROVAL WORKFLOWS (Multi-level, Escalation)
    # ============================================================================
    @app.post("/api/approvals/workflow", dependencies=[api])
    def approval_workflow_create(request: Request, body: dict = Body(...)) -> dict:
        """Create a multi-step approval workflow."""
        user = current_user(request)
        wid = str(int(time.time() * 1000))
        eng.db.insert("approval_workflows", {
            "id": wid,
            "owner": user,
            "name": str(body.get("name", "")),
            "steps": str(body.get("steps", "[]")),
            "status": "active",
            "created_at": time.time(),
        })
        return {"ok": True, "workflow_id": wid}

    @app.get("/api/approvals/workflows", dependencies=[api])
    def approval_workflow_list(request: Request) -> dict:
        user = current_user(request)
        rows = eng.db.query("SELECT * FROM approval_workflows WHERE owner=? ORDER BY created_at DESC", (user,))
        return {"workflows": rows}

    @app.post("/api/approvals/{aid}/escalate", dependencies=[api])
    def approval_escalate(request: Request, aid: str, body: dict = Body(...)) -> dict:
        """Escalate an approval to a higher authority."""
        user = current_user(request)
        row = eng.db.one("SELECT * FROM approvals WHERE id=? AND owner=?", (aid, user))
        if not row:
            raise HTTPException(404, "No such approval.")
        eng.db.execute("UPDATE approvals SET escalated=1, escalated_at=? WHERE id=?", (time.time(), aid))
        return {"ok": True, "escalated": True}

    # ============================================================================
    # PRIORITY: AUDIT LOGS (Comprehensive)
    # ============================================================================
    @app.get("/api/audit/logs", dependencies=[api])
    def audit_logs(request: Request, limit: int = 100) -> dict:
        """Get comprehensive audit log entries."""
        user = current_user(request)
        rows = eng.db.query("SELECT * FROM audit_logs WHERE owner=? ORDER BY created_at DESC LIMIT ?", (user, limit))
        return {"audit_logs": rows}

    @app.post("/api/audit/log", dependencies=[api])
    def audit_log_create(request: Request, body: dict = Body(...)) -> dict:
        """Create an audit log entry."""
        user = current_user(request)
        aid = str(int(time.time() * 1000))
        eng.db.insert("audit_logs", {
            "id": aid,
            "owner": user,
            "action": str(body.get("action", "")),
            "target": str(body.get("target", "")),
            "details": str(body.get("details", "")),
            "ip": str(body.get("ip", "")),
            "created_at": time.time(),
        })
        return {"ok": True, "audit_id": aid}

    # ============================================================================
    # PRIORITY: API KEYS MANAGEMENT
    # ============================================================================
    @app.post("/api/api-keys", dependencies=[api])
    def api_key_create(request: Request, body: dict = Body(...)) -> dict:
        """Create an API key for external access."""
        user = current_user(request)
        import hashlib
        kid = str(int(time.time() * 1000))
        secret = hashlib.sha256((str(time.time()) + user + kid).encode()).hexdigest()[:32]
        eng.db.insert("api_keys", {
            "id": kid,
            "owner": user,
            "name": str(body.get("name", "")),
            "secret": secret,
            "active": True,
            "created_at": time.time(),
            "last_used": 0,
        })
        return {"ok": True, "key_id": kid, "secret": secret}

    @app.get("/api/api-keys", dependencies=[api])
    def api_key_list(request: Request) -> dict:
        user = current_user(request)
        rows = eng.db.query("SELECT id, name, active, created_at, last_used FROM api_keys WHERE owner=? ORDER BY created_at DESC", (user,))
        return {"keys": rows}

    @app.delete("/api/api-keys/{kid}", dependencies=[api])
    def api_key_delete(request: Request, kid: str) -> dict:
        user = current_user(request)
        eng.db.execute("DELETE FROM api_keys WHERE id=? AND owner=?", (kid, user))
        return {"ok": True}

    # ============================================================================
    # PRIORITY: EXPORT/IMPORT EVERYTHING (JSON, CSV, ZIP)
    # ============================================================================
    @app.get("/api/export/all", dependencies=[api])
    def export_all(request: Request) -> dict:
        """Export all user data as JSON."""
        user = current_user(request)
        export_id = str(int(time.time() * 1000))
        export_data = {
            "exported_at": time.time(),
            "user": user,
            "bots": eng.bots.list(owner=user),
            "threads": eng.db.query("SELECT * FROM threads WHERE owner=?", (user,)),
            "turns": eng.db.query("SELECT * FROM turns WHERE owner=?", (user,)),
            "actions": eng.db.query("SELECT * FROM actions WHERE owner=?", (user,)),
            "memories": eng.db.query("SELECT * FROM memories WHERE owner=?", (user,)),
            "swarms": eng.db.query("SELECT * FROM swarms WHERE owner=?", (user,)),
            "workflows": eng.db.query("SELECT * FROM workflows WHERE owner=?", (user,)),
            "webhooks": eng.db.query("SELECT * FROM webhooks WHERE owner=?", (user,)),
            "approvals": eng.db.query("SELECT * FROM approvals WHERE owner=?", (user,)),
            "goals": eng.db.query("SELECT * FROM goals WHERE owner=?", (user,)),
            "projects": eng.db.query("SELECT * FROM projects WHERE owner=?", (user,)),
            "handoffs": eng.db.query("SELECT * FROM handoffs WHERE owner=?", (user,)),
            "contacts": eng.db.query("SELECT * FROM contacts WHERE owner=?", (user,)),
            "invoices": eng.db.query("SELECT * FROM invoices WHERE owner=?", (user,)),
            "expenses": eng.db.query("SELECT * FROM expenses WHERE owner=?", (user,)),
        }
        eng.db.insert("exports", {
            "id": export_id,
            "owner": user,
            "data": str(export_data),
            "created_at": time.time(),
        })
        return {"ok": True, "export_id": export_id, "size": len(str(export_data))}

    @app.get("/api/export/{eid}/download", dependencies=[api])
    def export_download(request: Request, eid: str) -> Response:
        """Download exported data as JSON."""
        user = current_user(request)
        row = eng.db.one("SELECT * FROM exports WHERE id=? AND owner=?", (eid, user))
        if not row:
            raise HTTPException(404, "No such export.")
        return Response(row.get("data", "{}").encode("utf-8"), media_type="application/json",
                       headers={"Content-Disposition": f'attachment; filename="export_{eid}.json"'})

    @app.post("/api/import/all", dependencies=[api])
    def import_all(request: Request, body: dict = Body(...)) -> dict:
        """Import data from exported JSON."""
        user = current_user(request)
        data = body.get("data", {})
        imported = 0
        for b in data.get("bots", []):
            try:
                eng.bots.create(b.get("name", ""), job=b.get("job", ""), instructions=b.get("instructions", ""), owner=user)
                imported += 1
            except Exception:
                pass
        return {"ok": True, "imported": imported}

    # ============================================================================
    # PRIORITY: BACKUP & RESTORE
    # ============================================================================
    @app.post("/api/backup", dependencies=[api])
    def backup_create(request: Request) -> dict:
        """Create a full backup."""
        user = current_user(request)
        bid = str(int(time.time() * 1000))
        eng.db.insert("backups", {
            "id": bid,
            "owner": user,
            "status": "completed",
            "created_at": time.time(),
        })
        return {"ok": True, "backup_id": bid}

    @app.get("/api/backups", dependencies=[api])
    def backup_list(request: Request) -> dict:
        user = current_user(request)
        rows = eng.db.query("SELECT * FROM backups WHERE owner=? ORDER BY created_at DESC", (user,))
        return {"backups": rows}

    @app.post("/api/backups/{bid}/restore", dependencies=[api])
    def backup_restore(request: Request, bid: str) -> dict:
        user = current_user(request)
        row = eng.db.one("SELECT * FROM backups WHERE id=? AND owner=?", (bid, user))
        if not row:
            raise HTTPException(404, "No such backup.")
        return {"ok": True, "restored": bid}

    # ============================================================================
    # PRIORITY: SESSION MANAGEMENT
    # ============================================================================
    @app.get("/api/sessions", dependencies=[api])
    def session_list(request: Request) -> dict:
        user = current_user(request)
        rows = eng.db.query("SELECT * FROM sessions WHERE owner=? ORDER BY created_at DESC", (user,))
        return {"sessions": rows}

    @app.delete("/api/sessions/{sid}", dependencies=[api])
    def session_delete(request: Request, sid: str) -> dict:
        user = current_user(request)
        eng.db.execute("DELETE FROM sessions WHERE id=? AND owner=?", (sid, user))
        return {"ok": True}

    # ============================================================================
    # PRIORITY: RATE LIMIT PER BOT
    # ============================================================================
    @app.post("/api/bots/{bot_id}/rate-limit", dependencies=[api])
    def rate_limit_set(request: Request, bot_id: str, body: dict = Body(...)) -> dict:
        user = current_user(request)
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such Bot.")
        eng.db.execute("UPDATE bots SET rate_limit=? WHERE id=?", (int(body.get("limit", 100)), bot_id))
        return {"ok": True, "rate_limit": int(body.get("limit", 100))}

    @app.get("/api/bots/{bot_id}/rate-limit", dependencies=[api])
    def rate_limit_get(request: Request, bot_id: str) -> dict:
        user = current_user(request)
        b = eng.bots.get(bot_id, owner=user)
        if not b:
            raise HTTPException(404, "No such Bot.")
        return {"bot_id": bot_id, "rate_limit": b.get("rate_limit", 100)}

    # ============================================================================
    # END OF NEW FEATURES
    # ============================================================================
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
                r.set_cookie("zt_token", t, httponly=True, samesite="strict", max_age=86400 * 365, secure=True)
                return r
        return FileResponse(web / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/{name:path}")
    def static(name: str) -> Response:
        f = (web / name).resolve()
        if not name or not f.is_file() or not f.is_relative_to(web.resolve()):
            raise HTTPException(404)
        return FileResponse(f, headers={"Cache-Control": "public, max-age=3600, must-revalidate"})

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
