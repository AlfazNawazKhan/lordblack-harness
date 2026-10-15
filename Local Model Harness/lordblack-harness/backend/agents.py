"""Agent builder: manifest-defined agents running on local models.

Agents live as JSON manifests in data/agents/. They can run manually (chat,
streamed with the same trace pipeline), on a schedule, or be hosted as local
HTTP endpoints (rate-limited) reachable from localhost or the LAN.
"""
import asyncio
import json
import logging
import os
import re
import time

from .config import DATA_DIR, get_config
from .logs import emit_trace
from .providers import get_runtime
from .providers import cloud as cloud_mod

log = logging.getLogger("agent")
AGENTS_DIR = os.path.join(DATA_DIR, "agents")

TOOL_SCHEMAS = {
    "memory_search": "Search the user's private memory. args: {\"query\": \"...\"}",
    "memory_add": "Save a note to memory. args: {\"title\": \"...\", \"content\": \"...\"}",
    "read_file": "Read a file from an approved workspace folder. args: {\"path\": \"...\"}",
    "list_files": "List files in an approved folder. args: {\"path\": \"...\"}",
    "external_lookup": "Ask a stronger external cloud model one focused question "
                       "(no private data is ever sent). args: {\"task\": \"...\"}",
    "bluetooth_scan": "Scan nearby Bluetooth devices. args: {}",
}

RATE_BUCKETS = {}  # ip -> deque[timestamps]

# tools whose results can expose private user data through the agent's answer
PRIVATE_TOOLS = {"memory_search", "memory_add", "read_file", "list_files"}


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9\-]+", "-", name.lower()).strip("-") or "agent"


def default_manifest() -> dict:
    return {
        "name": "New Agent", "slug": "", "description": "",
        "system_prompt": "You are a helpful autonomous agent.",
        "runtime_id": "", "model": "",
        "max_turns": 6, "temperature": 0.4,
        "tools": {"memory_search": True, "memory_add": False, "read_file": False,
                  "list_files": False, "external_lookup": False, "bluetooth_scan": False},
        "trigger": {"type": "manual", "interval_minutes": 30},
        "hosting": {"enabled": False, "rate_limit_per_min": 30},
        "created": time.time(),
    }


def list_agents() -> list:
    os.makedirs(AGENTS_DIR, exist_ok=True)
    out = []
    for f in sorted(os.listdir(AGENTS_DIR)):
        if f.endswith(".json") and not f.endswith(".runs.json"):
            try:
                with open(os.path.join(AGENTS_DIR, f), "r", encoding="utf-8") as fh:
                    out.append(json.load(fh))
            except Exception:
                pass
    return out


def get_agent(slug: str):
    for a in list_agents():
        if a["slug"] == slug:
            return a
    return None


def save_agent(manifest: dict) -> dict:
    os.makedirs(AGENTS_DIR, exist_ok=True)
    manifest.setdefault("created", time.time())
    # always re-derive the slug: a caller-supplied slug is used in file paths
    # and must never be able to escape data/agents/ (path traversal)
    manifest["slug"] = slugify(manifest.get("slug") or manifest.get("name") or "agent")
    base = default_manifest()
    for k, v in base.items():
        manifest.setdefault(k, v)
    path = os.path.join(AGENTS_DIR, manifest["slug"] + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    log.info("saved agent '%s'", manifest["slug"])
    return manifest


def delete_agent(slug: str) -> bool:
    slug = slugify(slug or "")
    p = os.path.join(AGENTS_DIR, slug + ".json")
    if os.path.exists(p):
        os.remove(p)
        rp = os.path.join(AGENTS_DIR, slug + ".runs.json")
        if os.path.exists(rp):
            os.remove(rp)
        return True
    return False


def record_run(slug: str, run: dict):
    p = os.path.join(AGENTS_DIR, slug + ".runs.json")
    runs = []
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                runs = json.load(f)
        except Exception:
            runs = []
    runs.append(run)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(runs[-50:], f, indent=2, ensure_ascii=False)


def get_runs(slug: str) -> list:
    p = os.path.join(AGENTS_DIR, slug + ".runs.json")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


# ================================================================ execution

def _parse_reply(text: str):
    """Agents reply with JSON: {"tool": ..., "args": ...} or {"answer": ...}."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None, text.strip()
    try:
        obj = json.loads(m.group(0))
        if isinstance(obj, dict):
            if obj.get("answer"):
                return None, str(obj["answer"])
            if obj.get("tool"):
                return str(obj["tool"]), obj.get("args") or {}
    except ValueError:
        pass
    return None, text.strip()


async def dispatch_tool(name: str, args: dict, manifest: dict, trace) -> str:
    if name == "memory_search":
        from .memory.store import STORE
        res = await STORE.search(str(args.get("query", ""))[:500], k=5)
        return "\n\n".join(f"[{r['doc_title']}] {r['text'][:400]}" for r in res) \
            or "no results"
    if name == "memory_add":
        from .memory.store import STORE
        doc = STORE.create_document(str(args.get("title", "agent note"))[:80],
                                    str(args.get("content", ""))[:20000])
        return f"saved to memory as {doc['id']}"
    if name in ("read_file", "list_files"):
        from . import fsaccess
        if name == "read_file":
            f = fsaccess.read_file(str(args.get("path", "")))
            return f["content"][:6000]
        entries = fsaccess.list_dir(str(args.get("path", "")))
        return "\n".join(f"{e['type']}: {e['path']}" for e in entries[:100]) or "empty"
    if name == "external_lookup":
        cfg = get_config()
        if not cfg.internet_enabled():
            return "internet access is disabled"
        pipeline_mod = _pipeline()
        try:
            answer, model, prov = await pipeline_mod._delegate(str(args.get("task", ""))[:2000])
            return f"[{prov}:{model}] {answer[:3000]}"
        except Exception as e:
            return f"external lookup failed: {str(e)[:150]}"
    if name == "bluetooth_scan":
        cfg = get_config()
        if not cfg.get("general.bluetooth_enabled"):
            return "bluetooth module not enabled in Security settings"
        try:
            from bleak import BleakScanner
            devices = await BleakScanner.discover(timeout=5)
            return "\n".join(f"{d.address} {d.name or 'unnamed'} ({d.rssi} dBm)"
                             for d in devices[:20]) or "no devices found"
        except Exception as e:
            return f"bluetooth unavailable ({str(e)[:120]})"
    return f"unknown tool: {name}"


def _pipeline():
    from .pipeline import ChatPipeline
    return ChatPipeline()


async def run_agent(manifest: dict, user_input: str, run_id: str):
    """Async generator yielding (event, data) — same SSE protocol as chat."""
    cfg = get_config()
    runtime = get_runtime(manifest.get("runtime_id") or cfg.get("active.runtime_id", ""))
    model = manifest.get("model") or cfg.get("active.model", "")
    if not runtime:
        yield ("error", {"message": "agent runtime not connected"})
        return

    def tr(phase, title, detail="", status="ok", **extra):
        return ("trace", emit_trace(phase, title, detail, status, extra.pop("latency_ms", None),
                                    agent=manifest["slug"], **extra))

    enabled_tools = [t for t, on in (manifest.get("tools") or {}).items() if on]
    tool_docs = "\n".join(f'- {t}: {TOOL_SCHEMAS[t]}' for t in enabled_tools) or "- (none)"
    system = (manifest.get("system_prompt", "") +
              "\n\nYou can use tools. To use one, reply with ONLY a JSON object: "
              '{"tool": "<name>", "args": {...}}. When you have enough information to '
              "fulfill the request, reply with ONLY: {\"answer\": \"<your final answer>\"}.\n"
              f"Available tools:\n{tool_docs}")

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user_input}]
    yield tr("agent", f"Agent '{manifest['name']}' started",
             f"{runtime.name}:{model} | tools: {', '.join(enabled_tools) or 'none'}")

    final_answer, used_tools = "", []
    max_turns = int(manifest.get("max_turns", 6))
    for turn in range(max_turns):
        t0 = time.perf_counter() * 1000
        try:
            reply, _ = await _collect_agent(runtime.chat_stream(
                messages, model, temperature=float(manifest.get("temperature", 0.4)),
                max_tokens=1024))
        except Exception as e:
            yield tr("agent", "Model call failed", str(e)[:160], "error")
            yield ("error", {"message": f"Local model error: {str(e)[:300]}"})
            return
        tool, payload = _parse_reply(reply)
        latency = int(time.perf_counter() * 1000 - t0)

        if tool is None:
            final_answer = payload
            yield tr("agent", f"Final answer (turn {turn + 1})",
                     f"{len(final_answer)} chars", "ok", latency_ms=latency)
            for ch in final_answer:
                yield ("token", {"t": ch})
            break

        used_tools.append(tool)
        yield tr("tool", f"Tool: {tool}", json.dumps(payload)[:200], "ok", latency_ms=latency)
        try:
            result = await dispatch_tool(tool, payload if isinstance(payload, dict) else {},
                                         manifest, None)
        except Exception as e:
            result = f"tool error: {str(e)[:200]}"
        yield tr("tool", f"Tool result: {tool}", result[:160].replace("\n", " "), "ok")
        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "user", "content":
                         f"TOOL RESULT ({tool}):\n{result[:4000]}\n\nContinue. Use another tool "
                         'or give your final answer as {"answer": "..."}.'})
    else:
        final_answer = final_answer or \
            "I could not complete this task within the allowed number of turns."
        yield tr("agent", "Max turns reached", "returning partial result", "warn")
        for ch in final_answer:
            yield ("token", {"t": ch})

    yield ("done", {"agent": manifest["slug"], "tools_used": used_tools})
    record_run(manifest["slug"], {"ts": time.time(), "input": user_input[:2000],
                                  "output": final_answer[:8000], "tools": used_tools})


async def _collect_agent(gen):
    parts, usage = [], {}
    async for item in gen:
        if isinstance(item, str):
            parts.append(item)
        else:
            usage = item.get("usage") or {}
    return "".join(parts), usage


# ================================================================ scheduler

class Scheduler:
    """Runs scheduled agents on simple intervals."""

    def __init__(self):
        self.tasks = {}

    async def start(self):
        for m in list_agents():
            if m.get("trigger", {}).get("type") == "scheduled":
                self.schedule(m)

    def schedule(self, manifest: dict):
        self.cancel(manifest["slug"])
        minutes = int(manifest.get("trigger", {}).get("interval_minutes", 30)) or 30
        self.tasks[manifest["slug"]] = asyncio.create_task(
            self._loop(manifest, minutes * 60))

    def cancel(self, slug: str):
        t = self.tasks.pop(slug, None)
        if t:
            t.cancel()

    async def _loop(self, manifest: dict, interval: float):
        while True:
            await asyncio.sleep(interval)
            try:
                input_text = manifest.get("trigger", {}).get("prompt") \
                    or "Run your scheduled task."
                out, tools = "", []
                async for ev, data in run_agent(manifest, input_text, "sched"):
                    if ev == "token":
                        out += data.get("t", "")
                    elif ev == "done":
                        tools = data.get("tools_used", [])
                log.info("scheduled agent '%s' finished (%d chars)", manifest["slug"], len(out))
            except Exception as e:
                log.warning("scheduled agent '%s' failed: %s", manifest["slug"], e)


SCHEDULER = Scheduler()


# ================================================================ hosting

def check_rate_limit(slug: str, ip: str, per_min: int) -> bool:
    from collections import deque
    key = (slug, ip)
    now = time.time()
    bucket = RATE_BUCKETS.setdefault(key, deque())
    while bucket and bucket[0] < now - 60:
        bucket.popleft()
    if len(bucket) >= max(1, per_min):
        return False
    bucket.append(now)
    return True


def lan_ip() -> str:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()
