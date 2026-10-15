"""Chat orchestration pipeline: RAG injection, agentic external delegation,
local-to-local duet chat — every step emitting trace events for the AI Activity
panel. Privacy boundary: memory content and granted-folder files go ONLY to the
local model; cloud providers receive just the composed sub-task text.
"""
import json
import logging
import re
import time

from .config import get_config
from .logs import emit_trace
from .memory.store import STORE
from .providers import active_runtime, get_runtime
from .providers import cloud as cloud_mod

log = logging.getLogger("chat")

INTENT_SYSTEM = (
    "You are the routing module of a local AI harness. Decide if the user's message "
    "needs an external, more capable cloud model for a specific sub-task. Respond with "
    "ONLY a JSON object, no other text:\n"
    '{"need_external": false, "sub_task": null, "reason": "brief"}\n'
    'Set need_external=true only for tasks that clearly exceed a small local model '
    '(deep code analysis, long reasoning, specialized knowledge). The sub_task must be '
    "self-contained and contain NO private data, NO file contents, NO memory notes."
)

EXTRACT_SYSTEM = (
    "You extract information. You are given a sub-task and the raw output of an external "
    "model. Return ONLY the information from that output needed to fulfill the sub-task — "
    "no commentary, no preamble. If the output is unusable, return: EXTERNAL_FAILED"
)


def _now_ms() -> float:
    return time.perf_counter() * 1000


def _extract_json(text: str):
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        try:
            return json.loads(m.group(0).replace("'", '"'))
        except ValueError:
            return None


def _trim_messages(messages, budget_chars: int):
    """Keep system prompt + as much recent history as fits the char budget."""
    if not messages:
        return messages
    system = [m for m in messages if m["role"] == "system"]
    rest = [m for m in messages if m["role"] != "system"]
    kept, used = [], 0
    for m in reversed(rest):
        if used + len(m["content"]) > budget_chars and kept:
            break
        kept.insert(0, m)
        used += len(m["content"])
    return system + kept


async def collect_stream(gen):
    """Consume a chat_stream generator: return (full_text, usage)."""
    parts, usage = [], {}
    async for item in gen:
        if isinstance(item, str):
            parts.append(item)
        else:
            usage = item.get("usage") or {}
    return "".join(parts), usage


class ChatPipeline:
    def tr(self, phase, title, detail="", status="ok", t0=None, **extra):
        """Build a trace event tuple ready to yield."""
        latency = int(_now_ms() - t0) if t0 else None
        return ("trace", emit_trace(phase, title, detail, status, latency, **extra))

    # ================================================================ main chat
    async def run(self, body: dict):
        """Async generator yielding (event, data) SSE tuples."""
        cfg = get_config()
        messages = body.get("messages") or []
        rag = bool(body.get("rag", True))
        agentic = bool(body.get("agentic", False))
        duet = body.get("duet") or {}
        file_context = body.get("file_context") or []
        runtime = active_runtime()
        model = body.get("model") or cfg.get("active.model", "")

        if not runtime:
            yield ("error", {"message": "No local runtime connected. Open Settings → "
                                        "Model & API Integrations and connect LM Studio, "
                                        "Ollama, or llama.cpp."})
            return
        if not model:
            try:
                models = await runtime.list_models()
                model = models[0] if models else ""
            except Exception:
                pass
        if not model:
            yield ("error", {"message": f"No model available on {runtime.name}. "
                                        "Load a model in your runtime first."})
            return

        yield self.tr("input", "Input parsed", f"{len(messages)} messages | model: {model}")
        t0 = _now_ms()

        # ---- 1. build local context (memory + files) ------------------------
        memory_chunks = []
        if rag:
            query = messages[-1]["content"][:1000] if messages else ""
            try:
                memory_chunks = await STORE.search(query)
                detail = (f"{len(memory_chunks)} chunks: "
                          + ", ".join(c["doc_title"] for c in memory_chunks[:3])) \
                    if memory_chunks else "no strong matches"
                yield self.tr("memory", "Memory search", detail,
                              "ok" if memory_chunks else "warn", t0)
            except Exception as e:
                yield self.tr("memory", "Memory search failed", str(e)[:140], "warn", t0)
                memory_chunks = []
            t0 = _now_ms()

        file_block = ""
        if file_context:
            from . import fsaccess
            parts, total = [], 0
            for p in file_context[:10]:
                try:
                    f = fsaccess.read_file(p)
                    text = f["content"][:4000]
                    parts.append(f"--- {p} ---\n{text}")
                    total += len(text)
                    if total > cfg.get("general.context_chars", 6000):
                        break
                except Exception as e:
                    parts.append(f"--- {p} --- (unavailable: {str(e)[:80]})")
            file_block = ("\n\n# Workspace files\n" + "\n\n".join(parts)) if parts else ""
            yield self.tr("context", "Workspace files attached",
                          f"{len(file_context)} file(s), {total} chars", "ok", t0)
            t0 = _now_ms()

        system_parts = ["You are LordBlack Harness, a helpful local AI assistant. "
                        "Answer directly and concisely."]
        if memory_chunks:
            mem_block = "\n\n".join(
                f"[{c['doc_title']}" + (f" › {c['heading']}" if c['heading'] else "") + "]\n"
                + c["text"] for c in memory_chunks)
            system_parts.append(
                "# Relevant memory (retrieved from the user's private memory store)\n"
                + mem_block)
        if file_block:
            system_parts.append(file_block)
        # file creation/editing capability — always on for granted folders
        from . import fsaccess as _fs
        folders = _fs.granted_folders()
        if folders:
            system_parts.append(
                "# Workspace file editing\n"
                "You CAN create new files and edit existing ones inside these approved "
                "workspace folders:\n" + "\n".join(folders) + "\n"
                'To propose a file (new or edited), output a fenced code block tagged with '
                'the target path relative to a workspace folder, e.g.:\n'
                '```html file="index.html"\n...full file content...\n```\n'
                'Always output the COMPLETE file content for that path. The user will see '
                'Apply/Diff buttons and confirms every write. Never propose files outside '
                "the approved folders.")

        local_messages = _trim_messages(
            [{"role": "system", "content": "\n\n".join(system_parts)}] + messages,
            int(cfg.get("general.context_chars", 6000)) * 2)

        # ---- 2. agentic intent routing + external delegation -----------------
        external_result, external_model, provider_id = None, None, None
        intent = {}
        if agentic and cfg.internet_enabled():
            t0 = _now_ms()
            try:
                intent_raw, _ = await collect_stream(runtime.chat_stream(
                    [{"role": "system", "content": INTENT_SYSTEM},
                     {"role": "user", "content": messages[-1]["content"][:2000]}],
                    model, temperature=0.1, max_tokens=160))
                intent = _extract_json(intent_raw) or {}
                yield self.tr("route", "Analyzing routing & intent",
                              intent.get("reason") or ("external help: yes"
                                                       if intent.get("need_external")
                                                       else "local model sufficient"),
                              "ok", t0, model=model)
            except Exception as e:
                yield self.tr("route", "Routing failed — staying local", str(e)[:140], "warn", t0)
            t0 = _now_ms()

            if intent.get("need_external") and intent.get("sub_task"):
                try:
                    external_result, external_model, provider_id = await self._delegate(
                        intent["sub_task"])
                    yield self.tr("external", "External API model",
                                  f"{provider_id}:{external_model}", "ok", t0)
                except Exception as e:
                    yield self.tr("external", "External call failed — staying local",
                                  str(e)[:160], "warn", t0)
                t0 = _now_ms()
        elif agentic:
            yield self.tr("route", "Agentic mode", "internet access disabled — staying local",
                          "warn")

        # ---- 3. extraction of external output --------------------------------
        if external_result:
            t0 = _now_ms()
            try:
                extracted, _ = await collect_stream(runtime.chat_stream(
                    [{"role": "system", "content": EXTRACT_SYSTEM},
                     {"role": "user", "content": f"Sub-task: {intent.get('sub_task')}\n\n"
                                                 f"External output:\n{external_result[:6000]}"}],
                    model, temperature=0.2, max_tokens=1024))
                if "EXTERNAL_FAILED" in extracted or not extracted.strip():
                    extracted = external_result[:2000]
                yield self.tr("extract", "Response extraction",
                              f"kept {len(extracted)} of {len(external_result)} chars", "ok", t0)
            except Exception as e:
                extracted = external_result[:2000]
                yield self.tr("extract", "Extraction failed — using raw output",
                              str(e)[:140], "warn", t0)
            t0 = _now_ms()
            local_messages.append({"role": "system", "content":
                f"A more capable external model ({provider_id}:{external_model}) was consulted "
                f"for a sub-task. Relevant extracted information:\n{extracted}\n"
                "Use it if helpful; answer the user in your own words."})

        # ---- 4. duet (local-to-local) ----------------------------------------
        second_model = duet.get("model") if duet.get("enabled") else None
        second_runtime = get_runtime(duet.get("runtime_id")) if duet.get("runtime_id") else None
        use_duet = bool(second_runtime and second_model and second_model != model)

        # ---- 5. final generation ----------------------------------------------
        final_t0 = _now_ms()
        try:
            if use_duet:
                async for ev in self._duet(local_messages, runtime, model,
                                           second_runtime, second_model):
                    yield ev
                full, usage = "", {}
            else:
                full, usage = "", {}
                async for item in runtime.chat_stream(local_messages, model):
                    if isinstance(item, str):
                        yield ("token", {"t": item})
                    else:
                        usage = item.get("usage") or {}
                        full = ""
            await self._final_trace(full, usage, final_t0, bool(external_result))
            yield ("done", {"usage": usage, "model": model,
                            "external": {"provider": provider_id, "model": external_model}
                            if external_model else None,
                            "answered_by": f"{second_runtime.name}:{second_model}"
                                           if use_duet else model,
                            "memory_chunks": memory_chunks,
                            "latency_ms": int(_now_ms() - final_t0)})
        except Exception as e:
            yield self.tr("output", "Generation failed", str(e)[:200], "error", final_t0)
            yield ("error", {"message": f"Local model error: {str(e)[:300]}"})

    async def _final_trace(self, full, usage, t0, had_external):
        emit_trace("synthesize", "Final answer generated" if had_external
                   else "Response generated",
                   f"{usage.get('completion_tokens') or '?'} tokens", "ok", int(_now_ms() - t0))
        emit_trace("output", "Answer delivered", "streaming complete", "ok", None)

    # ================================================================ duet
    async def _duet(self, messages, runtime_a, model_a, runtime_b, model_b):
        """Local-to-local: model A drafts, model B critiques and improves."""
        t0 = _now_ms()
        try:
            draft, _ = await collect_stream(runtime_a.chat_stream(messages, model_a,
                                                                  max_tokens=1024))
            yield self.tr("duet", f"Draft by {runtime_a.name}:{model_a}",
                          f"{len(draft)} chars", "ok", t0)
        except Exception as e:
            yield self.tr("duet", "Draft failed — answering solo", str(e)[:140], "warn", t0)
            return
        t0 = _now_ms()
        refine_msgs = messages + [
            {"role": "assistant", "content": draft},
            {"role": "user", "content":
                "Critique and improve the draft answer above. Fix mistakes, fill gaps, "
                "keep what is correct. Output ONLY the improved full answer."}]
        try:
            async for item in runtime_b.chat_stream(refine_msgs, model_b, max_tokens=1024):
                if isinstance(item, str):
                    yield ("token", {"t": item, "model": f"{runtime_b.name}:{model_b}"})
            yield self.tr("duet", f"Refined by {runtime_b.name}:{model_b}", "", "ok", t0)
        except Exception as e:
            yield self.tr("duet", "Refinement failed", str(e)[:140], "warn", t0)
            for ch in draft:
                yield ("token", {"t": ch})

    # ================================================================ delegation
    DEFAULT_CLOUD_MODELS = {"anthropic": "claude-sonnet-4-20250514", "openai": "gpt-4o-mini"}

    async def _delegate(self, sub_task: str):
        """Call an external cloud model with ONLY the composed sub-task text."""
        cfg = get_config()
        if cfg.get("cloud.openrouter.enabled") and cfg.get_cloud_key("openrouter"):
            provider = cloud_mod.PROVIDERS["openrouter"]
            provider_id = "openrouter"
            key = cfg.get_cloud_key("openrouter")
            catalog = cfg.get("cloud.openrouter.catalog") or []
            if not catalog:
                catalog = await provider.list_models(key)
                cfg.set("cloud.openrouter.catalog", catalog[:400])
                cfg.save()
            preferred = cfg.get("cloud.openrouter.preferred_model", "")
            if cfg.get("cloud.openrouter.prefer_free", True):
                model = provider.pick_free_model(catalog, preferred)
                if not model and cfg.get("cloud.openrouter.allow_paid", False):
                    model = preferred or (catalog[0]["id"] if catalog else None)
            else:
                model = preferred or (catalog[0]["id"] if catalog else None)
            if not model:
                raise RuntimeError("no suitable model in OpenRouter catalog "
                                   "(enable 'allow paid' or set a preferred model)")
        elif cfg.get("cloud.anthropic.enabled") and cfg.get_cloud_key("anthropic"):
            provider = cloud_mod.PROVIDERS["anthropic"]
            provider_id, key = "anthropic", cfg.get_cloud_key("anthropic")
            model = cfg.get("cloud.anthropic.model") or self.DEFAULT_CLOUD_MODELS["anthropic"]
        elif cfg.get("cloud.openai.enabled") and cfg.get_cloud_key("openai"):
            provider = cloud_mod.PROVIDERS["openai"]
            provider_id, key = "openai", cfg.get_cloud_key("openai")
            model = cfg.get("cloud.openai.model") or self.DEFAULT_CLOUD_MODELS["openai"]
        else:
            raise RuntimeError("no external provider connected")
        answer = await provider.chat(key, model, [
            {"role": "system", "content": "You are a focused assistant helping a local AI "
                                          "harness with a specific sub-task. Answer the "
                                          "sub-task directly."},
            {"role": "user", "content": sub_task}], max_tokens=1500)
        log.info("delegated to %s:%s (%d chars back)", provider_id, model, len(answer))
        return answer, model, provider_id
