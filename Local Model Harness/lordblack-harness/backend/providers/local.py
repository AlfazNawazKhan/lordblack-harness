"""Local model runtimes behind one normalized interface.

Every runtime exposes: health(), list_models(), chat_stream(messages, model, ...)
(yields token strings, final chunk is a dict with usage), and embed(texts, model).
Built for slow hardware: long read timeouts, no buffering of full responses.
"""
import asyncio
import json
import logging

import httpx

log = logging.getLogger("provider")
TIMEOUT = httpx.Timeout(connect=5.0, read=600.0, write=60.0, pool=30.0)


class LocalRuntime:
    type_name = "base"

    def __init__(self, entry: dict):
        self.id = entry["id"]
        self.type = entry.get("type", "base")
        self.name = entry.get("name", self.type)
        self.base_url = entry.get("base_url", "").rstrip("/")
        self.enabled = entry.get("enabled", True)
        self.extra = entry

    def update(self, entry: dict):
        self.base_url = entry.get("base_url", self.base_url).rstrip("/")
        self.name = entry.get("name", self.name)
        self.enabled = entry.get("enabled", self.enabled)

    # ------------------------------------------------------------------ core
    async def health(self):
        """Return (ok: bool, latency_ms: int|None, detail: str)."""
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
                import time
                t0 = time.perf_counter()
                r = await cx.get(self.health_url())
                ms = int((time.perf_counter() - t0) * 1000)
                if r.status_code < 500:
                    return True, ms, "reachable"
                return False, ms, f"HTTP {r.status_code}"
        except Exception as e:
            return False, None, str(e)[:160]

    def health_url(self) -> str:
        return self.base_url + "/v1/models"

    async def list_models(self, force=False):
        raise NotImplementedError

    async def chat_stream(self, messages, model: str, temperature=0.7, max_tokens=2048):
        """Async generator: yields str tokens; last yield is dict(usage=..., model=...)."""
        raise NotImplementedError

    async def embed(self, texts, model=None):
        raise NotImplementedError(f"{self.type_name} does not support embeddings")

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _extract_usage(obj: dict) -> dict:
        u = obj.get("usage") or {}
        return {"prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens")}


class OpenAICompatMixin:
    """Shared OpenAI-compatible implementation (LM Studio, llama.cpp server)."""

    async def list_models(self, force=False):
        async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
            r = await cx.get(self.base_url + "/v1/models")
            r.raise_for_status()
            return [m["id"] for m in r.json().get("data", [])]

    async def chat_stream(self, messages, model: str, temperature=0.7, max_tokens=2048):
        payload = {"model": model, "messages": messages, "temperature": temperature,
                   "max_tokens": max_tokens, "stream": True,
                   "stream_options": {"include_usage": True}}
        usage = {"prompt_tokens": None, "completion_tokens": None}
        async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
            async with cx.stream("POST", self.base_url + "/v1/chat/completions",
                                 json=payload) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode("utf-8", "replace")[:300]
                    raise RuntimeError(f"{self.name} HTTP {r.status_code}: {body}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    if obj.get("usage"):
                        usage = self._extract_usage(obj)
                    for ch in obj.get("choices") or []:
                        delta = (ch.get("delta") or {}).get("content")
                        if delta:
                            yield delta
        yield {"usage": usage, "model": model}

    async def embed(self, texts, model=None):
        async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
            r = await cx.post(self.base_url + "/v1/embeddings",
                              json={"model": model or "embedding", "input": list(texts)})
            r.raise_for_status()
            data = sorted(r.json().get("data", []), key=lambda d: d.get("index", 0))
            return [d["embedding"] for d in data]


class LMStudioRuntime(OpenAICompatMixin, LocalRuntime):
    type_name = "lmstudio"

    async def embed(self, texts, model=None):
        # LM Studio requires the *loaded embedding model* id; use current one.
        if not model:
            models = await self.list_models()
            model = models[0] if models else "embedding"
        return await super().embed(texts, model)


class LlamaCppServerRuntime(OpenAICompatMixin, LocalRuntime):
    type_name = "llamacpp"

    def health_url(self):
        return self.base_url + "/health"


class OllamaRuntime(LocalRuntime):
    type_name = "ollama"
    _models_cache = None
    _models_ts = 0.0

    def health_url(self):
        return self.base_url + "/api/version"

    async def list_models(self, force=False):
        # called on every memory search (to find an embedding model) — cache 30 s
        import time
        if not force and self._models_cache is not None \
                and time.time() - self._models_ts < 30:
            return self._models_cache
        async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
            r = await cx.get(self.base_url + "/api/tags")
            r.raise_for_status()
            self._models_cache = [m["name"] for m in r.json().get("models", [])]
            self._models_ts = time.time()
            return self._models_cache

    async def chat_stream(self, messages, model: str, temperature=0.7, max_tokens=2048):
        payload = {"model": model, "messages": messages, "stream": True,
                   "options": {"temperature": temperature, "num_predict": max_tokens}}
        usage = {"prompt_tokens": None, "completion_tokens": None}
        async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
            async with cx.stream("POST", self.base_url + "/api/chat", json=payload) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode("utf-8", "replace")[:300]
                    raise RuntimeError(f"Ollama HTTP {r.status_code}: {body}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if obj.get("done"):
                        usage = {"prompt_tokens": obj.get("prompt_eval_count"),
                                 "completion_tokens": obj.get("eval_count")}
                        break
                    content = (obj.get("message") or {}).get("content")
                    if content:
                        yield content
        yield {"usage": usage, "model": model}

    async def embed(self, texts, model=None):
        async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
            r = await cx.post(self.base_url + "/api/embed",
                              json={"model": model or "nomic-embed-text", "input": list(texts)})
            if r.status_code == 404:
                # older Ollama: one prompt per call
                out = []
                for t in texts:
                    r2 = await cx.post(self.base_url + "/api/embeddings",
                                       json={"model": model or "nomic-embed-text", "prompt": t})
                    r2.raise_for_status()
                    out.append(r2.json()["embedding"])
                return out
            r.raise_for_status()
            return r.json().get("embeddings", [])


class EmbeddedLlamaCppRuntime(LocalRuntime):
    """In-process llama.cpp via optional llama-cpp-python (no server needed).

    Config entry carries `model_path` (path to a .gguf file).
    """
    type_name = "llamacpp_embedded"
    _llama = None
    _loaded_path = None
    _lock = asyncio.Lock()

    def health_url(self):
        return self.base_url or "http://embedded.invalid/"

    async def health(self):
        if not self.extra.get("model_path"):
            return False, None, "no model_path configured"
        try:
            import llama_cpp  # noqa: F401
            return True, 0, f"llama-cpp-python ready: {self.extra.get('model_path')}"
        except Exception as e:
            return False, None, f"llama_cpp not installed ({e})"

    async def list_models(self):
        p = self.extra.get("model_path")
        return [p] if p else []

    def _get(self):
        from llama_cpp import Llama
        path = self.extra.get("model_path")
        if self._llama is None or self._loaded_path != path:
            self._llama = Llama(model_path=path, n_ctx=self.extra.get("n_ctx", 2048),
                                verbose=False)
            self._loaded_path = path
        return self._llama

    async def chat_stream(self, messages, model: str, temperature=0.7, max_tokens=2048):
        def _sync_gen():
            llama = self._get()
            stream = llama.create_chat_completion(messages=messages, temperature=temperature,
                                                  max_tokens=max_tokens, stream=True)
            for part in stream:
                delta = (part["choices"][0].get("delta") or {}).get("content")
                if delta:
                    yield delta

        # bridge the sync generator into async via a pump thread
        sync_iter = _sync_gen()
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def _pump():
            try:
                while True:
                    item = next(sync_iter)
                    loop.call_soon_threadsafe(q.put_nowait, ("tok", item))
            except StopIteration:
                loop.call_soon_threadsafe(q.put_nowait, ("__end__", None))
            except Exception as e:
                loop.call_soon_threadsafe(q.put_nowait, ("__err__", str(e)))

        import threading
        threading.Thread(target=_pump, daemon=True).start()
        usage = {"prompt_tokens": None, "completion_tokens": None}
        while True:
            kind, val = await q.get()
            if kind == "__end__":
                break
            if kind == "__err__":
                raise RuntimeError(f"embedded llama.cpp: {val}")
            if isinstance(val, str):
                yield val
        yield {"usage": usage, "model": model}

    async def embed(self, texts, model=None):
        def _run():
            llama = self._get()
            fn = getattr(llama, "embed", None)
            if not callable(fn):
                return None
            return [fn(t) for t in texts]
        res = await asyncio.to_thread(_run)
        if res is None:
            raise RuntimeError("embedded embeddings unavailable")
        return res
