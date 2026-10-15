"""Live system stats: CPU/RAM sampler pushing over SSE (no client polling)."""
import asyncio
import logging

log = logging.getLogger("system")

_state = {"cpu": 0, "ram_used": 0, "ram_total": 0, "harness_mb": 0, "vram": None}
_subscribers = set()


async def start_sampler():
    try:
        import psutil
    except Exception:
        log.warning("psutil unavailable — system stats disabled")
        return
    psutil.cpu_percent(interval=None)  # prime
    while True:
        try:
            vm = psutil.virtual_memory()
            proc = psutil.Process()
            _state.update({
                "cpu": round(psutil.cpu_percent(interval=None), 1),
                "ram_used": round((vm.total - vm.available) / (1 << 30), 2),
                "ram_total": round(vm.total / (1 << 30), 2),
                "harness_mb": round(proc.memory_info().rss / (1 << 20), 1),
                "vram": _vram_percent(),
            })
            dead = []
            for q in _subscribers:
                try:
                    q.put_nowait(dict(_state))
                except asyncio.QueueFull:
                    # stats are lossy by design: drop the oldest sample and
                    # keep the subscriber alive so a slow client never hangs
                    # on a queue that is no longer being fed
                    try:
                        q.get_nowait()
                        q.put_nowait(dict(_state))
                    except Exception:
                        dead.append(q)
                except Exception:
                    dead.append(q)
            for q in dead:
                _subscribers.discard(q)
        except Exception:
            pass
        await asyncio.sleep(2.0)


def _vram_percent():
    try:
        import pynvml
        if not getattr(pynvml, "_inited", False):
            pynvml.nvmlInit()
            pynvml._inited = True
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        return round(mem.used / mem.total * 100, 1)
    except Exception:
        return None


def subscribe() -> asyncio.Queue:
    q = asyncio.Queue(maxsize=10)
    q.put_nowait(dict(_state))
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue):
    _subscribers.discard(q)


def snapshot() -> dict:
    return dict(_state)
