"""Tiny mock LM Studio (OpenAI-compatible, SSE streaming) for E2E testing."""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v1/models":
            self._json({"data": [{"id": "mock-qwen-3b"}]})
        else:
            self._json({}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/v1/embeddings":
            self._json({"error": {"message": "no embedding model loaded"}}, 400)
            return
        if self.path != "/v1/chat/completions":
            self._json({}, 404)
            return
        msgs = body.get("messages", [])
        sys = " ".join(m["content"] for m in msgs if m["role"] == "system")
        user = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
        # routing probe -> decline external help
        if "routing module" in sys:
            reply = '{"need_external": false, "sub_task": null, "reason": "simple question"}'
        elif "extract information" in sys.lower():
            reply = "Python, FastAPI, SQLite, plain JS. MiniLM or BM25."
        elif "Project Stack" in sys:
            reply = ("Based on my memory: **the harness is built with Python, FastAPI, "
                     "SQLite and plain JS**, using MiniLM or BM25 keyword fallback for "
                     "embeddings on low-end devices.")
        else:
            reply = (f'Echo ok. Here is a sample file proposal:\n\n'
                     f'```python file="demo/hello.py"\nprint("hello from mock")\n```\n'
                     f'(reply to: {user[:60]})')
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for i in range(0, len(reply), 12):
            chunk = {"choices": [{"delta": {"content": reply[i:i + 12]}}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.004)
        end = {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 30,
                                                     "completion_tokens": len(reply) // 4}}
        self.wfile.write(f"data: {json.dumps(end)}\n\ndata: [DONE]\n\n".encode())


if __name__ == "__main__":
    print("mock LM Studio on :1234")
    ThreadingHTTPServer(("127.0.0.1", 1234), Handler).serve_forever()
