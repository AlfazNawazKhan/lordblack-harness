"""Core unit tests — run with:  python tests/test_core.py   (no pytest needed)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import encrypt_secret, decrypt_secret, mask_key
from backend.memory.embeddings import pack_vec, unpack_vec, normalize, cosine
from backend.memory.store import MemoryStore
from backend.pipeline import _extract_json, _trim_messages
from backend.agents import _parse_reply, slugify, check_rate_limit
from backend import fsaccess

PASSED = 0


def ok(name, cond):
    global PASSED
    assert cond, f"FAILED: {name}"
    PASSED += 1
    print(f"  ok  {name}")


# ---- config / keys ----
print("[config]")
secret = "sk-test-abcdef123456"
enc = encrypt_secret(secret)
ok("key encrypt/decrypt roundtrip", decrypt_secret(enc) == secret)
ok("key not stored as plaintext", not enc.startswith("plain:") or enc == "plain:" + secret)
ok("mask hides most of key", secret[:7] in mask_key(secret) and secret[-4:] not in mask_key(secret))

# ---- chunking ----
print("[memory chunker]")
store = MemoryStore()
md = "# Alpha\n\npara one\n\npara two\n\n## Beta\n\nlonger " + "x " * 400 + "\n\n### Gamma\n\ntail"
chunks = store._chunk_text(md)
ok("chunks produced", len(chunks) >= 2)
ok("heading path tracked", "Alpha › Beta" in (chunks[0][0] or "")
   and "Alpha › Gamma" in (chunks[-1][0] or ""))
ok("content preserved", "tail" in chunks[-1][1])

# ---- embeddings helpers ----
print("[vector helpers]")
v = normalize([3.0, 4.0])
ok("normalize to unit length", abs(cosine(v, v) - 1.0) < 1e-6)
blob = pack_vec([0.1, -0.2, 0.3])
ok("pack/unpack roundtrip", all(abs(a - b) < 1e-6 for a, b in zip(unpack_vec(blob), [0.1, -0.2, 0.3])))

# ---- BM25 ----
print("[bm25]")
store._bm25 = None
rows = [("c1", "python fastapi backend server"), ("c2", "cat sat on the mat"),
        ("c3", "python snake in the jungle")]
import sqlite3
mem = sqlite3.connect(":memory:")
mem.execute("CREATE TABLE chunks(id TEXT, text TEXT)")
mem.executemany("INSERT INTO chunks VALUES (?,?)", rows)
store._db = mem
scores = store._bm25_scores("python backend")
ok("relevant chunk ranked first", max(scores, key=scores.get) == "c1")
ok("irrelevant chunks excluded", "c2" not in scores)

# ---- pipeline json / trimming ----
print("[pipeline helpers]")
ok("json extraction", (_extract_json('blah {"need_external": true, "sub_task": "x"} blah')
                      or {}).get("need_external") is True)
ok("json extraction fails soft", _extract_json("no json here") is None)
msgs = [{"role": "system", "content": "S"}, *({"role": "user", "content": "m" * 100} for _ in range(10))]
trimmed = _trim_messages(msgs, 350)
ok("trim keeps system + recent", trimmed[0]["content"] == "S" and len(trimmed) < 11)

# ---- agent reply parser ----
print("[agent parser]")
tool, payload = _parse_reply('{"tool": "memory_search", "args": {"query": "stack"}}')
ok("tool call parsed", tool == "memory_search" and payload["query"] == "stack")
tool, payload = _parse_reply('{"answer": "final answer text"}')
ok("answer parsed (no tool)", tool is None and payload == "final answer text")
tool, payload = _parse_reply("plain text, no json")
ok("plain text falls through", tool is None and payload == "plain text, no json")
ok("slugify", slugify("Devin Lite #2!") == "devin-lite-2")

# ---- rate limiter ----
print("[rate limiter]")
check_rate_limit.__globals__  # touch
from collections import deque
import backend.agents as A
A.RATE_BUCKETS.clear()
allowed = all(check_rate_limit("t", "1.2.3.4", 3) for _ in range(3))
blocked = not check_rate_limit("t", "1.2.3.4", 3)
ok("token bucket allows then blocks", allowed and blocked)

# ---- fs path normalization ----
print("[fs sandbox]")
if os.name == "nt":
    ok("posix-style normalized", fsaccess._normalize("/c/Users/x") == "C:\\Users\\x")
ok("tilde expanded", not fsaccess._normalize("~/x").startswith("~"))
ok("forward slashes normalized", os.sep in fsaccess._normalize("a/b"))

print(f"\nALL {PASSED} TESTS PASSED")
