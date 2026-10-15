"""Configuration persistence with encrypted-at-rest API keys.

Keys are encrypted with Windows DPAPI (no dependencies) on Windows,
or Fernet via the optional `cryptography` package elsewhere. If neither
is available keys fall back to plaintext with a warning surfaced in the
Security settings page.
"""
import base64
import copy
import json
import os
import threading

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(APP_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
SECRET_KEY_PATH = os.path.join(DATA_DIR, "secret.key")

_KEY_STORAGE = "plain"  # resolved at import: dpapi | fernet | plain
_PLAIN_FALLBACK = False  # True once encrypt_secret had to fall back to plaintext
_LOCK = threading.Lock()

DEFAULT_CONFIG = {
    "version": "0.8.4",
    "general": {
        "port": 8765,
        "open_browser_on_start": True,
        "internet_enabled": False,
        "embedding_backend": "auto",       # auto | runtime | minilm | keyword
        "memory_k": 4,
        "chunk_chars": 600,
        "context_chars": 6000,
        "lan_access": False,               # bind 0.0.0.0 (UI + hosted agents)
        "bluetooth_enabled": False,
    },
    "runtimes": [
        {"id": "lmstudio", "type": "lmstudio", "name": "LM Studio",
         "base_url": "http://127.0.0.1:1234", "enabled": True},
    ],
    "cloud": {
        "openrouter": {"key": "", "enabled": False, "prefer_free": True,
                       "preferred_model": "", "allow_paid": False, "catalog": []},
        "anthropic": {"key": "", "enabled": False, "model": ""},
        "openai": {"key": "", "enabled": False, "model": ""},
    },
    "folders": [],
    "active": {"runtime_id": "", "model": "", "secondary": {"runtime_id": "", "model": ""}},
    "node_toggles": {},   # visual mapping toggles: {"memory": true, "<model|provider>": true}
    "log_level": "INFO",
}

# ---------------------------------------------------------------- encryption

def _dpapi_protect(text: str) -> str:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    raw = text.encode("utf-8")
    buf = ctypes.create_string_buffer(raw, len(raw))
    blob_in = BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in), "LordBlackHarness", None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("CryptProtectData failed")
    try:
        return base64.b64encode(ctypes.string_at(blob_out.pbData, blob_out.cbData)).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(token: str) -> str:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    raw = base64.b64decode(token)
    buf = ctypes.create_string_buffer(raw, len(raw))
    blob_in = BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _fernet_protect(text: str) -> str:
    from cryptography.fernet import Fernet
    key = open(SECRET_KEY_PATH, "rb").read()
    return Fernet(key).encrypt(text.encode("utf-8")).decode("ascii")


def _fernet_unprotect(token: str) -> str:
    from cryptography.fernet import Fernet
    key = open(SECRET_KEY_PATH, "rb").read()
    return Fernet(key).decrypt(token.encode("ascii")).decode("utf-8")


def _resolve_storage_mode():
    global _KEY_STORAGE
    if os.name == "nt":
        _KEY_STORAGE = "dpapi"
        return
    try:
        import cryptography  # noqa: F401
        _KEY_STORAGE = "fernet"
    except Exception:
        _KEY_STORAGE = "plain"


def encrypt_secret(text: str) -> str:
    global _PLAIN_FALLBACK
    if not text:
        return ""
    if _KEY_STORAGE == "dpapi":
        try:
            return "dpapi1:" + _dpapi_protect(text)
        except Exception:
            pass
    if _KEY_STORAGE == "fernet":
        try:
            return "fernet1:" + _fernet_protect(text)
        except Exception:
            pass
    _PLAIN_FALLBACK = True
    return "plain:" + text


def decrypt_secret(stored: str) -> str:
    if not stored:
        return ""
    if stored.startswith("dpapi1:"):
        return _dpapi_unprotect(stored[7:])
    if stored.startswith("fernet1:"):
        return _fernet_unprotect(stored[8:])
    if stored.startswith("plain:"):
        return stored[6:]
    return stored


def key_storage_mode() -> str:
    # report the REAL mode: if encryption ever fell back to plaintext, the
    # config on disk holds "plain:" keys even though _KEY_STORAGE says dpapi
    if _PLAIN_FALLBACK:
        return "plain"
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for conf in (cfg.get("cloud") or {}).values():
            if isinstance(conf, dict) and (conf.get("key") or "").startswith("plain:"):
                return "plain"
    except Exception:
        pass
    return _KEY_STORAGE


def mask_key(key: str) -> str:
    if not key:
        return ""
    head = key[:7] if len(key) > 12 else key[:3]
    return head + "-\u2022" * 8


# ---------------------------------------------------------------- config I/O

def _deep_merge(base: dict, patch: dict) -> dict:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


class Config:
    def __init__(self):
        self.data = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        if _KEY_STORAGE == "fernet" and not os.path.exists(SECRET_KEY_PATH):
            from cryptography.fernet import Fernet
            with open(SECRET_KEY_PATH, "wb") as f:
                f.write(Fernet.generate_key())
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                _deep_merge(self.data, stored)
            except Exception:
                pass  # corrupted config -> fall back to defaults

    def save(self):
        with _LOCK:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, CONFIG_PATH)

    # -- convenience accessors -------------------------------------------
    def get(self, path: str, default=None):
        node = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, path: str, value):
        parts = path.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def internet_enabled(self) -> bool:
        return bool(self.get("general.internet_enabled", False))

    def get_cloud_key(self, provider: str) -> str:
        return decrypt_secret(self.get(f"cloud.{provider}.key", ""))

    def safe_view(self) -> dict:
        """Config copy safe to send to the UI: keys masked, never revealed."""
        view = copy.deepcopy(self.data)
        for prov, conf in view.get("cloud", {}).items():
            stored = conf.get("key", "")
            conf["key"] = ""
            conf["key_set"] = bool(stored)
            conf["key_masked"] = mask_key(decrypt_secret(stored)) if stored else ""
        return view


# Resolve once at import so encrypt_secret() can never run before the mode is known
# (it would otherwise silently fall back to plaintext).
_resolve_storage_mode()

_instance = None


def get_config() -> Config:
    global _instance
    if _instance is None:
        _instance = Config()
    return _instance
