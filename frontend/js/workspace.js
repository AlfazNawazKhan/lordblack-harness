/* Workspace: chat, file access tree, memory retrieval, AI activity, raw log. */
(() => {
  const { el, esc, icon, toast, modal, confirmModal, fmtTime } = UI;

  const S = {
    messages: [],
    rag: true, agentic: false, duet: false,
    fileContext: new Set(),
    tracing: true,
    memoryChunks: [],
    activeAgent: null,
    expanded: {},       // tree path -> loaded
    controller: null,
  };
  window.Workspace = S;

  const $ = (id) => document.getElementById(id);
  const thread = () => $("chat-thread");

  /* ============================== chat ============================== */

  function addMessage(role, content, who) {
    const m = el(`<div class="msg ${role}">
      <div class="mavatar">${role === "user" ? "YOU" : "AI"}</div>
      <div class="bubble"><div class="who">${esc(who || (role === "user" ? "You" : "Local model"))}</div>
      <div class="mcontent"></div></div></div>`);
    m.querySelector(".mcontent").innerHTML = content ? MD.render(content) : "";
    thread().appendChild(m);
    thread().scrollTop = thread().scrollHeight;
    MD.wire(m, { onApply: applyToFile, onDiff: showDiff, onSave: saveAsFile });
    return m;
  }

  function updateModelChips() {
    const cfg = App.state.config;
    const chips = $("model-chips");
    if (!cfg) return;
    const a = cfg.active || {};
    let html = "";
    if (a.model) {
      const rt = (cfg.runtimes || []).find((r) => r.id === a.runtime_id);
      html += `<span class="tag cyan" data-switch style="cursor:pointer" title="Click to switch model">${esc(a.model)} (Local${rt ? " · " + esc(rt.name) : ""})</span>`;
    } else html += `<span class="tag amber">no local model</span>`;
    // external chips only appear when the user explicitly allowed the provider
    // in Settings → Model Mapping & Nodes (node_toggles), never just for storing a key
    for (const [pid, p] of Object.entries(cfg.cloud || {})) {
      if (p.enabled && p.key_set && cfg.node_toggles?.[pid] === true)
        html += `<span class="tag purple">${esc(pid)} (External)</span>`;
    }
    if (S.duet && a.secondary && a.secondary.model)
      html += `<span class="tag green">◎ ${esc(a.secondary.model)}</span>`;
    chips.innerHTML = html;
    const sw = chips.querySelector("[data-switch]");
    if (sw) sw.onclick = openModelSwitcher;
  }
  S.updateModelChips = updateModelChips;

  /* quick model switcher from the workspace header chip */
  async function openModelSwitcher() {
    let groups = [];
    try { groups = await API.get("/api/models"); } catch (e) { return toast(e.message, "error"); }
    const active = App.state.config?.active || {};
    const m = modal({ title: "Switch active model",
      body: groups.map((g) => `<div class="field"><label>${esc(g.runtime_name)}</label>
        <div class="model-grid">${(g.models || []).map((mm) =>
          `<span class="model-pill ${active.runtime_id === g.runtime_id && active.model === mm ? "active" : ""}"
                 data-rt="${esc(g.runtime_id)}" data-m="${esc(mm)}">${esc(mm)}</span>`).join("") ||
          `<span class="faint small">no models detected</span>`}</div></div>`).join("") ||
        `<div class="empty">No runtimes connected.</div>` });
    m.querySelectorAll(".model-pill").forEach((p) => {
      p.onclick = async () => {
        await API.post(`/api/runtimes/${p.dataset.rt}/set-active`, { model: p.dataset.m });
        await App.refreshConfig();
        toast("Active model: " + p.dataset.m);
        m.remove();
      };
    });
  }

  /* ---------- chat persistence (localStorage, survives refresh) ---------- */
  function saveChat() {
    try { localStorage.setItem("lbh-chat", JSON.stringify(S.messages.slice(-60))); } catch (e) {}
  }
  function loadChat() {
    try {
      const raw = localStorage.getItem("lbh-chat");
      if (!raw) return;
      S.messages = JSON.parse(raw) || [];
      // WorkspaceBoot runs on every visit — clear the thread first so the
      // history isn't appended twice
      const th = thread();
      th.innerHTML = "";
      const w = $("chat-welcome");
      if (w) w.remove();
      if (!S.messages.length) return;
      for (const msg of S.messages.slice(-30))
        addMessage(msg.role === "user" ? "user" : "assistant", msg.content);
    } catch (e) {}
  }
  function clearChat() {
    if (S.controller) { S.controller.abort(); S.controller = null; $("send-btn").disabled = false; }
    S.messages = [];
    S.memoryChunks = [];
    localStorage.removeItem("lbh-chat");
    thread().innerHTML = "";
    thread().appendChild(UI.el(`<div class="empty" id="chat-welcome">
      <svg><use href="#i-zap"/></svg><b>LordBlack Harness</b><br>New conversation started.
      Your memory notes stay untouched — only this chat thread is cleared.</div>`));
    // fresh run trace + memory retrieval for the new conversation
    runTrace.length = 0;
    $("run-trace").style.display = "none";
    $("rt-count").textContent = "";
    $("rt-body").innerHTML = "";
    renderMemRetrievalFromChunks();
    updateTokenCount();
    $("chat-input").focus();
  }
  window.WorkspaceClearChat = clearChat;
  $("btn-new-chat").onclick = clearChat;

  async function send() {
    const input = $("chat-input");
    const text = input.value.trim();
    if (!text || S.controller) return;
    input.value = ""; autoGrow(); updateTokenCount();
    $("chat-welcome") && $("chat-welcome").remove();

    S.messages.push({ role: "user", content: text });
    addMessage("user", text);
    saveChat();

    const aiMsg = addMessage("assistant", "");
    const mcontent = aiMsg.querySelector(".mcontent");
    mcontent.innerHTML = `<span class="cursor-blink"></span>`;
    let acc = "", lastRender = 0;
    const renderNow = () => {
      mcontent.innerHTML = MD.render(acc) + `<span class="cursor-blink"></span>`;
      thread().scrollTop = thread().scrollHeight;
    };

    $("send-btn").disabled = true;
    S.controller = new AbortController();
    const cfg = App.state.config || {};
    const body = {
      messages: S.messages,
      rag: S.rag, agentic: S.agentic,
      duet: { enabled: S.duet, runtime_id: cfg.active?.secondary?.runtime_id,
              model: cfg.active?.secondary?.model },
      file_context: [...S.fileContext],
    };
    const t0 = performance.now();
    let firstToken = true;

    await API.sse("/api/chat", body, {
      onEvent: (name, data) => {
        if (name === "token") {
          if (firstToken) {
            firstToken = false;
            $("latency").textContent = Math.round(performance.now() - t0) + " ms (TTFT)";
          }
          acc += data.t || "";
          const now = performance.now();
          if (now - lastRender > 90) { lastRender = now; renderNow(); }
        } else if (name === "trace") {
          Activity.add(data);
          if (data.phase === "memory") renderMemRetrieval(data);
          addRunTrace(data);
        } else if (name === "error") {
          acc = acc || "";
          mcontent.innerHTML = MD.render(acc) +
            `<p style="color:var(--red);margin-top:8px">${esc(data.message)}</p>`;
        } else if (name === "done") {
          if (data.answered_by) aiMsg.querySelector(".who").textContent = "Answered by " + data.answered_by;
          if (data.memory_chunks) S.memoryChunks = data.memory_chunks;
          renderMemRetrievalFromChunks();
          saveChat();
        }
      },
      onError: (e) => {
        mcontent.innerHTML = MD.render(acc) +
          `<p style="color:var(--red);margin-top:8px">${esc(e.message)}</p>`;
      },
      onDone: () => {},
    }, S.controller.signal);

    if (acc) {
      mcontent.innerHTML = MD.render(acc);
      // final render replaced the DOM — wire copy/apply/diff buttons again
      MD.wire(aiMsg, { onApply: applyToFile, onDiff: showDiff, onSave: saveAsFile });
    } else if (firstToken) mcontent.innerHTML = `<span class="faint">(empty response)</span>`;
    S.messages.push({ role: "assistant", content: acc });
    $("send-btn").disabled = false;
    S.controller = null;
    $("latency").textContent = Math.round(performance.now() - t0) + " ms";
  }

  function renderMemRetrievalFromChunks() {
    const list = $("memretr-list");
    if (!S.memoryChunks.length) {
      list.innerHTML = `<div class="empty">No memory was used in the last turn.</div>`;
      return;
    }
    list.innerHTML = S.memoryChunks.map((c) => {
      const src = c.heading && !c.doc_title.startsWith(c.heading)
        ? `${c.doc_title} › ${c.heading}` : c.doc_title;
      return `<div class="memretr-item"><span class="src">${esc(src)}</span>${esc(c.text.slice(0, 160))}${c.text.length > 160 ? "…" : ""}</div>`;
    }).join("");
  }

  function renderMemRetrieval(traceEvt) {
    // quick visual while streaming; final content comes from done event
    const list = $("memretr-list");
    if (traceEvt.detail && traceEvt.detail !== "no strong matches")
      list.innerHTML = `<div class="memretr-item"><span class="src">${esc(traceEvt.title)}</span>${esc(traceEvt.detail)}</div>`;
  }

  /* ---- attach files ---- */
  $("attach-btn").onclick = () => $("attach-input").click();
  $("attach-input").onchange = async (e) => {
    for (const f of e.target.files) {
      const text = await f.text();
      S.messages.push({ role: "user", content: `[Attached file: ${f.name}]\n${text.slice(0, 100000)}` });
      addMessage("user", `**Attached:** ${esc(f.name)}\n\n\`\`\`\n${text.slice(0, 800)}${text.length > 800 ? "\n…" : ""}\n\`\`\``);
    }
    e.target.value = "";
  };

  /* ============================== run trace (center) ============================== */
  const runTrace = [];

  function addRunTrace(evt) {
    runTrace.push(evt);
    $("run-trace").style.display = "";
    $("rt-count").textContent = runTrace.length + " steps";
    $("rt-body").innerHTML = runTrace.map(traceRow).join("");
    $("rt-body").scrollTop = $("rt-body").scrollHeight;
  }
  function traceRow(t) {
    const time = new Date(t.ts * 1000).toLocaleTimeString([], { hour12: false });
    return `<div class="tr-row"><span class="t-time">${time}</span>
      <span class="t-status ${t.status}">${t.status === "ok" ? "●" : t.status === "warn" ? "▲" : "✕"}</span>
      <span class="t-title">${esc(t.title)}</span>
      <span class="t-detail">${esc(t.detail || "")}</span></div>`;
  }
  $("rt-toggle").onclick = () => {
    const b = $("rt-body");
    b.style.display = b.style.display === "none" ? "" : "none";
  };

  /* ============================== AI activity (right) ============================== */
  const Activity = {
    items: [],
    add(evt) {
      if (!S.tracing) return;
      this.items.push(evt);
      if (this.items.length > 200) this.items.shift();
      const list = $("activity-list");
      if (this.items.length === 1) list.innerHTML = "";
      const time = fmtTime(evt.ts);
      const item = el(`<div class="act-item">
        <div class="a-top"><span class="a-status ${evt.status}"></span>
          <span class="a-title">${esc(evt.title)}</span>
          ${evt.latency_ms != null ? `<span class="a-lat">${evt.latency_ms}ms</span>` : ""}</div>
        ${evt.detail ? `<div class="a-detail">${esc(evt.detail)}</div>` : ""}
        <div class="a-phase">${esc(evt.phase)} · ${time}</div></div>`);
      list.appendChild(item);
      list.scrollTop = list.scrollHeight;
    },
    clear() { this.items = []; $("activity-list").innerHTML =
      `<div class="empty">Pipeline steps will appear here:<br>parse → route → memory → external → extract → answer</div>`; },
  };
  window.Activity = Activity;
  $("act-clear").onclick = () => Activity.clear();
  $("act-pause").onclick = (e) => {
    S.tracing = !S.tracing;
    e.currentTarget.innerHTML = S.tracing ? icon("pause") : icon("play");
    e.currentTarget.title = S.tracing ? "Pause" : "Resume";
  };

  /* ============================== raw log ============================== */
  let logStreamStarted = false;
  function startLogStream() {
    if (logStreamStarted) return;
    logStreamStarted = true;
    const log = $("rawlog");
    let count = 0;
    const pump = () => API.sse("/api/logs/stream", undefined, {
      onEvent: (name, data) => {
        const line = (data.line || "").replace(/</g, "&lt;");
        const lv = line.includes("[WARNING]") ? "WARNING" : line.includes("[ERROR]") ? "ERROR" : "INFO";
        const div = document.createElement("div");
        div.className = "lv-" + lv;
        div.innerHTML = line;
        log.appendChild(div);
        if (++count > 400) { log.removeChild(log.firstChild); count--; }
        log.scrollTop = log.scrollHeight;
      },
      onError: () => setTimeout(pump, 3000),
    });
    pump();
  }
  window.startLogStream = startLogStream;
  $("rawlog-jump").onclick = () => { const l = $("rawlog"); l.scrollTop = l.scrollHeight; };

  /* ============================== file access tree ============================== */

  async function loadFolders() {
    const { folders } = await API.get("/api/fs/folders").catch(() => ({ folders: [] }));
    const g = $("folder-grants");
    g.innerHTML = folders.length ? "" :
      `<div class="faint small">No folder granted yet — the model sees nothing on disk until you grant access.</div>`;
    for (const f of folders) {
      const row = el(`<div class="row"><span class="tag cyan" style="max-width:70%;overflow:hidden;text-overflow:ellipsis">${esc(f)}</span>
        <button class="icon-btn" title="Revoke access">${icon("trash", 13)}</button></div>`);
      row.querySelector("button").onclick = async () => {
        if (await confirmModal("Revoke folder", `Revoke all access to <b>${esc(f)}</b>?`, "Revoke")) {
          await API.del("/api/fs/folders?path=" + encodeURIComponent(f));
          S.expanded = {}; S.fileContext.clear();
          loadFolders();
        }
      };
      g.appendChild(row);
    }
    const tree = $("file-tree");
    tree.innerHTML = "";
    for (const f of folders) {
      const node = el(`<div class="tree-node"></div>`);
      node.appendChild(folderRow(f, f));
      tree.appendChild(node);
      expandNode(node, f, f);
    }
  }

  function folderRow(name, path) {
    const r = el(`<div class="tree-row"><span class="ticon">${icon("folder", 14)}</span>
      <span class="tname">${esc(name)}</span></div>`);
    return r;
  }

  /* btoa throws on non-Latin1 paths (C:\Users\Ünal\…); use a codepoint hash */
  function pathId(p) {
    let h = 0;
    for (let i = 0; i < p.length; i++) h = (h * 31 + p.codePointAt(i)) >>> 0;
    return h.toString(36);
  }

  async function expandNode(container, path, label) {
    const childrenBox = el(`<div class="tree-children"></div>`);
    const loading = el(`<div class="tree-row"><span class="spinner">loading…</span></div>`);
    container.appendChild(loading);
    try {
      const { entries } = await API.get("/api/fs/tree?path=" + encodeURIComponent(path));
      loading.remove();
      childrenBox.innerHTML = "";
      for (const e of entries.slice(0, 300)) {
        if (e.type === "dir") {
          const sub = el(`<div class="tree-node"></div>`);
          const row = el(`<div class="tree-row"><span class="ticon">${icon("chev", 12)}</span>
            <span class="ticon">${icon("folder", 13)}</span>
            <span class="tname">${esc(e.name)}</span></div>`);
          row.onclick = async () => {
            if (sub.dataset.open) { sub.querySelector(".tree-children").remove(); sub.dataset.open = ""; return; }
            sub.dataset.open = "1";
            await expandNode(sub, e.path, e.name);
          };
          sub.appendChild(row);
          childrenBox.appendChild(sub);
        } else {
          const row = el(`<div class="tree-row"><span class="ticon">${icon("file", 13)}</span>
            <span class="tname">${esc(e.name)}</span>
            ${UI.toggleSwitch("ft-" + pathId(e.path), S.fileContext.has(e.path))}</div>`);
          row.querySelector("input").onchange = (ev) => {
            if (ev.target.checked) S.fileContext.add(e.path);
            else S.fileContext.delete(e.path);
            toast(ev.target.checked ? `${e.name} added to working context (${S.fileContext.size} files)` :
              `${e.name} removed (${S.fileContext.size})`);
          };
          row.querySelector(".tname").onclick = () => previewFile(e.path);
          childrenBox.appendChild(row);
        }
      }
    } catch (err) {
      loading.innerHTML = `<span class="faint">unavailable: ${esc(err.message)}</span>`;
    }
    container.appendChild(childrenBox);
  }

  async function previewFile(path) {
    try {
      const f = await API.get("/api/fs/file?path=" + encodeURIComponent(path));
      modal({ title: path, wide: true,
        body: `<pre class="mono" style="font-size:12px;white-space:pre-wrap;max-height:60vh;overflow:auto">${esc(f.content)}${f.truncated ? "\n\n… (truncated)" : ""}</pre>` });
    } catch (e) { toast(e.message, "error"); }
  }

  async function resolveTargetPath(file) {
    // resolve relative paths against a granted folder; ask when ambiguous
    const folders = (await API.get("/api/fs/folders")).folders;
    if (file.match(/^[A-Za-z]:/) || file.startsWith("/")) return file;
    if (!folders.length) throw new Error("No granted folder to resolve '" + file + "'");
    if (folders.length === 1) return folders[0].replace(/[\\/]+$/, "") + "/" + file;
    return new Promise((resolve, reject) => {
      const m = modal({ title: "Choose target folder",
        body: `<p class="muted small" style="margin-bottom:10px">Where should <b>${esc(file)}</b> be written?</p>` +
          folders.map((f) => `<div class="free-model-item" data-f="${esc(f)}">${icon("folder", 14)} ${esc(f)}</div>`).join(""),
        footer: `<button class="btn" data-close>Cancel</button>` });
      m.querySelectorAll("[data-f]").forEach((o) => {
        o.onclick = () => { m.remove(); resolve(o.dataset.f.replace(/[\\/]+$/, "") + "/" + file); };
      });
      m.querySelector("[data-close]").onclick = () => reject(new Error("cancelled"));
    });
  }

  async function applyToFile(file, code) {
    if (!file) return;
    let path;
    try { path = await resolveTargetPath(file); } catch (e) { return; }
    if (!(await confirmModal("Apply to file",
        `Write this content to <b>${esc(path)}</b>? A .lbk.bak backup is kept.`, "Apply"))) return;
    try {
      const r = await API.post("/api/fs/apply", { path, content: code });
      toast(`Applied to ${r.path}${r.backup ? " (backup: " + r.backup + ")" : ""}`);
    } catch (e) { toast(e.message, "error"); }
  }

  /* Save-as-file: for blocks tagged file="…" it behaves like Apply; for
     untagged blocks (small models often skip the tag) ask for a name first */
  async function saveAsFile(file, code) {
    let name = file;
    if (!name) {
      name = await new Promise((resolve) => {
        const m = modal({ title: "Save code as file",
          body: `<div class="field"><label>FILE NAME</label>
            <input id="save-name" placeholder="main.py" style="width:100%">
            <span class="hint">Saved inside a granted folder. A .lbk.bak backup is kept on overwrite.</span></div>`,
          footer: `<button class="btn" data-close>Cancel</button>
                   <button class="btn primary" id="save-ok">Save</button>` });
        const inp = m.querySelector("#save-name");
        inp.focus();
        m.querySelector("#save-ok").onclick = () => { const v = inp.value.trim(); m.remove(); resolve(v); };
        m.querySelector("[data-close]").onclick = () => resolve(null);
      });
    }
    if (!name) return;
    return applyToFile(name, code);
  }

  async function showDiff(file, code) {
    try {
      const path = await resolveTargetPath(file);
      const { diff } = await API.post("/api/fs/diff", { path, content: code });
      modal({ title: "Diff — " + file, wide: true,
        body: `<pre class="mono" style="font-size:11.5px;white-space:pre-wrap;max-height:60vh;overflow:auto;color:${diff.length ? "var(--muted)" : "var(--faint)"}">${esc(diff.join("\n") || "No changes — file already matches.")}</pre>` });
    } catch (e) { toast(e.message, "error"); }
  }

  $("btn-add-folder").onclick = async () => {
    const m = modal({ title: "Grant folder access",
      body: `<div class="field"><label>FOLDER PATH</label>
        <input id="grant-path" placeholder="C:\\Users\\you\\Projects\\my-repo" style="width:100%">
        <span class="hint">Only this folder (and its subfolders) can ever be read. You can revoke it any time.</span></div>`,
      footer: `<button class="btn" data-close>Cancel</button><button class="btn primary" id="grant-ok">Grant</button>` });
    m.querySelector("#grant-ok").onclick = async () => {
      const path = m.querySelector("#grant-path").value.trim();
      if (!path) return;
      try { await API.post("/api/fs/folders", { path }); m.remove(); loadFolders(); }
      catch (e) { toast(e.message, "error"); }
    };
  };

  $("btn-pick-folder").onclick = async () => {
    try {
      const r = await API.post("/api/fs/folders", { pick: true });
      if (r && r.path) { loadFolders(); toast("Granted: " + r.path); }
    } catch (e) { toast("Native picker unavailable — use + to paste a path (" + e.message + ")", "warn"); }
  };

  /* ============================== input ============================== */

  function autoGrow() {
    const i = $("chat-input");
    i.style.height = "auto";
    i.style.height = Math.min(i.scrollHeight, 140) + "px";
  }
  function estTokens() {
    const text = $("chat-input").value;
    const ctx = [...S.fileContext].length * 500 + S.messages.reduce((a, m) => a + (m.content || "").length, 0);
    return Math.ceil((text.length + ctx) / 4);
  }
  function updateTokenCount() { $("token-count").textContent = estTokens() + " tokens"; }
  $("chat-input").addEventListener("input", () => { autoGrow(); updateTokenCount(); });
  $("chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });
  $("send-btn").onclick = send;

  function bindModeChip(id, key) {
    $(id).onclick = () => {
      S[key] = !S[key];
      $(id).classList.toggle("on", S[key]);
      if (key === "duet" && S.duet) updateModelChips();
      if (key === "duet") $(id).classList.toggle("greenmode", S[key]);
      if (key === "agentic") $(id).classList.toggle("ambermode", S[key]);
    };
  }
  bindModeChip("rag-chip", "rag");
  bindModeChip("agentic-chip", "agentic");
  bindModeChip("duet-chip", "duet");

  /* ============================== boot ============================== */
  let traceLoaded = false;
  window.WorkspaceBoot = async () => {
    loadChat();
    updateModelChips();
    await loadFolders();
    try {
      const st = await API.get("/api/memory/status");
      $("mem-mode-tag").textContent = st.backend + " · " + st.chunks + " chunks";
    } catch (e) { $("mem-mode-tag").textContent = "offline"; }
    startLogStream();
    recentTrace();
  };

  async function recentTrace() {
    if (traceLoaded) return; // boot runs on every workspace visit — load once
    traceLoaded = true;
    try {
      const { trace } = await API.get("/api/trace/recent");
      trace.slice(-40).forEach((t) => Activity.add(t));
    } catch (e) {}
  }
})();
