"""Logging: rotating file, in-memory ring, and SSE broadcast for the UI."""
import asyncio
import logging
import logging.handlers
import os
import time
from collections import deque

from .config import DATA_DIR, get_config

LOG_DIR = os.path.join(DATA_DIR, "logs")
LOG_PATH = os.path.join(LOG_DIR, "harness.log")

_ring = deque(maxlen=1500)
_subscribers = set()          # asyncio.Queue per SSE log client
_loop = None                  # main event loop, captured at startup
_trace_ring = deque(maxlen=300)


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    level = getattr(logging, get_config().get("log_level", "INFO"), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
                            datefmt="%H:%M:%S")
    file_h = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=2_000_000,
                                                  backupCount=2, encoding="utf-8")
    file_h.setFormatter(fmt)
    root.addHandler(file_h)
    root.addHandler(RingHandler(fmt))


class RingHandler(logging.Handler):
    def __init__(self, fmt):
        super().__init__()
        self.setFormatter(fmt)

    def emit(self, record):
        try:
            line = self.format(record)
            _ring.append(line)
            if _loop and _subscribers:
                for q in list(_subscribers):
                    try:
                        _loop.call_soon_threadsafe(_put_nowait, q, line)
                    except RuntimeError:
                        pass
        except Exception:
            pass


def _put_nowait(q: asyncio.Queue, item):
    if not q.full():
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            pass


def capture_loop(loop: asyncio.AbstractEventLoop):
    global _loop
    _loop = loop


def subscribe_logs() -> asyncio.Queue:
    q = asyncio.Queue(maxsize=500)
    for line in list(_ring)[-100:]:
        _put_nowait(q, line)
    _subscribers.add(q)
    return q


def unsubscribe_logs(q: asyncio.Queue):
    _subscribers.discard(q)


def recent_logs(n: int = 300):
    return list(_ring)[-n:]


def emit_trace(phase: str, title: str, detail: str = "", status: str = "ok",
               latency_ms=None, **extra):
    """Record a pipeline trace step (AI Activity panel)."""
    evt = {"ts": time.time(), "phase": phase, "title": title, "detail": detail,
           "status": status, "latency_ms": latency_ms}
    evt.update(extra)
    _trace_ring.append(evt)
    logging.getLogger("trace").info("%s | %s %s %s",
                                    phase, title, detail, f"({latency_ms}ms)" if latency_ms else "")
    return evt


def recent_trace(n: int = 120):
    return list(_trace_ring)[-n:]
