/* Settings: General, Model & API Integrations (mock layout), Node Networks,
   System & Logs, Database, Security. */
(() => {
  const { esc, toast, icon, confirmModal } = UI;
  let tab = "general";

  async function render() {
    const body = document.getElementById("settings-body");
    const cfg = App.state.config;
    if (!cfg) { body.innerHTML = `<div class="empty">loading…</div>`; return; }
    if (tab === "general") await renderGeneral(body, cfg);
    else if (tab === "integrations") await renderIntegrations(body, cfg);
    else if (tab === "nodes") await renderNodes(body, cfg);
    else if (tab === "system") renderSystem(body, cfg);
    else if (tab === "database") await renderDatabase(body);
    else if (tab === "security") await renderSecurity(body, cfg);
  }

  async function save(patch, msg) {
    await API.post("/api/config", patch);
    await App.refreshConfig();
    if (msg) toast(msg);
    render();
  }

  /* ================================ GENERAL ================================ */
  async function renderGeneral(body, cfg) {
    const g = cfg.general;
    body.innerHTML = `
      <h2>General</h2><div class="sub">Core behavior of the harness.</div>
      <div class="settings-section">
        <div class="set-row"><div><div class="s-label">Internet access</div>
          <div class="s-desc">Master switch for everything that leaves the device. When off, all external calls are fully inert.</div></div>
          ${UI.toggleSwitch("g-internet", g.internet_enabled)}</div>
        <div class="set-row"><div><div class="s-label">Allow LAN access (UI + hosted agents)</div>
          <div class="s-desc">Binds 0.0.0.0 — other devices on your network can reach the harness. Only on trusted networks. Restart required.</div></div>
          ${UI.toggleSwitch("g-lan", g.lan_access)}</div>
        <div class="set-row"><div><div class="s-label">Open browser on start</div><div class="s-desc">Launcher opens the UI automatically.</div></div>
          ${UI.toggleSwitch("g-browser", g.open_browser_on_start)}</div>
        <div class="set-row"><div><div class="s-label">Bluetooth module</div>
          <div class="s-desc">Opt-in. Requires <span class="mono">pip install bleak</span>. Adds a bluetooth_scan tool for agents.</div></div>
          ${UI.toggleSwitch("g-bt", g.bluetooth_enabled)}</div>
      </div>
      <div class="settings-section"><h3>Memory & context budget</h3>
        <div class="set-row"><div><div class="s-label">Memory chunks per query</div><div class="s-desc">How many retrieved facts are injected per turn (low = fast on 4 GB machines).</div></div>
          <input type="number" min="1" max="12" id="g-memk" value="${g.memory_k}"></div>
        <div class="set-row"><div><div class="s-label">Chunk size (characters)</div><div class="s-desc">Target size of indexed memory chunks.</div></div>
          <input type="number" min="200" max="2000" step="50" id="g-chunk" value="${g.chunk_chars}"></div>
        <div class="set-row"><div><div class="s-label">Context char budget</div><div class="s-desc">Trim older conversation to keep prompts small.</div></div>
          <input type="number" min="1000" max="50000" step="500" id="g-ctx" value="${g.context_chars}"></div>
        <div class="set-row"><div><div class="s-label">Embedding backend</div>
          <div class="s-desc">Auto picks the lightest that works: runtime → local MiniLM → keyword (BM25). Current: <b>${esc((await API.get("/api/memory/status").catch(() => ({ backend_detail: "?" }))).backend_detail)}</b></div></div>
          <select id="g-emb">
            <option value="auto" ${g.embedding_backend === "auto" ? "selected" : ""}>Auto (by device capability)</option>
            <option value="runtime" ${g.embedding_backend === "runtime" ? "selected" : ""}>Runtime embeddings</option>
            <option value="minilm" ${g.embedding_backend === "minilm" ? "selected" : ""}>Local MiniLM (ONNX)</option>
            <option value="keyword" ${g.embedding_backend === "keyword" ? "selected" : ""}>Keyword only (lightest)</option>
          </select></div>
        <div class="set-row"><div><div class="s-label">Download MiniLM model (~90 MB, one-time)</div>
          <div class="s-desc">Better semantic memory search on weak CPUs. Needs internet once.</div></div>
          <button class="btn small" id="g-minilm">Download</button></div>
      </div>
      <div class="settings-section"><h3>Duet mode partner</h3>
        <div class="set-row"><div><div class="s-label">Secondary local model</div>
          <div class="s-desc">Used when "Duet Mode" is on: primary drafts, partner refines.</div></div>
          <select id="g-duet"><option value="">(none)</option></select></div>
      </div>`;

    document.getElementById("g-internet").onchange = (e) => save({ general: { internet_enabled: e.target.checked } },
      e.target.checked ? "Internet access ON — cloud calls possible" : "Internet access OFF — fully local");
    document.getElementById("g-lan").onchange = (e) => save({ general: { lan_access: e.target.checked } }, "Restart the harness to apply binding");
    document.getElementById("g-browser").onchange = (e) => save({ general: { open_browser_on_start: e.target.checked } });
    document.getElementById("g-bt").onchange = (e) => save({ general: { bluetooth_enabled: e.target.checked } });
    const saveNum = (id, path) => {
      const elx = document.getElementById(id);
      elx.onchange = () => {
        const p = {}; p[path] = +elx.value; save({ general: p }, "Saved");
      };
    };
    saveNum("g-memk", "memory_k"); saveNum("g-chunk", "chunk_chars"); saveNum("g-ctx", "context_chars");
    document.getElementById("g-emb").onchange = (e) => save({ general: { embedding_backend: e.target.value } }, "Embedding backend updated");
    document.getElementById("g-minilm").onclick = async () => {
      if (!cfg.internet_enabled) return toast("Enable internet access first", "warn");
      toast("Downloading MiniLM (~90 MB)…");
      try { const r = await API.post("/api/memory/embeddings/download-minilm"); toast(r.detail); }
      catch (e) { toast(e.message, "error"); }
    };

    // duet partner options
    const sel = document.getElementById("g-duet");
    try {
      for (const g2 of await API.get("/api/models"))
        for (const mm of g2.models) {
          const o = document.createElement("option");
          o.value = g2.runtime_id + "|" + mm;
          o.textContent = `${g2.runtime_name}: ${mm}`;
          if (cfg.active.secondary && cfg.active.secondary.runtime_id === g2.runtime_id &&
              cfg.active.secondary.model === mm) o.selected = true;
          sel.appendChild(o);
        }
    } catch (e) {}
    sel.onchange = () => {
      const [rid, m] = sel.value.split("|");
      save({ active: { ...(cfg.active || {}), secondary: { runtime_id: rid, model: m } } }, "Duet partner set");
    };
  }

  /* ============================ INTEGRATIONS ============================ */
  async function renderIntegrations(body, cfg) {
    let runtimes = [];
    try { runtimes = (await API.get("/api/runtimes")).runtimes; } catch (e) {}
    const primary = runtimes.find((r) => r.id === (cfg.active || {}).runtime_id) || runtimes[0];

    const providerRows = [];
    const icons = { openrouter: "cloud", anthropic: "cloud", openai: "cloud" };
    for (const [pid, conf] of Object.entries(cfg.cloud || {})) {
      if (!conf.key_set) continue;
      const allowed = cfg.node_toggles?.[pid] === true;
      providerRows.push(`<div class="map-row">
        <span class="micon">${icon(icons[pid] || "cloud")}</span>
        <div><div class="mname">${esc(pid[0].toUpperCase() + pid.slice(1))} API</div>
          <div class="mstat">key encrypted (${esc(conf.key_masked)}) · ${allowed ? "usable in chat" : "off until you toggle it on"}</div></div>
        <div class="grow"></div>
        <span class="tag ${allowed ? "green" : ""}">${allowed ? "ALLOWED" : "NOT ALLOWED"}</span>
        <span title="Allow this provider in chat/delegation">${UI.toggleSwitch("nt-" + pid, allowed)}</span>
      </div>`);
    }

    body.innerHTML = `
      <h2>Model &amp; API Integrations</h2>
      <div class="sub">Manage connections to your local hub, APIs, and model providers.</div>
      <div class="grid2">
        <div class="settings-section"><h3>LM Studio / Local Hub</h3>
          <div class="card">
            <h3 style="font-size:15px">Local Connection (Local Router)
              <span class="tag ${primary?.status?.ok ? "green" : "red"}" style="margin-left:auto">
                ${primary?.status?.ok ? `status: ${primary.status.latency_ms}ms ping` : "NOT CONNECTED"}</span></h3>
            <div class="desc">Paste the “Reachable at” URL from LM Studio’s Developer mode, then Connect.</div>
            <div class="url-row">
              <input id="int-url" class="mono" value="${esc(primary?.base_url || "http://127.0.0.1:1234")}">
              <span class="tag ${primary?.status?.ok ? "green" : "red"}" id="int-chip">${primary?.status?.ok ? "CONNECTED" : "OFFLINE"}</span>
            </div>
            <div class="row">
              ${UI.toggleSwitch("int-enable", primary ? primary.enabled : true)}
              <div class="grow"></div>
              <button class="btn" id="int-disc" ${primary ? "" : "disabled"}>Disconnect</button>
              <button class="btn primary" id="int-connect">Connect</button>
              <button class="btn" id="int-test">Test Connection</button>
            </div>
            <div class="status-line" id="int-status">${esc(primary?.status?.detail || "Paste your runtime URL and press Connect.")}</div>
            <div class="status-line" id="int-detect"></div>
          </div>
          <div class="card" style="margin-top:12px">
            <h3 style="font-size:15px">Model Mapping &amp; Nodes</h3>
            <div class="desc">Visual summary mapping of active mapping. Toggle what the harness may use.</div>
            <div class="map-row"><span class="micon">${icon("memory")}</span>
              <div><div class="mname">Memory</div><div class="mstat">SQLite · ${esc((await API.get("/api/memory/status").catch(() => ({ chunks: 0 }))).chunks)} chunks</div></div>
              <div class="grow"></div><span class="mstat">node</span>${UI.toggleSwitch("nm-memory", cfg.node_toggles?.memory !== false)}</div>
            ${runtimes.map((r) => (r.models || []).slice(0, 4).map((mm) => `
              <div class="map-row"><span class="micon">${icon("hub")}</span>
                <div><div class="mname">${esc(mm)}</div><div class="mstat">${esc(r.name)} · ${r.status?.ok ? r.status.latency_ms + "ms ping" : "offline"}</div></div>
                <div class="grow"></div>${UI.toggleSwitch("nm-" + mm.replace(/[^a-z0-9]/gi, "").slice(0, 12) || "model", (cfg.active || {}).model === mm)}</div>`).join("")).join("")}
            ${providerRows.join("")}
          </div>
        </div>
        <div class="settings-section"><h3>Cloud API Providers</h3>
          <div class="card">
            <h3 style="font-size:15px">API Connections (External Providers)
              <span class="tag ${cfg.internet_enabled ? "green" : "amber"}" style="margin-left:auto">
                ${cfg.internet_enabled ? "internet ON" : "internet OFF"}</span></h3>
            ${["anthropic", "openai", "openrouter"].map((pid) => {
              const conf = cfg.cloud[pid] || {};
              return `<div class="map-row" style="margin-top:8px">
                <span class="micon">${icon("cloud")}</span>
                <div><div class="mname">${esc(pid === "anthropic" ? "Anthropic Claude API" : pid === "openai" ? "OpenAI GPT API" : "OpenRouter API")}</div>
                  <div class="mstat">${conf.key_set ? esc(conf.key_masked) : "not connected"}</div></div>
                <div class="grow"></div>
                <span class="tag ${conf.enabled && conf.key_set ? "green" : ""}">${conf.enabled && conf.key_set ? "CONNECTED" : "NOT CONNECTED"}</span>
              </div>
              <div class="row" style="margin:-4px 0 8px;padding-left:42px">
                <input class="mono" data-key="${pid}" placeholder="Enter ${esc(pid)} API key…" style="flex:1;font-size:11.5px">
                <button class="btn small" data-connectkey="${pid}">Connect</button>
                <button class="btn small" data-verifykey="${pid}" ${conf.key_set ? "" : "disabled"}>Verify</button>
                <button class="btn small" data-models="${pid}" ${conf.key_set ? "" : "disabled"}>Pick model</button>
              </div>`;
            }).join("")}
            <div class="status-line" id="cloud-status"></div>
            <div class="privacy-box" style="margin-top:8px">Cloud models <b>cannot read your Harness Memory</b> — only locally-run
              models ever query it. Delegation sends just the sub-task text your local model composed.</div>
          </div>
          <div class="card" style="margin-top:12px">
            <h3 style="font-size:15px">Add another runtime</h3>
            <div class="desc">Ollama, llama.cpp server, or embedded llama.cpp.</div>
            <button class="btn" id="int-addrt">+ Add runtime</button>
          </div>
        </div>
      </div>`;

    document.getElementById("int-connect").onclick = async () => {
      const url = document.getElementById("int-url").value.trim().replace(/\/+$/, "");
      const status = document.getElementById("int-status");
      status.textContent = "connecting…";
      try {
        const existing = cfg.runtimes.find((r) => r.id === (primary?.id || "lmstudio"));
        if (existing) {
          await API.post("/api/config", { runtimes: cfg.runtimes.map((r) =>
            r.id === existing.id ? { ...r, base_url: url, enabled: true } : r) });
        } else {
          await API.post("/api/runtimes", { type: "lmstudio", name: "LM Studio", base_url: url });
        }
        await App.refreshConfig();
        // probe models, set active
        const list = await API.get("/api/runtimes");
        const me = list.runtimes.find((r) => r.base_url === url);
        if (me) {
          if (me.status.ok) {
            await API.post(`/api/runtimes/${me.id}/set-active`, { model: (me.models || [])[0] || "" });
            document.getElementById("int-detect").innerHTML =
              `<span style="color:var(--green)">Detection active: found ${(me.models || []).length} model(s)${(me.models || []).length ? ": " + me.models.slice(0, 3).map(esc).join(", ") : ""}</span>`;
            document.getElementById("int-chip").textContent = "CONNECTED";
            document.getElementById("int-chip").className = "tag green";
            await App.refreshConfig();
          } else {
            document.getElementById("int-detect").innerHTML =
              `<span style="color:var(--red)">${esc(me.status.detail)}</span>`;
          }
          status.textContent = me.status.detail;
        }
        toast("Connection attempted — see status");
        render();
      } catch (e) { status.textContent = e.message; }
    };
    document.getElementById("int-test").onclick = async () => {
      const status = document.getElementById("int-status");
      if (!primary) { status.textContent = "no runtime configured — connect one first"; return; }
      status.textContent = "testing…";
      try {
        const r = await API.post(`/api/runtimes/${primary.id}/test`);
        status.innerHTML = r.ok
          ? `<span style="color:var(--green)">reachable · ${r.latency_ms} ms · ${r.models.length} model(s)</span>` : esc(r.detail);
      } catch (e) { status.textContent = e.message; }
    };
    document.getElementById("int-disc").onclick = async () => {
      if (!primary) return;
      await API.post("/api/config", { runtimes: cfg.runtimes.map((r) =>
        r.id === primary.id ? { ...r, enabled: false } : r) });
      await App.refreshConfig(); toast("Disconnected"); render();
    };
    document.getElementById("int-enable").onchange = (e) =>
      save({ runtimes: cfg.runtimes.map((r) => r.id === primary.id ? { ...r, enabled: e.target.checked } : r) });
    document.getElementById("int-addrt").onclick = () => document.getElementById("btn-add-runtime").click();
    document.getElementById("nm-memory").onchange = (e) =>
      save({ node_toggles: { ...(cfg.node_toggles || {}), memory: e.target.checked } }, "Memory node " + (e.target.checked ? "enabled" : "disabled"));
    ["openrouter", "anthropic", "openai"].forEach((pid) => {
      const t = document.getElementById("nt-" + pid);
      if (t) t.onchange = (e) =>
        save({ node_toggles: { ...(cfg.node_toggles || {}), [pid]: e.target.checked } },
          e.target.checked ? `${pid} allowed in chat` : `${pid} hidden from chat`);
    });
    body.querySelectorAll("[data-connectkey]").forEach((b) => {
      b.onclick = async () => {
        const pid = b.dataset.connectkey;
        const key = body.querySelector(`[data-key="${pid}"]`).value.trim();
        if (!key) return toast("Paste the key first", "warn");
        await API.post(`/api/config/keys/${pid}`, { key });
        toast(`${pid} key stored (encrypted: ${(await API.get("/api/health")).key_storage})`);
        await App.refreshConfig(); render();
      };
    });
    body.querySelectorAll("[data-verifykey]").forEach((b) => {
      b.onclick = async () => {
        const pid = b.dataset.verifykey;
        const st = document.getElementById("cloud-status");
        st.textContent = "verifying " + pid + "…";
        try {
          const r = await API.post(`/api/config/verify/${pid}`);
          st.innerHTML = r.ok ? `<span style="color:var(--green)">✓ ${esc(r.detail)}</span>`
            : `<span style="color:var(--red)">✕ ${esc(r.detail)}</span>`;
        } catch (e) { st.textContent = e.message; }
      };
    });
    body.querySelectorAll("[data-models]").forEach((b) => {
      b.onclick = async () => {
        const pid = b.dataset.models;
        const st = document.getElementById("cloud-status");
        st.textContent = "loading " + pid + " models…";
        try {
          const { models } = await API.get(`/api/cloud/${pid}/models`);
          const current = (App.state.config.cloud[pid] || {}).model || "";
          UI.modal({ title: `Pick ${pid} model for delegation`,
            body: `<div class="free-model-list">${models.slice(0, 80).map((mm) =>
              `<div class="free-model-item ${mm.id === current ? "sel" : ""}" data-m="${esc(mm.id)}">
                 <span>${esc(mm.name || mm.id)}</span><span class="fm-id">${esc(mm.id)}</span></div>`).join("")}</div>`,
            footer: `<button class="btn" data-close>Close</button>` })
            .querySelectorAll("[data-m]").forEach((o) => {
              o.onclick = async () => {
                await API.post("/api/config", { cloud: { [pid]: { model: o.dataset.m } } });
                await App.refreshConfig();
                st.innerHTML = `<span style="color:var(--green)">delegation model: ${esc(o.dataset.m)}</span>`;
                UI.toast(pid + " model set: " + o.dataset.m);
              };
            });
        } catch (e) { st.textContent = e.message; }
      };
    });
  }

  /* ============================== NODE NETWORKS ============================== */
  function renderNodes(body, cfg) {
    body.innerHTML = `
      <h2>Node Networks</h2>
      <div class="sub">Your agent fleet. Build, run, and host agents on local models.</div>
      <div class="set-row"><div><div class="s-label">Open the Agent Builder dashboard</div>
        <div class="s-desc">Visual node map, agent manifests, run console, hosted endpoints.</div></div>
        <button class="btn primary" id="nd-open">Open Node Networks</button></div>
      <div class="set-row"><div><div class="s-label">Hosted agents network scope</div>
        <div class="s-desc">Hosted agent endpoints bind with the app: 127.0.0.1 (localhost only) or 0.0.0.0 (LAN, when enabled in General).</div></div>
        <span class="tag ${cfg.general.lan_access ? "amber" : "green"}">${cfg.general.lan_access ? "localhost + LAN" : "localhost only"}</span></div>
      <div class="set-row"><div><div class="s-label">Scheduled agents</div>
        <div class="s-desc">Agents with trigger "scheduled" run in the background automatically.</div></div>
        <button class="btn" id="nd-agents">List agents</button></div>`;
    document.getElementById("nd-open").onclick = () => App.go("agents");
    document.getElementById("nd-agents").onclick = () => App.go("agents");
  }

  /* ================================ SYSTEM ================================ */
  function renderSystem(body, cfg) {
    body.innerHTML = `
      <h2>System &amp; Logs</h2><div class="sub">Transparency: everything the harness does is logged locally.</div>
      <div class="settings-section">
        <div class="set-row"><div><div class="s-label">Log level</div><div class="s-desc">Applies to new log lines.</div></div>
          <select id="sys-level">
            ${["DEBUG", "INFO", "WARNING", "ERROR"].map((l) =>
              `<option ${cfg.log_level === l ? "selected" : ""}>${l}</option>`).join("")}</select></div>
        <div class="set-row"><div><div class="s-label">Data folder</div>
          <div class="s-desc">config.json, harness.db, memory notes, agent manifests, logs.</div></div>
          <button class="btn" id="sys-openfolder">Open folder</button></div>
        <div class="set-row"><div><div class="s-label">Download full log</div><div class="s-desc">Rotating log file, max 2 MB × 2 backups.</div></div>
          <a class="btn" href="/api/logs/download" download>Download</a></div>
      </div>`;
    document.getElementById("sys-level").onchange = (e) => save({ log_level: e.target.value }, "Log level: " + e.target.value);
    document.getElementById("sys-openfolder").onclick = async () => {
      try {
        const r = await API.post("/api/system/open-data-dir");
        toast("Opened " + r.path);
      } catch (e) { toast(e.message, "error"); }
    };
  }

  /* ================================ DATABASE ================================ */
  async function renderDatabase(body) {
    const s = await API.get("/api/db/stats").catch(() => ({}));
    body.innerHTML = `
      <h2>Database</h2><div class="sub">Single SQLite file — copy it to back everything up.</div>
      <div class="grid2">
        <div class="card"><h3>Memory store</h3>
          <div class="map-row"><span class="micon">${icon("db")}</span>
            <div><div class="mname">Documents</div><div class="mstat">${s.documents ?? "—"}</div></div></div>
          <div class="map-row"><span class="micon">${icon("db")}</span>
            <div><div class="mname">Chunks</div><div class="mstat">${s.chunks ?? "—"} (${s.embedded_chunks ?? 0} embedded)</div></div></div>
          <div class="map-row"><span class="micon">${icon("db")}</span>
            <div><div class="mname">DB size</div><div class="mstat">${UI.fmtBytes(s.db_bytes)}</div></div></div>
          <div class="row" style="margin-top:10px">
            <button class="btn" id="db-reindex">Reindex all</button>
            <button class="btn" id="db-vacuum">Vacuum</button>
            <button class="btn danger" id="db-wipe">Wipe memory</button>
          </div></div>
        <div class="card"><h3>Agents</h3>
          <div class="map-row"><span class="micon">${icon("agents")}</span>
            <div><div class="mname">Defined agents</div><div class="mstat">${s.agents ?? 0}</div></div></div>
          <div class="map-row"><span class="micon">${icon("folder")}</span>
            <div><div class="mname">Data location</div><div class="mstat mono" style="font-size:10px">${esc(s.data_dir || "")}</div></div></div>
          <div class="privacy-box">Everything here is plain files + one SQLite DB on your disk.
            No cloud sync, no telemetry, nothing leaves the device.</div>
        </div>
      </div>`;
    document.getElementById("db-reindex").onclick = async () => { const r = await API.post("/api/memory/reindex"); toast("Reindexed " + r.reindexed + " docs"); render(); };
    document.getElementById("db-vacuum").onclick = async () => { await API.post("/api/db/vacuum"); toast("Vacuumed"); render(); };
    document.getElementById("db-wipe").onclick = async () => {
      if (!await confirmModal("Wipe Harness Memory", "Delete <b>all</b> notes, chunks and memory files? This cannot be undone.", "Wipe everything")) return;
      await API.post("/api/memory/wipe");
      toast("Memory wiped"); render();
    };
  }

  /* ================================ SECURITY ================================ */
  async function renderSecurity(body, cfg) {
    const folders = (await API.get("/api/fs/folders").catch(() => ({ folders: [] }))).folders;
    const health = await API.get("/api/health").catch(() => ({}));
    body.innerHTML = `
      <h2>Security</h2><div class="sub">Your data model, enforced in code — not just promised.</div>
      <div class="privacy-box" style="margin-bottom:16px">
        <b>Privacy guarantees</b><br>
        • All user data stays on this device — no implicit uploading, syncing, or telemetry.<br>
        • Cloud models (Claude, GPT, OpenRouter) <b>can never read, list, or search Harness Memory</b> —
        enforced at the routing layer: memory tools simply don't exist in their toolset.<br>
        • Only local models may query memory.<br>
        • API keys are stored encrypted at rest
        (<b>${esc(health.key_storage || "?")}</b>${health.key_storage === "plain" ? " — install <span class='mono'>cryptography</span> for encryption on this OS" : ""}) and masked in the UI.<br>
        • Anything that leaves the device is visible in the AI Activity trace.
      </div>
      <div class="settings-section"><h3>Permissions</h3>
        <div class="set-row"><div><div class="s-label">Internet access</div>
          <div class="s-desc">Off = every external code path is inert. On = AI Activity logs each external call.</div></div>
          ${UI.toggleSwitch("sec-internet", cfg.internet_enabled)}</div>
        <div class="set-row"><div><div class="s-label">Granted folders (${folders.length})</div>
          <div class="s-desc">${folders.length ? folders.map((f) => esc(f)).join("<br>") : "none — model sees no files"}</div></div>
          <button class="btn" id="sec-folders">Manage</button></div>
        <div class="set-row"><div><div class="s-label">Bluetooth module</div>
          <div class="s-desc">Off by default; opt-in per device. Requires the optional <span class="mono">bleak</span> package.</div></div>
          ${UI.toggleSwitch("sec-bt", cfg.general.bluetooth_enabled)}</div>
      </div>`;
    document.getElementById("sec-internet").onchange = (e) =>
      save({ general: { internet_enabled: e.target.checked } },
        e.target.checked ? "Internet ON" : "Internet OFF — fully local now");
    document.getElementById("sec-bt").onchange = (e) => save({ general: { bluetooth_enabled: e.target.checked } });
    document.getElementById("sec-folders").onclick = () => App.go("workspace");
  }

  /* nav */
  document.querySelectorAll("#settings-nav button").forEach((b) => {
    b.onclick = () => {
      document.querySelectorAll("#settings-nav button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      tab = b.dataset.tab;
      render();
    };
  });

  window.SettingsPage = { render };
})();
