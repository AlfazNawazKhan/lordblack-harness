/* Cloud API providers page: key management, verify, OpenRouter free catalog. */
(() => {
  const { esc, toast } = UI;
  const PROVIDERS = {
    openrouter: { title: "OpenRouter API", hint: "Mixes free & paid models. Delegation prefers free ones.", url: "https://openrouter.ai/keys" },
    anthropic: { title: "Anthropic Claude API", hint: "Used for external delegation only — never sees your memory.", url: "https://console.anthropic.com" },
    openai: { title: "OpenAI GPT API", hint: "Used for external delegation only — never sees your memory.", url: "https://platform.openai.com" },
  };

  async function render() {
    const wrap = document.getElementById("cloud-cards");
    const cfg = App.state.config;
    wrap.innerHTML = "";
    for (const [pid, meta] of Object.entries(PROVIDERS)) {
      const conf = (cfg.cloud || {})[pid] || {};
      const card = UI.el(`<div class="card">
        <h3>${esc(meta.title)}
          <span class="tag ${conf.enabled && conf.key_set ? "green" : ""}" style="margin-left:auto">
            ${conf.enabled && conf.key_set ? "CONNECTED" : "NOT CONNECTED"}</span></h3>
        <div class="desc">${esc(meta.hint)} <a href="${meta.url}" target="_blank" rel="noopener">get a key ↗</a></div>
        <div class="url-row">
          ${conf.key_set
            ? `<input class="mono" value="${esc(conf.key_masked || "••••••••")}" disabled style="flex:1">
               <button class="btn small" data-verify>Verify</button>
               <button class="btn small" data-modify>Modify</button>
               <button class="btn small danger" data-clear>Remove</button>`
            : `<input class="mono" data-key placeholder="Paste API key…" style="flex:1">
               <button class="btn primary small" data-connect>Connect</button>`}
        </div>
        <div class="status-line" data-status></div>
        ${pid === "openrouter" ? `
          <div class="row" style="margin-top:10px">
            <span class="small muted">Prefer free models</span>${UI.toggleSwitch("or-free", conf.prefer_free !== false)}
            <span class="small muted">Allow paid models</span>${UI.toggleSwitch("or-paid", !!conf.allow_paid)}
          </div>` : ""}
      </div>`);

      const status = card.querySelector("[data-status]");
      const connect = card.querySelector("[data-connect]");
      if (connect) connect.onclick = async () => {
        const key = card.querySelector("[data-key]").value.trim();
        if (!key) return toast("Paste a key first", "warn");
        await API.post(`/api/config/keys/${pid}`, { key });
        toast(`${meta.title} connected — key encrypted at rest (${(await API.get("/api/health")).key_storage})`);
        await App.refreshConfig(); render();
      };
      const verify = card.querySelector("[data-verify]");
      if (verify) verify.onclick = async () => {
        status.textContent = "verifying…";
        try {
          const r = await API.post(`/api/config/verify/${pid}`);
          status.innerHTML = r.ok
            ? `<span style="color:var(--green)">✓ ${esc(r.detail)}</span>`
            : `<span style="color:var(--red)">✕ ${esc(r.detail)}</span>`;
        } catch (e) { status.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; }
      };
      const modify = card.querySelector("[data-modify]");
      if (modify) modify.onclick = async () => {
        const key = prompt("Paste the new " + meta.title + " key:");
        if (!key) return;
        await API.post(`/api/config/keys/${pid}`, { key: key.trim() });
        toast("Key updated"); await App.refreshConfig(); render();
      };
      const clear = card.querySelector("[data-clear]");
      if (clear) clear.onclick = async () => {
        await API.post(`/api/config/keys/${pid}`, { key: "" });
        toast("Key removed"); await App.refreshConfig(); render();
      };
      const free = card.querySelector("#or-free");
      if (free) free.onchange = async () => {
        await API.post("/api/config", { cloud: { openrouter: { prefer_free: free.checked } } });
        await App.refreshConfig();
      };
      const paid = card.querySelector("#or-paid");
      if (paid) paid.onchange = async () => {
        await API.post("/api/config", { cloud: { openrouter: { allow_paid: paid.checked } } });
        await App.refreshConfig();
      };
      wrap.appendChild(card);
    }
    renderFreeModels();
  }

  async function renderFreeModels() {
    const list = document.getElementById("free-model-list");
    const cfg = App.state.config;
    const catalog = (cfg.cloud?.openrouter?.catalog) || [];
    if (!catalog.length) { list.innerHTML = `<div class="empty">Connect OpenRouter and refresh to load the catalog.</div>`; return; }
    const q = (document.getElementById("fm-search").value || "").toLowerCase();
    const free = catalog.filter((m) => m.free && (!q || m.id.toLowerCase().includes(q) || (m.name || "").toLowerCase().includes(q)));
    const preferred = cfg.cloud?.openrouter?.preferred_model || "";
    list.innerHTML = "";
    const auto = UI.el(`<div class="free-model-item ${!preferred ? "sel" : ""}">
      <b>Auto</b><span class="fm-id">harness picks the best currently-free model per task</span></div>`);
    auto.onclick = async () => {
      await API.post("/api/config", { cloud: { openrouter: { preferred_model: "" } } });
      await App.refreshConfig(); renderFreeModels();
    };
    list.appendChild(auto);
    for (const m of free.slice(0, 120)) {
      const item = UI.el(`<div class="free-model-item ${m.id === preferred ? "sel" : ""}">
        <span>${esc(m.name)}</span><span class="fm-id">${esc(m.id)}</span>
        ${m.context ? `<span class="fm-id" style="margin-left:auto">${Math.round(m.context / 1000)}k</span>` : ""}</div>`);
      item.onclick = async () => {
        await API.post("/api/config", { cloud: { openrouter: { preferred_model: m.id } } });
        await App.refreshConfig(); renderFreeModels();
      };
      list.appendChild(item);
    }
  }

  document.getElementById("fm-search").addEventListener("input", renderFreeModels);
  document.getElementById("fm-refresh").onclick = async () => {
    if (!App.state.config.internet_enabled) return toast("Enable internet access first (Settings → Security)", "warn");
    const btn = document.getElementById("fm-refresh");
    btn.disabled = true; btn.textContent = "loading…";
    try {
      // goes through the backend, which enforces the internet gate + key auth
      const r = await API.post("/api/cloud/openrouter/catalog");
      await App.refreshConfig();
      renderFreeModels();
      toast(`Catalog loaded: ${r.free} free of ${r.total} models`);
    } catch (e) { toast(e.message, "error"); }
    btn.disabled = false; btn.textContent = "Refresh catalog";
  };

  window.CloudPage = { render };
})();
