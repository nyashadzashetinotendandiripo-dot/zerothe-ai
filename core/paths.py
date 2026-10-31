"""Filesystem locations used by ZerotheBot.

Everything the account owns lives under one data directory. The shared computer
(workspace, browser profile, downloads) belongs to the account, not to any Bot.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


APP_DIR = "ZerotheBot"
OLD_APP_DIR = "OpenGrokBot"   # pre-rename folder, moved into place once on first run
DB_FILE = "zerothebot.sqlite3"


def resource_root() -> Path:
    """Directory holding bundled resources (web/, skills/, plugins/, assets/)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def _default_base() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / APP_DIR
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / APP_DIR


def _old_base() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / OLD_APP_DIR
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / OLD_APP_DIR


def _migrate() -> None:
    """First run after the rename: move the old data folder into place so Bots, memory and settings survive."""
    new, old = _default_base(), _old_base()
    if new == old or new.exists() or not old.exists():
        return
    try:
        old.rename(new)
    except OSError:
        pass   # old app still running or locked: the new folder stays fresh, old data is untouched


def data_dir() -> Path:
    override = os.environ.get("ZEROTHEBOT_HOME") or os.environ.get("OPENGROKBOT_HOME")
    if override:
        base = Path(override)
    else:
        _migrate()
        base = _default_base()
    base.mkdir(parents=True, exist_ok=True)
    return base


def _sub(*parts: str) -> Path:
    p = data_dir().joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / DB_FILE


def workspace_dir() -> Path:
    """The shared filesystem workspace every Bot can see."""
    p = _sub("workspace")
    for name in ("shared", "downloads", "bots"):
        (p / name).mkdir(exist_ok=True)
    return p


def browser_profile_dir() -> Path:
    return _sub("computer", "browser-profile")


def screenshots_dir() -> Path:
    return _sub("computer", "screenshots")


def skills_dir() -> Path:
    return _sub("skills")


def plugins_dir() -> Path:
    return _sub("plugins")


def exports_dir() -> Path:
    return _sub("exports")


def logs_dir() -> Path:
    return _sub("logs")


def service_info_path() -> Path:
    return data_dir() / "service.json"


def ui_config_path() -> Path:
    return data_dir() / "ui_config.json"


def admin_policy_paths() -> list[Path]:
    """Places an administrator may drop an admin.json (first existing wins)."""
    paths: list[Path] = []
    env = os.environ.get("ZEROTHEBOT_ADMIN") or os.environ.get("OPENGROKBOT_ADMIN")
    if env:
        paths.append(Path(env))
    pd = os.environ.get("PROGRAMDATA")
    if pd:
        paths.append(Path(pd) / APP_DIR / "admin.json")
        paths.append(Path(pd) / OLD_APP_DIR / "admin.json")
    paths.append(data_dir() / "admin.json")
    return paths
