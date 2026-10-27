"""Secret storage in the OS credential store (Windows Credential Manager via keyring).

Secrets are never written to the database, settings or any plain-text file. On
hosts with no credential backend (e.g. a headless Docker host) secrets can only
be supplied through environment variables, which are read-only to the app.
"""
from __future__ import annotations

import json
import os
import re
import threading

SERVICE = "ZerotheBot"
OLD_SERVICE = "OpenGrokBot"   # pre-rename credential store; read as a fallback, never written to

try:  # keyring is a hard dependency, but fail soft so the app can still explain itself
    import keyring
    from keyring.backend import KeyringBackend as _KeyringBackend
    from keyring.errors import KeyringError, PasswordDeleteError
except Exception:  # pragma: no cover
    keyring = None  # type: ignore

    class _KeyringBackend:  # type: ignore
        priority = 1.0

    class KeyringError(Exception):  # type: ignore
        pass

    class PasswordDeleteError(Exception):  # type: ignore
        pass


class SecretStoreError(RuntimeError):
    pass


VAULT_FILE = "secrets.vault"       # encrypted JSON blob (Fernet) in the data dir
VAULT_KEY_FILE = "secrets.key"     # vault key, chmod 600; override with ZEROTHEBOT_VAULT_KEY


class _VaultBackend(_KeyringBackend):
    """Encrypted-at-rest credential file for hosts with no OS credential store.

    Headless servers (Linux VMs, containers) ship keyring without a usable
    backend, which used to make every plugin Connect fail: the refresh token
    could never be written. This backend keeps secrets in one Fernet-encrypted
    blob next to the database, so Connect works out of the box. The desktop app
    still uses the OS credential store whenever one is available.

    The key lives in the same data dir (mode 600) unless ZEROTHEBOT_VAULT_KEY
    supplies one, which is what you want when the data dir is on shared storage.
    """

    priority = 1.0

    def __init__(self) -> None:
        self._lock = threading.RLock()

    # -- storage plumbing ---------------------------------------------------
    @staticmethod
    def _dir():
        from . import paths
        return paths.data_dir()

    def _fernet(self):
        from cryptography.fernet import Fernet
        env = os.environ.get("ZEROTHEBOT_VAULT_KEY") or os.environ.get("OPENGROKBOT_VAULT_KEY")
        if env:
            try:
                return Fernet(env.strip().encode())
            except Exception as e:  # noqa: BLE001
                raise SecretStoreError(
                    "ZEROTHEBOT_VAULT_KEY is not a valid key (it must be a 32-byte urlsafe-base64 value)."
                ) from e
        kp = self._dir() / VAULT_KEY_FILE
        if kp.is_file():
            key = kp.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            tmp = kp.with_name(kp.name + ".tmp")
            tmp.write_bytes(key)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, kp)
        return Fernet(key)

    def _read(self, f) -> dict:
        p = self._dir() / VAULT_FILE
        if not p.is_file():
            return {}
        try:
            data = json.loads(f.decrypt(p.read_bytes()).decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            raise SecretStoreError(
                f"The local credential vault ({p.name}) could not be opened. If you set "
                "ZEROTHEBOT_VAULT_KEY, make sure it is the same key that encrypted it."
            ) from e
        return data if isinstance(data, dict) else {}

    def _write(self, f, data: dict) -> None:
        p = self._dir() / VAULT_FILE
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(f.encrypt(json.dumps(data).encode("utf-8")))
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, p)

    # -- keyring-compatible API ---------------------------------------------
    def get_password(self, service: str, name: str) -> str | None:
        with self._lock:
            try:
                f = self._fernet()
            except Exception:  # noqa: BLE001  (unreadable vault behaves like "no secret")
                return None
            return self._read(f).get(f"{service}|{name}") or None

    def set_password(self, service: str, name: str, value: str) -> None:
        with self._lock:
            f = self._fernet()
            data = self._read(f)
            data[f"{service}|{name}"] = value
            self._write(f, data)

    def delete_password(self, service: str, name: str) -> None:
        with self._lock:
            try:
                f = self._fernet()
            except Exception:  # noqa: BLE001
                return
            data = self._read(f)
            if data.pop(f"{service}|{name}", None) is not None:
                self._write(f, data)


_VAULT: _VaultBackend | None = None


def vault_backend() -> _VaultBackend | None:
    """The encrypted-file backend in use, or None when the OS store is available."""
    return _VAULT


def install_fallback_backend() -> bool:
    """Use the encrypted file vault when the OS credential store is unusable.

    Returns True when some writable backend is available afterwards.
    """
    global _VAULT
    if backend_available():
        return True
    if keyring is None:
        return False
    try:
        _VAULT = _VaultBackend()
        keyring.set_keyring(_VAULT)
    except Exception:  # noqa: BLE001
        _VAULT = None
        return False
    # Belt and braces: some keyring builds resolve the backend lazily, so make
    # sure the module-level handle points at the vault as well.
    try:
        import keyring.core as _kc
        _kc._keyring_backend = _VAULT
    except Exception:  # noqa: BLE001
        pass
    return backend_available()


def _env_name(name: str) -> str:
    return "ZEROTHEBOT_SECRET_" + re.sub(r"[^A-Za-z0-9]+", "_", name).upper().strip("_")


def _old_env_name(name: str) -> str:
    return "OPENGROKBOT_SECRET_" + re.sub(r"[^A-Za-z0-9]+", "_", name).upper().strip("_")


_ENV_ALIASES = {
    "provider:anthropic": "ANTHROPIC_API_KEY",
    "provider:openai": "OPENAI_API_KEY",
    "provider:openrouter": "OPENROUTER_API_KEY",
    "provider:groq": "GROQ_API_KEY",
}


def backend_available() -> bool:
    if keyring is None:
        return False
    try:
        kr = keyring.get_keyring()
        return "fail" not in type(kr).__module__.lower() and "null" not in type(kr).__module__.lower()
    except Exception:
        return False


def _read_keyring(service: str, name: str) -> str | None:
    if keyring is None:
        return None
    try:
        return keyring.get_password(service, name) or None
    except Exception:
        return None


def get_secret(name: str) -> str | None:
    v = _read_keyring(SERVICE, name)
    if v:
        return v
    old = _read_keyring(OLD_SERVICE, name)
    if old:   # pre-rename key: carry it forward into the new store (best effort)
        try:
            keyring.set_password(SERVICE, name, old)
        except Exception:
            pass
        return old
    for env in (_env_name(name), _old_env_name(name), _ENV_ALIASES.get(name, "")):
        if env and os.environ.get(env):
            return os.environ[env]
    return None


def has_secret(name: str) -> bool:
    return bool(get_secret(name))


def set_secret(name: str, value: str) -> None:
    if keyring is None or not backend_available():
        raise SecretStoreError(
            "No credential store is available on this machine. Set the environment variable "
            f"{_env_name(name)} instead (secrets are never stored in plain text)."
        )
    try:
        keyring.set_password(SERVICE, name, value)
    except SecretStoreError:
        raise
    except Exception as e:
        raise SecretStoreError(f"Could not save secret to the credential store: {e}") from e


def delete_secret(name: str) -> None:
    if keyring is None:
        return
    for service in (SERVICE, OLD_SERVICE):
        try:
            keyring.delete_password(service, name)
        except (PasswordDeleteError, KeyringError):
            pass
        except Exception:
            pass


def redact(text: str, extra: list[str] | None = None) -> str:
    """Best-effort scrub of obvious secret shapes from text headed to logs/exports."""
    if not text:
        return text
    patterns = [
        r"sk-ant-[A-Za-z0-9_\-]{20,}",
        r"sk-[A-Za-z0-9_\-]{20,}",
        r"xox[abprs]-[A-Za-z0-9\-]{10,}",
        r"gh[pousr]_[A-Za-z0-9]{30,}",
        r"github_pat_[A-Za-z0-9_]{30,}",
        r"AIza[0-9A-Za-z_\-]{30,}",
        r"lin_api_[A-Za-z0-9]{30,}",
        r"secret_[A-Za-z0-9]{30,}",
        r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}",
    ]
    out = text
    for p in patterns:
        out = re.sub(p, "[REDACTED]", out)
    for s in extra or []:
        if s and len(s) > 6:
            out = out.replace(s, "[REDACTED]")
    return out


# Headless hosts (Linux servers/containers) ship keyring with a "fail" backend.
# Install the encrypted file vault so plugins can actually store credentials.
install_fallback_backend()
