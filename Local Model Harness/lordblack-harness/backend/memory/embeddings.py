"""Embedding backends, chosen by device capability (user override in Settings).

Priority in "auto" mode:
  1. runtime   — the connected local runtime's /embeddings endpoint (free, offline)
  2. minilm    — optional local ONNX MiniLM (needs `pip install onnxruntime tokenizers`,
                 model downloaded once to data/models/minilm)
  3. keyword   — pure-Python BM25, zero dependencies, works on any machine
"""
import logging
import math
import os
import struct

log = logging.getLogger("memory")

MINILM_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "models", "minilm")
MINILM_MODEL_URL = ("https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2"
                    "/resolve/main/onnx/model.onnx")
MINILM_TOKENIZER_URL = ("https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2"
                        "/resolve/main/tokenizer.json")


def normalize(vec):
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


def pack_vec(vec) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_vec(blob: bytes):
    return list(struct.unpack(f"<{len(blob)//4}f", blob))


def cosine(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))  # both pre-normalized


class EmbeddingService:
    """Resolves the best available backend and embeds query/chunk texts."""

    def __init__(self):
        self.backend = "keyword"     # resolved mode actually in use
        self.detail = "keyword (BM25 fallback)"
        self._runtime = None         # LocalRuntime for runtime mode
        self._runtime_embed_model = None
        self._minilm = None          # (session, tokenizer)

    def resolve(self, force=False):
        cfg_backend = "auto"
        try:
            from ..config import get_config
            cfg_backend = get_config().get("general.embedding_backend", "auto")
        except Exception:
            pass
        order = (["runtime", "minilm", "keyword"] if cfg_backend == "auto"
                 else [cfg_backend, "keyword"] if cfg_backend != "keyword" else ["keyword"])
        for cand in order:
            if cand == "runtime" and self._try_runtime():
                return self.backend
            if cand == "minilm" and self._try_minilm():
                return self.backend
        self.backend, self.detail = "keyword", "keyword (BM25 fallback)"
        return self.backend

    # ---------------------------------------------------------------- runtimes
    def set_runtime(self, runtime, embed_model=None):
        self._runtime = runtime
        self._runtime_embed_model = embed_model
        if runtime:
            self.resolve(force=True)

    def _try_runtime(self) -> bool:
        # Runtime embedding calls are async and made by the store; the first
        # real call falls back to keyword mode if the runtime has no embedding
        # model loaded.
        if not self._runtime:
            return False
        self.backend = "runtime"
        self.detail = f"runtime embeddings ({self._runtime.name})"
        return True

    # ---------------------------------------------------------------- minilm
    def _try_minilm(self) -> bool:
        try:
            import onnxruntime  # noqa: F401
            from tokenizers import Tokenizer  # noqa: F401
        except Exception:
            return False
        model_path = os.path.join(MINILM_DIR, "model.onnx")
        tok_path = os.path.join(MINILM_DIR, "tokenizer.json")
        if not (os.path.exists(model_path) and os.path.exists(tok_path)):
            return False
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
            so = ort.SessionOptions()
            so.intra_op_num_threads = max(1, (os.cpu_count() or 2) - 1)
            session = ort.InferenceSession(model_path, so, providers=["CPUExecutionProvider"])
            self._minilm = (session, Tokenizer.from_file(tok_path))
            self.backend = "minilm"
            self.detail = "local ONNX MiniLM (all-MiniLM-L6-v2)"
            return True
        except Exception as e:
            log.warning("MiniLM init failed: %s", e)
            return False

    def minilm_downloaded(self) -> bool:
        return os.path.exists(os.path.join(MINILM_DIR, "model.onnx"))

    def download_minilm(self) -> dict:
        """One-time download of the ~90MB quantized MiniLM (requires internet)."""
        os.makedirs(MINILM_DIR, exist_ok=True)
        import httpx
        for url, name in ((MINILM_MODEL_URL, "model.onnx"),
                          (MINILM_TOKENIZER_URL, "tokenizer.json")):
            dest = os.path.join(MINILM_DIR, name)
            if os.path.exists(dest) and os.path.getsize(dest) > 1000:
                continue
            with httpx.Client(timeout=httpx.Timeout(connect=15, read=600, write=30, pool=30),
                              follow_redirects=True) as cx:
                with cx.stream("GET", url) as r:
                    r.raise_for_status()
                    with open(dest + ".part", "wb") as f:
                        for chunk in r.iter_bytes(1 << 16):
                            f.write(chunk)
            os.replace(dest + ".part", dest)
        self._minilm = None
        return {"ok": True, "detail": "MiniLM downloaded; enable it in Settings"}

    # ---------------------------------------------------------------- public
    def embed_texts(self, texts):
        """Returns list of normalized vectors, or None if unavailable."""
        if self.backend == "minilm" and self._minilm:
            try:
                return self._embed_minilm(texts)
            except Exception as e:
                log.warning("MiniLM embed failed, falling back: %s", e)
                self.backend, self.detail = "keyword", "keyword (BM25 fallback)"
                return None
        return None  # runtime embeddings are handled by the store via the runtime

    def _embed_minilm(self, texts):
        import numpy as np
        session, tok = self._minilm
        out = []
        for t in texts:
            enc = tok.encode(t[:2000]).encodings[0]
            ids = np.array([enc.ids], dtype="int64")
            mask = np.array([enc.attention_mask], dtype="int64")
            feed = {"input_ids": ids, "attention_mask": mask}
            try:
                feed["token_type_ids"] = np.zeros_like(ids)
            except Exception:
                pass
            res = session.run(None, feed)[0][0]
            m = res[mask[0] == 1].sum(axis=0)
            count = max(1, int(mask[0].sum()))
            out.append(normalize((m / count).tolist()))
        return out


SERVICE = EmbeddingService()
