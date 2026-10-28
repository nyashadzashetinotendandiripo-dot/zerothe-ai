"""Google Chrome (this PC): search local bookmarks and browsing history.

Reads the Chrome profile on this machine directly. The history database is copied
before reading, so a running Chrome is never disturbed. No network, no API key.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _user_data() -> Path | None:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "Google" / "Chrome" / "User Data"
        return None
    return Path.home() / ".config" / "google-chrome"


def _profile_dir(api) -> Path:
    root = _user_data()
    if root is None:
        raise RuntimeError("Could not locate the Chrome profile folder on this system.")
    name = (api.config("profile", "") or "").strip() or "Default"
    d = root / name
    if not d.is_dir():
        raise RuntimeError(f"Chrome profile '{name}' not found. Installed profiles: {', '.join(_profiles(api)) or 'none'}.")
    return d


def _profiles(api) -> list[str]:
    root = _user_data()
    if root is None or not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "Bookmarks").is_file())


def _walk_bookmarks(node, trail, out):
    for child in (node or {}).get("children", []) or []:
        if child.get("type") == "url" and child.get("url"):
            out.append({"name": child.get("name", ""), "url": child["url"], "folder": " / ".join(trail)})
        elif child.get("type") == "folder":
            _walk_bookmarks(child, trail + [child.get("name", "")], out)


def chrome_profiles(args, api):
    profiles = _profiles(api)
    if not profiles:
        return "Chrome does not seem to be installed (no profile folders with bookmarks found)."
    return {"profiles": profiles}


def chrome_bookmarks(args, api):
    query = (args.get("query") or "").strip().lower()
    try:
        profile = _profile_dir(api)
    except RuntimeError as e:
        return str(e)
    try:
        raw = json.loads((profile / "Bookmarks").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "This Chrome profile has no bookmarks file."
    except ValueError:
        return "The bookmarks file could not be read (is Chrome syncing it right now? try again)."
    found = []
    for section in (raw.get("roots") or {}).values():
        _walk_bookmarks(section, [], found)
    if query:
        found = [b for b in found if query in b["name"].lower() or query in b["url"].lower()]
    if not found:
        return "No bookmarks matched." if query else "No bookmarks in this profile."
    return {"bookmarks": found[:50], "total": len(found)}


_CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def _chrome_ts(micros: int) -> str:
    try:
        return (_CHROME_EPOCH + timedelta(microseconds=int(micros or 0))).astimezone().strftime("%Y-%m-%d %H:%M")
    except (ValueError, OverflowError):
        return ""


def chrome_history(args, api):
    query = (args.get("query") or "").strip().lower()
    limit = max(1, min(int(args.get("limit", 30) or 30), 200))
    try:
        src = _profile_dir(api) / "History"
    except RuntimeError as e:
        return str(e)
    if not src.is_file():
        return "This Chrome profile has no history file."
    tmp = Path(tempfile.mkdtemp(prefix="zt-chrome-")) / "History"
    try:
        shutil.copyfile(src, tmp)
        con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT url, title, visit_count, last_visit_time FROM urls ORDER BY last_visit_time DESC LIMIT ?", (limit * 4,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        return f"Could not read the history copy: {e}"
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)
    out = []
    for url, title, visits, when in rows:
        if query and query not in (url or "").lower() and query not in (title or "").lower():
            continue
        out.append({"title": title or "", "url": url or "", "visits": visits, "last_visit": _chrome_ts(when)})
        if len(out) >= limit:
            break
    if not out:
        return "No history entries matched." if query else "No history in this profile."
    return {"history": out}


def register(api):
    return [
        {"name": "profiles", "description": "List Chrome profiles on this PC (for the profile setting).",
         "input_schema": {"type": "object", "properties": {}}, "handler": lambda a: chrome_profiles(a, api)},
        {"name": "bookmarks", "description": "Search Chrome bookmarks by name or address. Empty query lists everything.",
         "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "Text to find"}}},
         "handler": lambda a: chrome_bookmarks(a, api)},
        {"name": "history", "description": "Search Chrome browsing history (most recent first). Empty query lists recent pages.",
         "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "Text to find"},
                                                           "limit": {"type": "integer", "description": "Max entries, default 30"}}},
         "handler": lambda a: chrome_history(a, api)},
    ]
