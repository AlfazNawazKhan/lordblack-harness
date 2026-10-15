/* API client: REST + fetch-based SSE streaming (works for POST too). */
window.API = (() => {
  const isFile = location.protocol === "file:";
  const BASE = isFile ? "http://127.0.0.1:8765" : "";

  async function req(method, url, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const r = await fetch(BASE + url, opts);
    if (!r.ok) {
      let msg = r.status + " " + r.statusText;
      try { msg = (await r.json()).detail || msg; } catch (e) {}
      throw new Error(msg);
    }
    return r.json();
  }

  async function upload(url, formData) {
    const r = await fetch(BASE + url, { method: "POST", body: formData });
    if (!r.ok) {
      let msg = r.status + " " + r.statusText;
      try { msg = (await r.json()).detail || msg; } catch (e) {}
      throw new Error(msg);
    }
    return r.json();
  }

  /* Stream SSE from a POST (or GET) endpoint.
     handlers: {onEvent(name, data), onError(err), onDone()} */
  async function sse(url, body, handlers, signal) {
    const opts = {
      method: body === undefined ? "GET" : "POST",
      headers: { "Accept": "text/event-stream" },
      signal,
    };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(BASE + url, opts);
    } catch (e) {
      handlers.onError && handlers.onError(new Error("Cannot reach harness backend at " + (BASE || location.origin)));
      return;
    }
    if (!res.ok || !res.body) {
      let msg = res.status;
      try { msg = (await res.json()).detail || msg; } catch (e) {}
      handlers.onError && handlers.onError(new Error(msg));
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          let name = "message", data = "";
          for (const line of block.split("\n")) {
            if (line.startsWith("event:")) name = line.slice(6).trim();
            else if (line.startsWith("data:")) data += line.slice(5).trim();
          }
          if (data) {
            try { handlers.onEvent && handlers.onEvent(name, JSON.parse(data)); }
            catch (e) { /* keep-alive or malformed line */ }
          }
        }
      }
      handlers.onDone && handlers.onDone();
    } catch (e) {
      if (e.name !== "AbortError") handlers.onError && handlers.onError(e);
    }
  }

  return {
    BASE, isFile,
    get: (u) => req("GET", u),
    post: (u, b) => req("POST", u, b),
    put: (u, b) => req("PUT", u, b),
    del: (u) => req("DELETE", u),
    upload, sse,
  };
})();
