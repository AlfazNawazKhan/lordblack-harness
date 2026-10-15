"""Harness Memory: SQLite-backed personal knowledge store.

Documents live as plain .md/.txt files in data/memory/ (user-editable source of
truth); the DB holds chunked, embedded copies for retrieval. Search combines
vector similarity with pure-Python BM25 (RRF fusion). Sized for thousands of
chunks on 4 GB RAM: no heavy vector database, optional numpy fast path.
"""
import asyncio
import hashlib
import logging
import math
import os
import re
import sqlite3
import time
import uuid
from collections import Counter

from ..config import get_config, DATA_DIR
from .embeddings import SERVICE, pack_vec, unpack_vec, cosine

log = logging.getLogger("memory")
MEM_DIR = os.path.join(DATA_DIR, "memory")
DB_PATH = os.path.join(DATA_DIR, "harness.db")

try:
    import numpy as _np
except Exception:
    _np = None


def now() -> float:
    return time.time()


class MemoryStore:
    def __init__(self):
        self._db = None
        self._vec_cache = {}    # chunk_id -> list[float] (only when embeddings exist)
        self._bm25 = None       # (df Counter, avgdl, n, doc_len dict, chunk order)

    # ------------------------------------------------------------------ db
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            os.makedirs(DATA_DIR, exist_ok=True)
            self._db = sqlite3.connect(DB_PATH, check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("""CREATE TABLE IF NOT EXISTS documents(
                id TEXT PRIMARY KEY, title TEXT, path TEXT,
                content TEXT, created REAL, updated REAL)""")
            self._db.execute("""CREATE TABLE IF NOT EXISTS chunks(
                id TEXT PRIMARY KEY, doc_id TEXT, ordinal INTEGER, heading TEXT,
                text TEXT, embedding BLOB, tokens INTEGER)""")
            self._db.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id)")
            self._db.commit()
        return self._db

    # ------------------------------------------------------------------ chunking
    def _chunk_text(self, text: str):
        """Split markdown into chunks of ~chunk_chars, keeping heading path."""
        chunk_chars = int(get_config().get("general.chunk_chars", 600))
        chunks = []
        heading = ""
        buf, buf_head = [], ""
        size = 0

        def flush():
            nonlocal buf, size
            body = "\n".join(buf).strip()
            if body:
                chunks.append((buf_head, body))
            buf, size = [], 0

        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if not para:
                continue
            m = re.match(r"^(#{1,4})\s+(.*)$", para.splitlines()[0])
            if m:
                level = len(m.group(1))
                title = m.group(2).strip()
                if level == 1:
                    heading = title
                else:
                    heading = (heading.split(" › ")[0] + " › " + title) if heading else title
                buf_head = heading
            buf.append(para)
            size += len(para)
            if size >= chunk_chars:
                flush()
        flush()
        return chunks

    # ------------------------------------------------------------------ indexing
    async def index_document(self, doc_id: str, title: str, content: str,
                             rel_path: str = "") -> dict:
        db = self.db()
        cfg = get_config()
        db.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        pieces = self._chunk_text(content)
        vectors = await self._embed_pieces([p[1] for p in pieces])
        rows = []
        for i, (heading, body) in enumerate(pieces):
            emb = pack_vec(vectors[i]) if vectors else None
            rows.append((f"{doc_id}:{i}", doc_id, i, heading, body, emb, len(body) // 4))
        db.executemany("INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?,?,?)", rows)
        ts = now()
        prev = db.execute("SELECT created FROM documents WHERE id=?", (doc_id,)).fetchone()
        created = prev[0] if prev else ts
        db.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?)",
                   (doc_id, title, rel_path, content, created, ts))
        db.commit()
        self._invalidate()
        log.info("indexed '%s': %d chunks, embeddings=%s", title, len(pieces),
                 "yes" if vectors else "no")
        return {"chunks": len(pieces), "embedded": vectors is not None}

    async def _embed_pieces(self, texts):
        """Embed via runtime first; fall back to MiniLM; else None (keyword)."""
        if not texts:
            return None
        svc = SERVICE
        if svc.backend == "minilm" and svc.minilm_downloaded():
            vecs = svc.embed_texts(texts)
            if vecs:
                return vecs
        from ..providers import active_runtime
        rt = active_runtime()
        if rt and svc.backend in ("runtime", "auto"):
            try:
                # any OpenAI-compatible runtime can host an embedding model
                # (LM Studio: nomic-embed-text, Ollama: pulled embed models)
                models = await rt.list_models()
                embed_model = next((m for m in models if "embed" in m.lower()), None)
                if rt.type == "ollama" and not embed_model:
                    return None  # no embedding model pulled in Ollama
                raw = await rt.embed(texts, embed_model)
                return [normalize_local(v) for v in raw]
            except Exception as e:
                log.info("runtime embeddings unavailable (%s) — using keyword search", str(e)[:120])
                svc.backend, svc.detail = "keyword", "keyword (BM25 fallback)"
        if svc.backend == "minilm":
            vecs = svc.embed_texts(texts)
            if vecs:
                return vecs
        return None

    # ------------------------------------------------------------------ docs CRUD
    def list_documents(self):
        cur = self.db().execute(
            """SELECT d.id, d.title, d.path, d.updated,
                      (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=d.id) AS n_chunks,
                      LENGTH(d.content)
               FROM documents d ORDER BY d.updated DESC""")
        return [dict(id=r[0], title=r[1], path=r[2], updated=r[3], chunks=r[4],
                     size=r[5]) for r in cur.fetchall()]

    def get_document(self, doc_id: str):
        r = self.db().execute("SELECT id, title, path, content, created, updated "
                              "FROM documents WHERE id=?", (doc_id,)).fetchone()
        return dict(id=r[0], title=r[1], path=r[2], content=r[3], created=r[4],
                    updated=r[5]) if r else None

    def slugify(self, title: str) -> str:
        slug = re.sub(r"[^\w\- ]", "", title).strip().replace(" ", "-").lower() or "note"
        return slug[:60]

    def file_path_for(self, doc_id: str, title: str) -> str:
        return os.path.join(MEM_DIR, self.slugify(title) + f"-{doc_id[:6]}.md")

    def write_doc_file(self, doc_id: str, title: str, content: str):
        os.makedirs(MEM_DIR, exist_ok=True)
        old = self.get_document(doc_id)
        if old and old.get("path") and os.path.exists(old["path"]) \
                and old["path"] != self.file_path_for(doc_id, title):
            try:
                os.remove(old["path"])
            except OSError:
                pass
        path = self.file_path_for(doc_id, title)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def create_document(self, title: str, content: str) -> dict:
        doc_id = uuid.uuid4().hex[:12]
        path = self.write_doc_file(doc_id, title, content)
        asyncio.get_running_loop().create_task(
            self.index_document(doc_id, title, content, path))
        return {"id": doc_id, "title": title, "path": path}

    def update_document(self, doc_id: str, title: str, content: str) -> bool:
        if not self.get_document(doc_id):
            return False
        path = self.write_doc_file(doc_id, title, content)
        asyncio.get_running_loop().create_task(
            self.index_document(doc_id, title, content, path))
        return True

    def delete_document(self, doc_id: str) -> bool:
        db = self.db()
        old = self.get_document(doc_id)
        if not old:
            return False
        db.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        db.commit()
        if old.get("path") and os.path.exists(old["path"]):
            try:
                os.remove(old["path"])
            except OSError:
                pass
        self._invalidate()
        return True

    def reindex_all(self) -> int:
        db = self.db()
        docs = self.list_documents()
        n = 0
        for d in docs:
            full = self.get_document(d["id"])
            if full:
                self.index_document_sync(full["id"], full["title"], full["content"], full["path"])
                n += 1
        return n

    def index_document_sync(self, doc_id, title, content, rel_path=""):
        """Sync wrapper used from non-async contexts."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.index_document(doc_id, title, content, rel_path))
        except RuntimeError:
            asyncio.run(self.index_document(doc_id, title, content, rel_path))

    # ------------------------------------------------------------------ BM25
    _token_re = re.compile(r"[a-z0-9]+")

    @staticmethod
    def _tokens(text: str):
        return MemoryStore._token_re.findall(text.lower())

    def _invalidate(self):
        self._bm25 = None
        self._vec_cache = {}

    def _build_bm25(self):
        rows = self.db().execute("SELECT id, text FROM chunks").fetchall()
        df = Counter()
        doc_len, ids, docs_tokens = {}, [], {}
        for cid, text in rows:
            toks = self._tokens(text)
            docs_tokens[cid] = Counter(toks)
            ids.append(cid)
            doc_len[cid] = len(toks)
            for t in set(toks):
                df[t] += 1
        n = max(1, len(ids))
        avgdl = (sum(doc_len.values()) / n) if n else 0.0
        self._bm25 = (df, avgdl, n, doc_len, ids, docs_tokens)

    def _bm25_scores(self, query: str) -> dict:
        if self._bm25 is None:
            self._build_bm25()
        df, avgdl, n, doc_len, ids, docs_tokens = self._bm25
        k1, b = 1.5, 0.75
        scores = {}
        qtoks = self._tokens(query)
        if not qtoks:
            return {}
        for cid in ids:
            tf = docs_tokens[cid]
            s, dl = 0.0, doc_len[cid] or 1
            for t in qtoks:
                if tf[t]:
                    idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                    s += idf * (tf[t] * (k1 + 1)) / (tf[t] + k1 * (1 - b + b * dl / (avgdl or 1)))
            if s > 0:
                scores[cid] = s
        return scores

    def _load_vec(self, blob):
        if blob is None:
            return None
        return unpack_vec(blob)

    def _vec_scores(self, qvec) -> dict:
        db = self.db()
        if _np is not None:
            rows = [r for r in db.execute(
                "SELECT id, embedding FROM chunks WHERE embedding IS NOT NULL").fetchall()
                if len(unpack_vec(r[1])) == len(qvec)]  # skip stale vectors from another embed model
            if not rows:
                return {}
            mat = _np.array([unpack_vec(r[1]) for r in rows], dtype="float32")
            qv = _np.array(qvec, dtype="float32")
            sims = mat @ qv
            return {rows[i][0]: float(sims[i]) for i in range(len(rows))}
        scores = {}
        for cid, blob in db.execute("SELECT id, embedding FROM chunks WHERE embedding IS NOT NULL"):
            v = self._vec_cache.get(cid)
            if v is None:
                v = self._load_vec(blob)
                self._vec_cache[cid] = v
            if len(v) != len(qvec):
                continue  # stale vector from another embed model — never fuse it
            scores[cid] = cosine(qvec, v)
        return scores

    # ------------------------------------------------------------------ search
    async def search(self, query: str, k: int = None):
        """Hybrid retrieval. Returns list of chunk dicts with score + source."""
        k = k or int(get_config().get("general.memory_k", 4))
        db = self.db()
        n_chunks = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        if n_chunks == 0:
            return []

        qvec = None
        if SERVICE.backend == "runtime":
            from ..providers import active_runtime
            rt = active_runtime()
            if rt:
                try:
                    embed_model = None
                    if rt.type == "ollama":
                        models = await rt.list_models()
                        embed_model = next((m for m in models if "embed" in m.lower()), None)
                    if rt.type != "ollama" or embed_model:
                        raw = await rt.embed([query], embed_model)
                        qvec = normalize_local(raw[0])
                except Exception:
                    qvec = None
        elif SERVICE.backend == "minilm":
            vecs = SERVICE.embed_texts([query])
            if vecs:
                qvec = vecs[0]

        vec_scores = self._vec_scores(qvec) if qvec else {}
        bm_scores = self._bm25_scores(query)

        # Reciprocal Rank Fusion over available rankings
        def ranks(scores):
            ordered = sorted(scores.items(), key=lambda kv: -kv[1])
            return {cid: i + 1 for i, (cid, _) in enumerate(ordered)}

        r_vec, r_bm = ranks(vec_scores) if vec_scores else {}, ranks(bm_scores)
        all_ids = set(r_vec) | set(r_bm)
        fused = {cid: 1 / (60 + r_vec.get(cid, 10**9)) + 1 / (60 + r_bm.get(cid, 10**9))
                 for cid in all_ids}
        top = sorted(fused.items(), key=lambda kv: -kv[1])[:k]

        results = []
        for cid, score in top:
            r = db.execute("""SELECT c.doc_id, c.heading, c.text, d.title
                              FROM chunks c JOIN documents d ON d.id=c.doc_id
                              WHERE c.id=?""", (cid,)).fetchone()
            if r:
                results.append({"chunk_id": cid, "doc_id": r[0], "doc_title": r[3],
                                "heading": r[1], "text": r[2], "score": round(score, 6)})
        log.info("memory search %d chunks | mode=%s | %d results", n_chunks,
                 "hybrid" if qvec else "bm25", len(results))
        return results

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict:
        db = self.db()
        docs = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        embedded = db.execute(
            "SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()[0]
        size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
        return {"documents": docs, "chunks": chunks, "embedded_chunks": embedded,
                "db_bytes": size, "backend": SERVICE.backend, "backend_detail": SERVICE.detail}

    def wipe(self):
        db = self.db()
        db.execute("DELETE FROM chunks")
        db.execute("DELETE FROM documents")
        db.commit()
        self._invalidate()
        if os.path.isdir(MEM_DIR):
            for f in os.listdir(MEM_DIR):
                try:
                    os.remove(os.path.join(MEM_DIR, f))
                except OSError:
                    pass


def normalize_local(vec):
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


STORE = MemoryStore()
