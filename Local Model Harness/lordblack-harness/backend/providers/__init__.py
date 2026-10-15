"""Provider registry: local runtimes + cloud API providers behind one interface."""
from ..config import get_config
from .local import LMStudioRuntime, OllamaRuntime, LlamaCppServerRuntime, EmbeddedLlamaCppRuntime

RUNTIME_CLASSES = {
    "lmstudio": LMStudioRuntime,
    "ollama": OllamaRuntime,
    "llamacpp": LlamaCppServerRuntime,
    "llamacpp_embedded": EmbeddedLlamaCppRuntime,
}

_registry = {}


def get_runtime(runtime_id: str):
    cfg = get_config()
    entry = next((r for r in cfg.get("runtimes", []) if r["id"] == runtime_id), None)
    if not entry:
        return None
    cls = RUNTIME_CLASSES.get(entry["type"])
    if not cls:
        return None
    key = (runtime_id, entry["base_url"])
    if key not in _registry:
        _registry[key] = cls(entry)
    else:
        _registry[key].update(entry)
    return _registry[key]


def active_runtime():
    cfg = get_config()
    rid = cfg.get("active.runtime_id", "")
    rt = get_runtime(rid) if rid else None
    if rt:
        return rt
    # fall back to first enabled runtime
    for r in cfg.get("runtimes", []):
        if r.get("enabled"):
            rt = get_runtime(r["id"])
            if rt:
                return rt
    return None
