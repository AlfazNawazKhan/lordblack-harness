"""LordBlack Harness — FastAPI application: REST + SSE routes, static UI."""
import asyncio
import contextlib
import json
import logging
import os
import secrets
import time
import uuid

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, APP_NAME
from . import agents as agent_mod
from . import fsaccess, sysinfo
from .config import DATA_DIR, get_config, key_storage_mode, encrypt_secret
from .logs import (setup_logging, capture_loop, subscribe_logs, unsubscribe_logs,
                   recent_logs, recent_trace, LOG_PATH)
from .memory.embeddings import SERVICE
from .memory.store import STORE
from .providers import RUNTIME_CLASSES, get_runtime, active_runtime
from .providers.cloud import PROVIDERS

log = logging.getLogger("main")
START_TIME = time.time()

app = FastAPI(title=APP_NAME, version=__version__)
# No CORS middleware on purpose: the UI is served same-origin and LAN clients
# open this origin directly. A permissive CORS policy on an unauthenticated
# localhost API would let any website the user visits call privileged routes
# (grant folders, read files, wipe memory) from the browser.


@app.on_event("startup")
async def startup():
    setup_logging()
    capture_loop(asyncio.get_running_loop())
    cfg = get_config()
    # bind embedding service to the active runtime
    rt = active_runtime()
    if rt:
        SERVICE.set_runtime(rt)
    SERVICE.resolve()

    async def _probe_embeddings():
        """One live probe so the Memory panel shows the truth on first paint."""
        if not rt:
            return
        try:
            embed_model = None
            # any OpenAI-compatible runtime may host an embedding model
            # (LM Studio: nomic-embed-text, Ollama: pulled embed models)
            try:
                models = await rt.list_models()
            except Exception:
                models = []
            embed_model = next((m for m in models if "embed" in m.lower()), None)
            if rt.type == "ollama" and not embed_model:
                raise RuntimeError("no Ollama embedding model pulled")
            await rt.embed(["probe"], embed_model)
            log.info("embedding probe ok via %s (%s)", rt.name, embed_model or "default")
        except Exception as e:
            if not SERVICE._try_minilm():
                SERVICE.backend, SERVICE.detail = "keyword", "keyword (BM25 fallback)"
            log.info("embedding probe failed (%s); using %s", str(e)[:100], SERVICE.backend)

    asyncio.create_task(_probe_embeddings())
    asyncio.create_task(sysinfo.start_sampler())
    await agent_mod.SCHEDULER.start()
    log.info("%s v%s ready | data: %s | key storage: %s",
             APP_NAME, __version__, DATA_DIR, key_storage_mode())


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
               "Connection": "keep-alive"}


# ================================================================ health/config

@app.get("/api/health")
async def health():
    cfg = get_config()
    return {"ok": True, "name": APP_NAME, "version": __version__,
            "uptime_s": int(time.time() - START_TIME),
            "internet": cfg.internet_enabled(), "lan": cfg.get("general.lan_access"),
            "key_storage": key_storage_mode(),
            "runtime_connected": bool(active_runtime())}


@app.get("/api/config")
async def get_config_view():
    return get_config().safe_view()


@app.post("/api/config")
async def set_config(patch: dict):
    cfg = get_config()
    from .config import _deep_merge
    _deep_merge(cfg.data, patch)
    cfg.save()
    if patch.get("runtimes") or patch.get("active"):
        SERVICE.set_runtime(active_runtime())
        SERVICE.resolve()
    log.info("config updated: %s", ", ".join(patch.keys()))
    return cfg.safe_view()


@app.post("/api/config/keys/{provider}")
async def set_key(provider: str, body: dict):
    if provider not in PROVIDERS:
        raise HTTPException(404, "unknown provider")
    key = (body.get("key") or "").strip()
    cfg = get_config()
    cfg.set(f"cloud.{provider}.key", encrypt_secret(key))
    cfg.set(f"cloud.{provider}.enabled", bool(key))
    cfg.save()
    log.info("API key stored for %s (encrypted: %s)", provider, key_storage_mode())
    return {"ok": True, "key_storage": key_storage_mode()}


@app.post("/api/config/verify/{provider}")
async def verify_key(provider: str):
    cfg = get_config()
    if not cfg.internet_enabled():
        raise HTTPException(403, "Internet access is disabled in Settings")
    key = cfg.get_cloud_key(provider)
    if not key:
        raise HTTPException(400, "no key stored for " + provider)
    try:
        return await PROVIDERS[provider].verify(key)
    except Exception as e:
        return {"ok": False, "detail": str(e)[:200]}


# ---- cloud catalog / model listing (all gated on the internet toggle) -----

@app.post("/api/cloud/openrouter/catalog")
async def openrouter_catalog():
    """Refresh the OpenRouter catalog through the backend (respects internet gate)."""
    cfg = get_config()
    if not cfg.internet_enabled():
        raise HTTPException(403, "Internet access is disabled in Settings")
    key = cfg.get_cloud_key("openrouter")
    if not key:
        raise HTTPException(400, "OpenRouter key not set")
    try:
        models = await PROVIDERS["openrouter"].list_models(key)
    except Exception as e:
        raise HTTPException(502, f"OpenRouter unreachable: {str(e)[:150]}")
    cfg.set("cloud.openrouter.catalog", models[:400])
    cfg.save()
    return {"ok": True, "free": sum(1 for m in models if m.get("free")),
            "total": len(models)}


@app.get("/api/cloud/{provider}/models")
async def cloud_models(provider: str):
    if provider not in PROVIDERS:
        raise HTTPException(404, "unknown provider")
    cfg = get_config()
    if not cfg.internet_enabled():
        raise HTTPException(403, "Internet access is disabled in Settings")
    key = cfg.get_cloud_key(provider)
    if not key:
        raise HTTPException(400, f"no key stored for {provider}")
    try:
        return {"models": await PROVIDERS[provider].list_models(key)}
    except Exception as e:
        raise HTTPException(502, f"{provider} unreachable: {str(e)[:150]}")


# ================================================================ runtimes

@app.get("/api/runtimes")
async def list_runtimes():
    cfg = get_config()
    out = []
    for r in cfg.get("runtimes", []):
        rt = get_runtime(r["id"])
        ok, ms, detail = (False, None, "disabled") if not r.get("enabled") \
            else await rt.health() if rt else (False, None, "unknown type")
        models = []
        if ok and rt:
            try:
                models = await rt.list_models()
            except Exception:
                models = []
        out.append({**r, "status": {"ok": ok, "latency_ms": ms, "detail": detail},
                    "models": models})
    return {"runtimes": out, "active": cfg.safe_view()["active"]}


@app.post("/api/runtimes")
async def add_runtime(entry: dict):
    cfg = get_config()
    rtype = entry.get("type")
    if rtype not in RUNTIME_CLASSES:
        raise HTTPException(400, f"unknown runtime type: {rtype}")
    rid = entry.get("id") or rtype + "-" + uuid.uuid4().hex[:4]
    new_entry = {"id": rid, "type": rtype, "name": entry.get("name", rtype),
                 "base_url": entry.get("base_url", "").rstrip("/"),
                 "enabled": entry.get("enabled", True)}
    if entry.get("model_path"):
        new_entry["model_path"] = entry["model_path"]
    # dedupe: same type + same base_url is the same runtime — reuse its id
    runtimes = cfg.get("runtimes", [])
    dupe = next((r for r in runtimes if r["type"] == rtype
                 and r.get("base_url", "").rstrip("/") == new_entry["base_url"]), None)
    if dupe:
        new_entry["id"] = dupe["id"]
    runtimes = [r for r in runtimes if r["id"] != new_entry["id"]]
    runtimes.append(new_entry)
    cfg.set("runtimes", runtimes)
    rid = new_entry["id"]
    if not cfg.get("active.runtime_id"):
        cfg.set("active.runtime_id", rid)
        try:
            models = await get_runtime(rid).list_models()
            if models:
                cfg.set("active.model", models[0])
        except Exception:
            pass
    cfg.save()
    SERVICE.set_runtime(active_runtime())
    SERVICE.resolve()
    log.info("runtime added: %s (%s) at %s", rid, rtype, entry["base_url"])
    return {"ok": True, "id": rid}


@app.delete("/api/runtimes/{rid}")
async def del_runtime(rid: str):
    cfg = get_config()
    cfg.set("runtimes", [r for r in cfg.get("runtimes", []) if r["id"] != rid])
    if cfg.get("active.runtime_id") == rid:
        cfg.set("active.runtime_id", "")
        cfg.set("active.model", "")
    cfg.save()
    return {"ok": True}


@app.post("/api/runtimes/{rid}/set-active")
async def set_active(rid: str, body: dict = None):
    cfg = get_config()
    body = body or {}
    cfg.set("active.runtime_id", rid)
    if body.get("model"):
        cfg.set("active.model", body["model"])
    cfg.save()
    SERVICE.set_runtime(active_runtime())
    SERVICE.resolve()
    return {"ok": True, "active": cfg.safe_view()["active"]}


@app.post("/api/runtimes/{rid}/test")
async def test_runtime(rid: str):
    rt = get_runtime(rid)
    if not rt:
        raise HTTPException(404, "runtime not found")
    ok, ms, detail = await rt.health()
    models = []
    if ok:
        try:
            models = await rt.list_models(force=True)
        except Exception:
            pass
    return {"ok": ok, "latency_ms": ms, "detail": detail, "models": models}


@app.post("/api/runtimes/detect")
async def detect_runtimes():
    """Probe the common local ports for known runtimes."""
    probes = [("lmstudio", "http://127.0.0.1:1234", "/v1/models"),
              ("ollama", "http://127.0.0.1:11434", "/api/tags"),
              ("llamacpp", "http://127.0.0.1:8080", "/health"),
              ("llamacpp", "http://127.0.0.1:8081", "/health")]
    import httpx
    found = []
    async with httpx.AsyncClient(timeout=2.0) as cx:
        for rtype, base, path in probes:
            try:
                r = await cx.get(base + path)
                if r.status_code < 500:
                    found.append({"type": rtype, "base_url": base, "status": r.status_code})
            except Exception:
                pass
    return {"found": found}


@app.get("/api/models")
async def all_models():
    """All models across enabled runtimes, for dropdowns."""
    cfg = get_config()
    out = []
    for r in cfg.get("runtimes", []):
        if not r.get("enabled"):
            continue
        rt = get_runtime(r["id"])
        if not rt:
            continue
        try:
            models = await rt.list_models()
            # embedding models (nomic-embed-text etc.) cannot chat — keep them
            # out of the chat model picker; the embedding service finds them
            # through the runtime directly
            models = [m for m in models if "embed" not in m.lower()]
            out.append({"runtime_id": r["id"], "runtime_name": r.get("name", r["id"]),
                        "type": r["type"], "models": models})
        except Exception:
            out.append({"runtime_id": r["id"], "runtime_name": r.get("name", r["id"]),
                        "type": r["type"], "models": []})
    return out


# ================================================================ chat

@app.post("/api/chat")
async def chat(body: dict):
    from .pipeline import ChatPipeline

    async def gen():
        async for ev, data in ChatPipeline().run(body):
            yield sse(ev, data)
    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/api/trace/recent")
async def trace_recent():
    return {"trace": recent_trace()}


# ================================================================ memory

@app.get("/api/memory/documents")
async def memory_list():
    return {"documents": STORE.list_documents()}


@app.get("/api/memory/documents/{doc_id}")
async def memory_get(doc_id: str):
    doc = STORE.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "document not found")
    return doc


@app.post("/api/memory/documents")
async def memory_create(body: dict):
    title = (body.get("title") or "Untitled note").strip()[:120]
    content = body.get("content") or ""
    doc = STORE.create_document(title, content)
    return doc


@app.put("/api/memory/documents/{doc_id}")
async def memory_update(doc_id: str, body: dict):
    new_title = (body.get("title") or "").strip()
    if not new_title:
        # a content-only edit must not wipe the existing title
        old = STORE.get_document(doc_id)
        if not old:
            raise HTTPException(404, "document not found")
        new_title = old["title"]
    ok = STORE.update_document(doc_id, new_title[:120], body.get("content", ""))
    if not ok:
        raise HTTPException(404, "document not found")
    return {"ok": True}


@app.delete("/api/memory/documents/{doc_id}")
async def memory_delete(doc_id: str):
    if not STORE.delete_document(doc_id):
        raise HTTPException(404, "document not found")
    return {"ok": True}


@app.post("/api/memory/upload")
async def memory_upload(files: list[UploadFile] = File(...)):
    added = []
    for f in files:
        raw = await f.read()
        if len(raw) > 2_000_000:
            continue
        text = raw.decode("utf-8", "replace")
        name = os.path.splitext(os.path.basename(f.filename or "upload"))[0] or "upload"
        doc = STORE.create_document(name, text)
        added.append(doc)
    log.info("uploaded %d memory file(s)", len(added))
    return {"added": added}


@app.post("/api/memory/search")
async def memory_search(body: dict):
    results = await STORE.search(body.get("query", ""), body.get("k"))
    return {"results": results}


@app.get("/api/memory/status")
async def memory_status():
    return STORE.stats()


@app.post("/api/memory/reindex")
async def memory_reindex():
    n = await asyncio.to_thread(STORE.reindex_all)
    return {"ok": True, "reindexed": n}


@app.post("/api/memory/wipe")
async def memory_wipe():
    await asyncio.to_thread(STORE.wipe)
    log.warning("Harness Memory wiped by user")
    return {"ok": True}


# ================================================================ folders/files

@app.get("/api/fs/folders")
async def fs_folders():
    return {"folders": fsaccess.granted_folders()}


@app.post("/api/fs/folders")
async def fs_grant(body: dict):
    if body.get("pick"):
        res = await asyncio.to_thread(fsaccess.pick_folder_dialog)
        if res.get("error"):
            raise HTTPException(500, res["error"][:200])
        if not res.get("path"):
            return {"ok": False, "cancelled": True}
        body["path"] = res["path"]
    if not body.get("path"):
        raise HTTPException(400, "path required")
    return fsaccess.grant_folder(body["path"])


@app.delete("/api/fs/folders")
async def fs_revoke(path: str):
    fsaccess.revoke_folder(path)
    return {"ok": True}


@app.get("/api/fs/tree")
async def fs_tree(path: str):
    try:
        return {"entries": fsaccess.list_dir(path)}
    except (PermissionError, NotADirectoryError, ValueError) as e:
        raise HTTPException(403, str(e)[:200])


@app.get("/api/fs/file")
async def fs_file(path: str):
    try:
        return fsaccess.read_file(path)
    except PermissionError as e:
        raise HTTPException(403, str(e)[:200])
    except FileNotFoundError:
        raise HTTPException(404, "file not found")


@app.post("/api/fs/apply")
async def fs_apply(body: dict):
    try:
        return fsaccess.apply_file(body["path"], body["content"])
    except PermissionError as e:
        raise HTTPException(403, str(e)[:200])


@app.post("/api/fs/diff")
async def fs_diff(body: dict):
    try:
        return {"diff": fsaccess.diff_file(body["path"], body["content"])}
    except PermissionError as e:
        raise HTTPException(403, str(e)[:200])


# ================================================================ agents

@app.get("/api/agents")
async def agents_list():
    return {"agents": agent_mod.list_agents()}


@app.post("/api/agents")
async def agents_create(manifest: dict):
    saved = agent_mod.save_agent(manifest)
    if saved.get("trigger", {}).get("type") == "scheduled":
        agent_mod.SCHEDULER.schedule(saved)
    return saved


@app.put("/api/agents/{slug}")
async def agents_update(slug: str, manifest: dict):
    manifest["slug"] = slug
    saved = agent_mod.save_agent(manifest)
    if saved.get("trigger", {}).get("type") == "scheduled":
        agent_mod.SCHEDULER.schedule(saved)
    else:
        agent_mod.SCHEDULER.cancel(slug)
    return saved


@app.delete("/api/agents/{slug}")
async def agents_delete(slug: str):
    agent_mod.SCHEDULER.cancel(slug)
    return {"ok": agent_mod.delete_agent(slug)}


@app.post("/api/agents/{slug}/run")
async def agents_run(slug: str, body: dict):
    manifest = agent_mod.get_agent(slug)
    if not manifest:
        raise HTTPException(404, "agent not found")

    async def gen():
        async for ev, data in agent_mod.run_agent(manifest, body.get("input", ""), slug):
            yield sse(ev, data)
    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/api/agents/{slug}/runs")
async def agents_runs(slug: str):
    return {"runs": agent_mod.get_runs(slug)}


# ---- hosted agent endpoints (localhost + LAN if enabled) ------------------

@app.get("/agents/{slug}")
async def hosted_info(slug: str):
    m = agent_mod.get_agent(slug)
    if not m or not m.get("hosting", {}).get("enabled"):
        raise HTTPException(404, "hosted agent not found")
    return {"agent": m["name"], "slug": slug, "endpoint": f"/agents/{slug}/chat",
            "method": "POST", "body": {"message": "text"}}


@app.post("/agents/{slug}/chat")
async def hosted_chat(slug: str, request: Request):
    m = agent_mod.get_agent(slug)
    if not m or not m.get("hosting", {}).get("enabled"):
        raise HTTPException(404, "hosted agent not found")
    token = (m.get("hosting") or {}).get("token") or ""
    if token:
        auth = request.headers.get("authorization", "")
        if not secrets.compare_digest(auth, f"Bearer {token}"):
            raise HTTPException(401, "invalid or missing bearer token")
    else:
        # Tokenless hosted agents are reachable by anything on the network.
        # Their answers go straight to the HTTP caller, so private tools
        # (memory, granted files) must be disabled or private data could be
        # exfiltrated by asking the agent to read and repeat it.
        m = dict(m)
        m["tools"] = {t: False for t in agent_mod.PRIVATE_TOOLS} | {
            t: v for t, v in (m.get("tools") or {}).items()
            if t not in agent_mod.PRIVATE_TOOLS}
    ip = request.client.host if request.client else "?"
    if not agent_mod.check_rate_limit(slug, ip,
                                      int(m["hosting"].get("rate_limit_per_min", 30))):
        return JSONResponse({"error": "rate limit exceeded"}, status_code=429)
    body = await request.json()
    message = str(body.get("message", ""))[:8000]
    final = []
    async for ev, data in agent_mod.run_agent(m, message, slug):
        if ev == "token":
            final.append(data.get("t", ""))
        elif ev == "error":
            return JSONResponse({"error": data.get("message")}, status_code=502)
    return {"agent": slug, "response": "".join(final)}


# ================================================================ logs/system/db

@app.get("/api/logs/stream")
async def logs_stream():
    q = subscribe_logs()

    async def gen():
        try:
            while True:
                item = await q.get()
                yield sse("log", {"line": item})
        finally:
            unsubscribe_logs(q)
    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/api/logs/recent")
async def logs_recent():
    return {"lines": recent_logs()}


@app.get("/api/logs/download")
async def logs_download():
    if not os.path.exists(LOG_PATH):
        raise HTTPException(404, "no log file yet")
    return StreamingResponse(open(LOG_PATH, "rb"), media_type="text/plain",
                             headers={"Content-Disposition":
                                      "attachment; filename=harness.log"})


@app.get("/api/system/stream")
async def system_stream():
    q = sysinfo.subscribe()

    async def gen():
        try:
            while True:
                item = await q.get()
                yield sse("stats", item)
        finally:
            sysinfo.unsubscribe(q)
    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/api/system/ip")
async def system_ip():
    return {"ip": agent_mod.lan_ip()}


@app.post("/api/system/open-data-dir")
async def open_data_dir():
    """Open the data folder in the OS file explorer."""
    import subprocess
    import sys
    try:
        if sys.platform == "win32":
            os.startfile(DATA_DIR)  # noqa: S110
        elif sys.platform == "darwin":
            subprocess.Popen(["open", DATA_DIR])
        else:
            subprocess.Popen(["xdg-open", DATA_DIR])
        return {"ok": True, "path": DATA_DIR}
    except Exception as e:
        raise HTTPException(500, str(e)[:200])


@app.get("/api/db/stats")
async def db_stats():
    s = STORE.stats()
    s["agents"] = len(agent_mod.list_agents())
    s["data_dir"] = DATA_DIR
    return s


@app.post("/api/db/vacuum")
async def db_vacuum():
    STORE.db().execute("VACUUM")
    return {"ok": True}


@app.post("/api/memory/embeddings/download-minilm")
async def download_minilm():
    if not get_config().internet_enabled():
        raise HTTPException(403, "Internet access is disabled in Settings")
    return await asyncio.to_thread(SERVICE.download_minilm)


# ================================================================ static UI

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "frontend")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="ui")
