/* App shell: routing, topbar status, statusbar sparkline, boot + onboarding. */
(() => {
  const { toast, esc } = UI;
  const PAGES = ["workspace", "modelhub", "cloud", "memory", "agents", "logs", "settings"];

  App.go = (page) => {
    if (!PAGES.includes(page)) page = "workspace";
    App.state.page = page;
    location.hash = "#/" + page;
    document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
    document.getElementById("page-" + page).classList.add("active");
    document.querySelectorAll("#rail .rail-btn").forEach((b) =>
      b.classList.toggle("active", b.dataset.page === page));

    if (page === "modelhub") ModelHub.render();
    else if (page === "cloud") CloudPage.render();
    else if (page === "memory") MemoryPage.render();
    else if (page === "agents") AgentsPage.render();
    else if (page === "logs") LogsPage.init();
    else if (page === "settings") SettingsPage.render();
    if (page === "workspace") WorkspaceBoot();
  };

  App.refreshConfig = async () => {
    App.state.config = await API.get("/api/config");
    const cfg = App.state.config;
    // keep chip + latency fresh
    const chip = document.getElementById("net-chip");
    const label = document.getElementById("net-label");
    if (cfg.internet_enabled) {
      chip.className = "chip online"; label.textContent = "ONLINE";
    } else {
      chip.className = "chip offline"; label.textContent = "OFFLINE · PRIVATE";
    }
    if (window.Workspace) Workspace.updateModelChips && Workspace.updateModelChips();
    return cfg;
  };

  /* ---------------- rail + routing ---------------- */
  document.querySelectorAll("#rail .rail-btn").forEach((b) => {
    b.onclick = () => App.go(b.dataset.page);
  });
  // back/forward buttons and manual hash edits switch pages too
  window.addEventListener("hashchange", () => {
    const page = (location.hash || "#/workspace").replace("#/", "");
    if (page !== App.state.page) App.go(page);
  });

  /* ---------------- command palette ---------------- */
  UI.registerCommands([
    { label: "Go to Workspace", icon: "workspace", run: () => App.go("workspace") },
    { label: "Go to Model Hub", icon: "hub", run: () => App.go("modelhub") },
    { label: "Go to Cloud Providers", icon: "cloud", run: () => App.go("cloud") },
    { label: "Go to Memory", icon: "memory", run: () => App.go("memory") },
    { label: "Go to Node Networks (Agents)", icon: "agents", run: () => App.go("agents") },
    { label: "Go to Logs", icon: "logs", run: () => App.go("logs") },
    { label: "Open Settings", icon: "settings", run: () => App.go("settings"), kbd: "Ctrl+," },
    { label: "Toggle internet access", icon: "shield",
      run: async () => {
        const on = !App.state.config.internet_enabled;
        await API.post("/api/config", { general: { internet_enabled: on } });
        await App.refreshConfig();
        toast(on ? "Internet ON" : "Internet OFF — fully local");
      } },
    { label: "Detect local runtimes", icon: "refresh", run: () => { App.go("modelhub");
      document.getElementById("btn-detect-runtimes").click(); } },
    { label: "New memory note", icon: "memory", run: () => { App.go("memory");
      setTimeout(() => document.getElementById("btn-new-doc").click(), 150); } },
    { label: "New agent", icon: "agents", run: () => { App.go("agents");
      setTimeout(() => document.getElementById("btn-new-agent").click(), 150); } },
    { label: "Grant folder access", icon: "folder", run: () => { App.go("workspace");
      setTimeout(() => document.getElementById("btn-add-folder").click(), 150); } },
    { label: "Clear AI Activity trace", icon: "trash", run: () => window.Activity && Activity.clear() },
    { label: "New chat (start fresh conversation)", icon: "zap",
      run: () => { App.go("workspace");
        setTimeout(() => window.WorkspaceClearChat && WorkspaceClearChat(), 150); } },
  ]);
  document.getElementById("cmdk").onclick = () => UI.openPalette();
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault(); UI.openPalette();
    }
    if ((e.ctrlKey || e.metaKey) && e.key === ",") { e.preventDefault(); App.go("settings"); }
    if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
      e.preventDefault(); UI.openPalette();
    }
  });
  document.getElementById("win-close").onclick = () => window.close();

  /* ---------------- statusbar: SSE stats + sparkline ---------------- */
  function startStats() {
    const spark = document.getElementById("cpu-spark");
    const ctx = spark.getContext("2d");
    const hist = new Array(60).fill(0);
    const draw = () => {
      ctx.clearRect(0, 0, spark.width, spark.height);
      ctx.beginPath();
      hist.forEach((v, i) => {
        const x = (i / (hist.length - 1)) * spark.width;
        const y = spark.height - (v / 100) * (spark.height - 2) - 1;
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      });
      ctx.strokeStyle = "#22d3ee";
      ctx.lineWidth = 1.2;
      ctx.stroke();
    };
    draw();
    const pump = () => API.sse("/api/system/stream", undefined, {
      onEvent: (name, data) => {
        hist.push(data.cpu || 0); hist.shift(); draw();
        document.getElementById("sb-cpu").textContent = "CPU " + (data.cpu ?? "—") + "%";
        document.getElementById("sb-ram").textContent =
          "RAM " + (data.ram_used ?? "—") + "/" + (data.ram_total ?? "—") + " GB";
        document.getElementById("sb-vram").textContent =
          "VRAM " + (data.vram != null ? data.vram + "%" : "n/a");
      },
      onError: () => setTimeout(pump, 4000),
    });
    pump();
    API.get("/api/system/ip").then((r) => {
      document.getElementById("sb-port").textContent =
        "serving " + (App.state.config?.general?.lan_access ? r.ip : "localhost") + ":" + (App.state.config?.general?.port || 8765);
    }).catch(() => {});
  }

  /* ---------------- health poll (latency + status) ---------------- */
  async function healthLoop() {
    for (;;) {
      try {
        const t0 = performance.now();
        const h = await API.get("/api/health");
        document.getElementById("latency").textContent = Math.round(performance.now() - t0) + " ms";
        if (!App.state.config) await App.refreshConfig();
      } catch (e) {
        const l = document.getElementById("latency");
        l.textContent = "backend unreachable";
      }
      await new Promise((r) => setTimeout(r, 30000));
    }
  }

  /* ---------------- onboarding: first-run connect flow ---------------- */
  async function maybeOnboard() {
    const cfg = App.state.config;
    const connected = (cfg.runtimes || []).some((r) => r.enabled) && cfg.active?.model;
    if (connected) return;
    const m = UI.modal({
      title: "Welcome to LordBlack Harness",
      body: `
        <p class="muted" style="line-height:1.6;margin-bottom:14px">
          Connect a local runtime to start. In <b>LM Studio</b>: open the <b>Developer</b> tab,
          start the <b>Local Server</b>, and paste the “Reachable at” URL here
          (usually <span class="mono">http://127.0.0.1:1234</span>).
          Ollama and llama.cpp servers work too.</p>
        <div class="field"><label>RUNTIME</label>
          <select id="ob-type">
            <option value="lmstudio">LM Studio</option>
            <option value="ollama">Ollama</option>
            <option value="llamacpp">llama.cpp server</option>
          </select></div>
        <div class="field"><label>SERVER URL</label><input id="ob-url" class="mono" value="http://127.0.0.1:1234"></div>
        <div class="status-line" id="ob-status"></div>`,
      footer: `<button class="btn" data-close>Skip for now</button>
               <button class="btn primary" id="ob-connect">Connect</button>`,
    });
    m.querySelector("#ob-type").onchange = (e) => {
      m.querySelector("#ob-url").value = e.target.value === "ollama"
        ? "http://127.0.0.1:11434" : e.target.value === "llamacpp"
        ? "http://127.0.0.1:8080" : "http://127.0.0.1:1234";
    };
    m.querySelector("#ob-connect").onclick = async () => {
      const status = m.querySelector("#ob-status");
      status.textContent = "connecting…";
      try {
        await API.post("/api/runtimes", {
          type: m.querySelector("#ob-type").value,
          name: m.querySelector("#ob-type").selectedOptions[0].textContent,
          base_url: m.querySelector("#ob-url").value.trim(),
        });
        await App.refreshConfig();
        const list = await API.get("/api/runtimes");
        const me = list.runtimes[list.runtimes.length - 1];
        if (me && me.status.ok) {
          await API.post(`/api/runtimes/${me.id}/set-active`, { model: (me.models || [])[0] || "" });
          await App.refreshConfig();
          status.innerHTML = `<span style="color:var(--green)">Connected — found: ${(me.models || []).join(", ") || "none"}</span>`;
          setTimeout(() => { m.remove(); toast("You're live. Start chatting!"); }, 700);
        } else {
          status.innerHTML = `<span style="color:var(--red)">${esc(me?.status?.detail || "unreachable")} — is the server running?</span>`;
        }
      } catch (e) { status.textContent = e.message; }
    };
  }

  /* ---------------- boot ---------------- */
  (async () => {
    if (API.isFile) {
      const b = document.createElement("div");
      b.className = "offline-banner";
      b.innerHTML = "You opened <b>index.html</b> directly. Start the backend first: double-click <b>run.bat</b> " +
        "(Windows) or <b>run.sh</b> — it serves this UI at http://127.0.0.1:8765. Using that address now.";
      document.getElementById("main").prepend(b);
    }
    try { await App.refreshConfig(); }
    catch (e) { toast("Backend unreachable — run run.bat / run.sh", "error"); }
    App.go((location.hash || "#/workspace").replace("#/", ""));
    startStats();
    healthLoop();
    maybeOnboard();
  })();
})();
