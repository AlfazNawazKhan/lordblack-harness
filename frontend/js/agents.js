/* Node Networks: agent builder dashboard + network map + run console. */
(() => {
  const { esc, toast, icon, confirmModal } = UI;
  let selected = null;

  const TOOL_LABELS = {
    memory_search: "memory search", memory_add: "memory write", read_file: "read files",
    list_files: "list folders", external_lookup: "external API", bluetooth_scan: "bluetooth",
  };

  async function render() {
    let agents = [];
    try { agents = (await API.get("/api/agents")).agents; } catch (e) {}
    const list = document.getElementById("agent-list");
    list.innerHTML = agents.length ? "" : `<div class="empty">No agents yet. Create one — it runs entirely on your local model.</div>`;
    for (const a of agents) {
      const card = UI.el(`<div class="card agent-card" data-slug="${esc(a.slug)}">
        <div class="a-row"><span class="a-name">${esc(a.name)}</span>
          ${a.hosting?.enabled ? `<span class="tag cyan">HOSTED</span>` : ""}
          ${a.trigger?.type === "scheduled" ? `<span class="tag purple">SCHEDULED</span>` : ""}
          <span style="margin-left:auto"></span>
          <button class="icon-btn" data-edit title="Edit">${icon("edit", 14)}</button>
          <button class="icon-btn" data-remove title="Delete">${icon("trash", 14)}</button></div>
        <div class="a-desc">${esc(a.description || a.system_prompt.slice(0, 90))}</div>
        <div class="a-meta"><span class="tag">${esc(a.model || "default model")}</span>
          ${Object.entries(a.tools || {}).filter(([, v]) => v).map(([t]) =>
            `<span class="tag">${esc(TOOL_LABELS[t] || t)}</span>`).join("")}</div>
        ${a.hosting?.enabled ? `<div class="status-line mono" style="color:var(--accent-2)">
          POST http://${location.hostname}:${location.port || "8765"}/agents/${esc(a.slug)}/chat</div>` : ""}
      </div>`);
      card.onclick = (e) => {
        if (e.target.closest("button")) return;
        selected = a.slug;
        document.getElementById("run-agent-name").textContent = a.name;
        list.querySelectorAll(".doc-item, .agent-card").forEach((x) => x.classList.remove("sel"));
        card.classList.add("sel");
      };
      card.querySelector("[data-edit]").onclick = () => editAgent(a);
      card.querySelector("[data-remove]").onclick = async () => {
        if (!await confirmModal("Delete agent", `Delete <b>${esc(a.name)}</b>?`, "Delete")) return;
        await API.del("/api/agents/" + a.slug);
        render(); drawNetwork();
      };
      list.appendChild(card);
    }
    drawNetwork();
  }

  /* ---------------- network map (SVG) ---------------- */
  async function drawNetwork() {
    const view = document.getElementById("network-view");
    const agents = (await API.get("/api/agents").catch(() => ({ agents: [] }))).agents;
    const cfg = App.state.config || {};
    const active = cfg.active || {};
    const cloud = Object.entries(cfg.cloud || {}).filter(([, c]) => c.enabled && c.key_set);
    const W = view.clientWidth || 800, H = 340;
    const cx = W / 2, cy = H / 2;

    const nodes = [];
    // center: active model
    nodes.push({ id: "model", x: cx - 180, y: cy, label: active.model || "no model",
      sub: "primary local", color: "var(--accent)" });
    if (active.secondary && active.secondary.model)
      nodes.push({ id: "sec", x: cx - 180, y: cy + 90, label: active.secondary.model,
        sub: "duet partner", color: "var(--green)" });
    nodes.push({ id: "memory", x: cx, y: cy - 95, label: "Memory", sub: "SQLite + vectors",
      color: "var(--accent-2)" });
    if (cloud.length) nodes.push({ id: "cloud", x: cx + 10, y: cy + 90,
      label: "Cloud APIs", sub: cloud.map(([k]) => k).join(", "), color: "var(--purple)" });

    agents.slice(0, 6).forEach((a, i) => {
      nodes.push({ id: "ag-" + a.slug, x: cx + 200, y: 50 + i * 52, label: a.name,
        sub: a.hosting?.enabled ? "hosted" : a.trigger?.type, color: "var(--amber)", agent: a.slug });
    });

    let edges = "";
    for (const n of nodes) {
      if (n.id.startsWith("ag-")) {
        edges += line(cx + 200, n.y, cx - 180, cy, "var(--border-2)");
        if (n.agent && (agents.find((a) => a.slug === n.agent)?.tools || {}).memory_search)
          edges += line(cx + 200, n.y, cx, cy - 95, "rgba(45,212,191,0.25)");
      }
    }
    edges += line(cx - 180, cy, cx, cy - 95, "var(--border-2)");
    if (nodes.find((n) => n.id === "cloud"))
      edges += line(cx - 180, cy, cx + 10, cy + 90, "rgba(167,139,250,0.3)");

    const dot = (n) => `
      <g style="cursor:${n.agent ? "pointer" : "default"}" data-agent="${n.agent || ""}">
        <circle cx="${n.x}" cy="${n.y}" r="26" fill="#10161f" stroke="${n.color}" stroke-width="1.5"/>
        <text x="${n.x}" y="${n.y - 34}" fill="#d9e1ea" font-size="11" text-anchor="middle" font-family="Segoe UI">${esc(n.label.slice(0, 24))}</text>
        <text x="${n.x}" y="${n.y + 42}" fill="#5a6b7d" font-size="9.5" text-anchor="middle" font-family="Consolas">${esc((n.sub || "").slice(0, 30))}</text>
      </g>`;

    view.innerHTML = `<svg viewBox="0 0 ${W} ${H}">
      <text x="14" y="24" fill="#5a6b7d" font-size="10" letter-spacing="2">ACTIVE NODE MAPPING</text>
      ${edges}${nodes.map(dot).join("")}</svg>`;
    view.querySelectorAll("[data-agent]").forEach((g) => {
      if (!g.dataset.agent) return;
      g.onclick = () => {
        const a = agents.find((x) => x.slug === g.dataset.agent);
        if (a) editAgent(a);
      };
    });
  }
  function line(x1, y1, x2, y2, color) {
    return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${color}" stroke-width="1"/>`;
  }

  /* ---------------- editor ---------------- */
  async function editAgent(existing) {
    const a = existing || { name: "", description: "", system_prompt: "", model: "",
      max_turns: 6, temperature: 0.4,
      tools: { memory_search: true, memory_add: false, read_file: false, list_files: false,
               external_lookup: false, bluetooth_scan: false },
      trigger: { type: "manual", interval_minutes: 30, prompt: "" },
      hosting: { enabled: false, rate_limit_per_min: 30 } };
    const models = [];
    try { for (const g of await API.get("/api/models")) for (const m of g.models) models.push(m); } catch (e) {}
    const t = a.tools || {};
    const m = UI.modal({
      title: existing ? "Edit agent — " + esc(a.name) : "New agent",
      wide: true,
      body: `
        <div class="grid2" style="gap:10px">
          <div class="field"><label>NAME</label><input id="ag-name" value="${esc(a.name)}"></div>
          <div class="field"><label>MODEL</label><select id="ag-model">
            <option value="">(active model)</option>
            ${models.map((mm) => `<option ${mm === a.model ? "selected" : ""}>${esc(mm)}</option>`).join("")}
          </select></div>
        </div>
        <div class="field"><label>DESCRIPTION</label><input id="ag-desc" value="${esc(a.description || "")}"></div>
        <div class="field"><label>SYSTEM PROMPT</label>
          <textarea id="ag-sys" rows="4" style="font-family:var(--mono);font-size:12px">${esc(a.system_prompt)}</textarea></div>
        <div class="field"><label>TOOLS</label>
          <div class="tool-checkboxes">
            ${Object.keys(TOOL_LABELS).map((k) => `
              <label><input type="checkbox" data-tool="${k}" ${t[k] ? "checked" : ""}> ${esc(TOOL_LABELS[k])}</label>`).join("")}
          </div></div>
        <div class="grid2" style="gap:10px">
          <div class="field"><label>TRIGGER</label><select id="ag-trigger">
            <option value="manual" ${a.trigger.type === "manual" ? "selected" : ""}>Manual (chat/run)</option>
            <option value="scheduled" ${a.trigger.type === "scheduled" ? "selected" : ""}>Scheduled</option>
          </select></div>
          <div class="field"><label>INTERVAL (MINUTES, SCHEDULED)</label>
            <input type="number" id="ag-interval" value="${a.trigger.interval_minutes || 30}"></div>
        </div>
        <div class="field"><label>SCHEDULED PROMPT</label><input id="ag-sprompt" value="${esc(a.trigger.prompt || "")}"></div>
        <div class="row" style="margin-bottom:8px">
          <span class="small muted">Host as endpoint</span>${UI.toggleSwitch("ag-host", a.hosting.enabled)}
          <span class="small muted">Rate limit / min</span>
          <input type="number" id="ag-rate" value="${a.hosting.rate_limit_per_min || 30}" style="width:80px">
          <span class="small muted">Max turns</span>
          <input type="number" id="ag-turns" value="${a.max_turns || 6}" style="width:70px">
          <span class="small muted">Temp</span>
          <input type="number" step="0.1" id="ag-temp" value="${a.temperature ?? 0.4}" style="width:70px">
        </div>
        <div class="field"><label>HOSTING BEARER TOKEN (OPTIONAL — RECOMMENDED FOR LAN)</label>
          <input id="ag-token" class="mono" value="${esc(a.hosting.token || "")}"
                 placeholder="leave empty for no auth; if set, callers must send: Authorization: Bearer <token>"></div>`,
      footer: `<button class="btn" data-close>Cancel</button><button class="btn primary" id="ag-save">Save agent</button>`,
    });
    m.querySelector("#ag-save").onclick = async () => {
      const tools = {};
      m.querySelectorAll("[data-tool]").forEach((c) => (tools[c.dataset.tool] = c.checked));
      const manifest = {
        ...(existing || {}),
        name: m.querySelector("#ag-name").value.trim() || "Agent",
        description: m.querySelector("#ag-desc").value,
        system_prompt: m.querySelector("#ag-sys").value,
        model: m.querySelector("#ag-model").value,
        tools,
        max_turns: +m.querySelector("#ag-turns").value || 6,
        temperature: +m.querySelector("#ag-temp").value || 0.4,
        trigger: { type: m.querySelector("#ag-trigger").value,
                   interval_minutes: +m.querySelector("#ag-interval").value || 30,
                   prompt: m.querySelector("#ag-sprompt").value },
        hosting: { enabled: m.querySelector("#ag-host").checked,
                   rate_limit_per_min: +m.querySelector("#ag-rate").value || 30,
                   token: m.querySelector("#ag-token").value.trim() },
      };
      try {
        if (existing) await API.put("/api/agents/" + existing.slug, manifest);
        else await API.post("/api/agents", manifest);
        m.remove(); toast("Agent saved"); render();
      } catch (e) { toast(e.message, "error"); }
    };
  }

  /* ---------------- run console ---------------- */
  document.getElementById("btn-new-agent").onclick = () => editAgent(null);

  document.getElementById("btn-run-agent").onclick = async () => {
    if (!selected) return toast("Select an agent first", "warn");
    const input = document.getElementById("agent-run-input").value.trim();
    if (!input) return toast("Type a task for the agent", "warn");
    const consoleEl = document.getElementById("agent-console");
    consoleEl.textContent = "";
    document.getElementById("btn-run-agent").disabled = true;
    document.getElementById("active-agent").textContent = selected;
    Workspace.activeAgent = selected;
    await API.sse(`/api/agents/${selected}/run`, { input }, {
      onEvent: (name, data) => {
        if (name === "token") consoleEl.textContent += data.t;
        else if (name === "trace") {
          consoleEl.textContent += `\n[${data.status}] ${data.title}${data.detail ? " — " + data.detail : ""}\n`;
          Activity.add(data);
        } else if (name === "error") consoleEl.textContent += `\n[error] ${data.message}\n`;
        else if (name === "done") consoleEl.textContent += `\n[done] tools used: ${data.tools_used?.join(", ") || "none"}\n`;
        consoleEl.scrollTop = consoleEl.scrollHeight;
      },
      onError: (e) => { consoleEl.textContent += "\n[error] " + e.message; },
    });
    document.getElementById("btn-run-agent").disabled = false;
  };

  window.AgentsPage = { render };
})();
