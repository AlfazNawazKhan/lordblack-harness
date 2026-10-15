"""Cloud API providers — opt-in only, never given access to Harness Memory.

Every call here sends ONLY the text the local model explicitly composed for
delegation. No memory chunks, no granted-folder files, no conversation history.
"""
import logging

import httpx

log = logging.getLogger("cloud")
TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)


class CloudProvider:
    id = ""
    name = ""

    async def verify(self, key: str) -> dict:
        raise NotImplementedError

    async def list_models(self, key: str):
        return []

    async def chat(self, key: str, model: str, messages, max_tokens=1024) -> str:
        raise NotImplementedError


class OpenRouterProvider(CloudProvider):
    id = "openrouter"
    name = "OpenRouter"
    BASE = "https://openrouter.ai/api/v1"

    def _headers(self, key):
        return {"Authorization": f"Bearer {key}",
                "HTTP-Referer": "http://localhost:local",
                "X-Title": "LordBlack Harness"}

    async def verify(self, key: str) -> dict:
        async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
            r = await cx.get(self.BASE + "/auth/key", headers=self._headers(key))
            if r.status_code == 401:
                return {"ok": False, "detail": "invalid key"}
            r.raise_for_status()
            data = r.json().get("data", {})
            return {"ok": True, "detail": f"key valid"
                    + (f", usage ${data.get('usage', 0):.4f}" if "usage" in data else "")}

    async def list_models(self, key: str):
        async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
            r = await cx.get(self.BASE + "/models", headers=self._headers(key))
            r.raise_for_status()
            models = []
            for m in r.json().get("data", []):
                pricing = m.get("pricing") or {}
                free = pricing.get("prompt") == "0" and pricing.get("completion") == "0"
                models.append({"id": m["id"], "name": m.get("name", m["id"]),
                               "free": free,
                               "context": m.get("context_length")})
            return models

    async def chat(self, key: str, model: str, messages, max_tokens=1024) -> str:
        async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
            r = await cx.post(self.BASE + "/chat/completions", headers=self._headers(key),
                              json={"model": model, "messages": messages,
                                    "max_tokens": max_tokens})
            r.raise_for_status()
            data = r.json()
            choices = data.get("choices") or [{}]
            return (choices[0].get("message") or {}).get("content", "") or ""

    def pick_free_model(self, catalog, preferred: str = ""):
        """Choose a currently-free model: user preference first, then a small
        quality heuristic, then catalog order."""
        free = [m for m in catalog if m.get("free")]
        if preferred and any(m["id"] == preferred for m in free):
            return preferred
        prefs = ["deepseek", "qwen", "llama", "mistral", "gemma", "gpt"]
        for kw in prefs:
            for m in free:
                if kw in m["id"].lower():
                    return m["id"]
        return free[0]["id"] if free else None


class AnthropicProvider(CloudProvider):
    id = "anthropic"
    name = "Anthropic Claude"
    BASE = "https://api.anthropic.com/v1"

    def _headers(self, key):
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}

    async def verify(self, key: str) -> dict:
        async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
            r = await cx.get(self.BASE + "/models", headers=self._headers(key))
            if r.status_code == 401:
                return {"ok": False, "detail": "invalid key"}
            r.raise_for_status()
            n = len(r.json().get("data", []))
            return {"ok": True, "detail": f"key valid, {n} models available"}

    async def list_models(self, key: str):
        async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
            r = await cx.get(self.BASE + "/models", headers=self._headers(key))
            r.raise_for_status()
            return [{"id": m["id"], "name": m.get("display_name", m["id"]), "free": False}
                    for m in r.json().get("data", [])]

    async def chat(self, key: str, model: str, messages, max_tokens=1024) -> str:
        system = ""
        msgs = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                msgs.append({"role": m["role"], "content": m["content"]})
        async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
            r = await cx.post(self.BASE + "/messages", headers=self._headers(key),
                              json={"model": model, "max_tokens": max_tokens,
                                    **({"system": system} if system else {}), "messages": msgs})
            r.raise_for_status()
            return "".join(c.get("text", "") for c in r.json().get("content", []))


class OpenAIProvider(CloudProvider):
    id = "openai"
    name = "OpenAI GPT"
    BASE = "https://api.openai.com/v1"

    def _headers(self, key):
        return {"Authorization": f"Bearer {key}"}

    async def verify(self, key: str) -> dict:
        async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
            r = await cx.get(self.BASE + "/models", headers=self._headers(key))
            if r.status_code == 401:
                return {"ok": False, "detail": "invalid key"}
            r.raise_for_status()
            return {"ok": True, "detail": "key valid"}

    async def list_models(self, key: str):
        async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
            r = await cx.get(self.BASE + "/models", headers=self._headers(key))
            r.raise_for_status()
            return [{"id": m["id"], "name": m["id"], "free": False}
                    for m in r.json().get("data", [])]

    async def chat(self, key: str, model: str, messages, max_tokens=1024) -> str:
        async with httpx.AsyncClient(timeout=TIMEOUT) as cx:
            r = await cx.post(self.BASE + "/chat/completions", headers=self._headers(key),
                              json={"model": model, "messages": messages,
                                    "max_tokens": max_tokens})
            r.raise_for_status()
            return (r.json()["choices"][0].get("message") or {}).get("content", "") or ""


PROVIDERS = {p.id: p for p in (OpenRouterProvider(), AnthropicProvider(), OpenAIProvider())}
