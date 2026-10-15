/* Model Hub: runtime cards, model lists, active model selection, duet model. */
(() => {
  const { esc, icon, toast, modal, fmtTime } = UI;

  async function render() {
    const wrap = document.getElementById("runtime-cards");
    wrap.innerHTML = `<div class="empty">Probing runtimes…</div>`;
    let data;
    try { data = await API.get("/api/runtimes"); }
    catch (e) { wrap.innerHTML = `<div class="empty">backend offline</div>`; return; }
    wrap.innerHTML = "";
    for (const r of data.runtimes) {
      const st = r.status || {};
      const card = UI.el(`<div class="card">
        <h3>${icon("hub", 15)} ${esc(r.name)}
          <span class="tag ${st.ok ? "green" : r.enabled ? "red" : "amber"}" style="margin-left:auto">
            ${st.ok ? "CONNECTED" : r.enabled ? "OFFLINE" : "DISABLED"}</span></h3>
        <div class="desc">${esc(r.type)} · ${esc(r.base_url)}
          ${st.latency_ms != null ? ` · <span class="mono">${st.latency_ms} ms</span>` : ""}</div>
        <div class="url-row">
          <input value="${esc(r.base_url)}" data-url>
          <button class="btn small" data-test>Test</button>
          <button class="btn small" data-save>Save URL</button>
          ${UI.toggleSwitch("rt-" + r.id, r.enabled)}
        </div>
        <div class="model-grid" data-models></div>
        <div class="status-line" data-status>${esc(st.detail || "")}</div>
        <div class="row" style="margin-top:10px">
          <button class="btn small" data-refresh>${icon("refresh", 12)} Refresh models</button>
          <button class="btn small" data-duet>◎ Use for Duet</button>
          <button class="btn small danger" data-del style="margin-left:auto">Remove</button>
        </div>
      </div>`);
      const modelsBox = card.querySelector("[data-models]");
      const active = App.state.config?.active || {};
      const drawModels = (models) => {
        modelsBox.innerHTML = models.length ? "" :
          `<span class="faint small">No models detected. Load one in your runtime, then refresh.</span>`;
        for (const m of models) {
          const isActive = active.runtime_id === r.id && active.model === m;
          const isDuet = active.secondary && active.secondary.runtime_id === r.id &&
            active.secondary.model === m;
          const pill = UI.el(`<span class="model-pill ${isActive ? "active" : ""}">
            ${isActive ? '<span class="star">★</span>' : isDuet ? "◎ " : ""}${esc(m)}</span>`);
          pill.onclick = async () => {
            await API.post(`/api/runtimes/${r.id}/set-active`, { model: m });
            toast(`Active model: ${m}`);
            await App.refreshConfig();
            render();
          };
          modelsBox.appendChild(pill);
        }
      };
      drawModels(r.models || []);
      card.querySelector("[data-refresh]").onclick = async () => {
        try { drawModels(await API.get(`/api/models`)); } catch (e) {}
        render();
      };
      card.querySelector("[data-test]").onclick = async () => {
        const b = card.querySelector("[data-status]");
        b.textContent = "testing…";
        try {
          const res = await API.post(`/api/runtimes/${r.id}/test`);
          b.innerHTML = res.ok
            ? `<span style="color:var(--green)">reachable · ${res.latency_ms} ms</span>`
            : `<span style="color:var(--red)">${esc(res.detail)}</span>`;
        } catch (e) { b.textContent = e.message; }
      };
      card.querySelector("[data-save]").onclick = async () => {
        const url = card.querySelector("[data-url]").value.trim();
        const runtimes = App.state.config.runtimes.map((x) =>
          x.id === r.id ? { ...x, base_url: url } : x);
        await API.post("/api/config", { runtimes });
        toast("URL saved"); await App.refreshConfig(); render();
      };
      card.querySelector('[data-duet]').onclick = async () => {
        const cfg = App.state.config;
        const sec = { runtime_id: r.id, model: (r.models || [])[0] || "" };
        if (cfg.active.secondary && cfg.active.secondary.runtime_id === r.id) sec.model = "";
        cfg.active.secondary = sec;
        await API.post("/api/config", { active: cfg.active });
        toast(sec.model ? `Duet partner: ${sec.model}` : "Duet partner cleared");
        await App.refreshConfig(); render();
      };
      card.querySelector("[data-del]").onclick = async () => {
        if (!await UI.confirmModal("Remove runtime", `Remove <b>${esc(r.name)}</b>?`, "Remove")) return;
        await API.del("/api/runtimes/" + r.id);
        await App.refreshConfig(); render();
      };
      card.querySelector(".switch input").onchange = async (e) => {
        const runtimes = App.state.config.runtimes.map((x) =>
          x.id === r.id ? { ...x, enabled: e.target.checked } : x);
        await API.post("/api/config", { runtimes });
        await App.refreshConfig(); render();
      };
      wrap.appendChild(card);
    }
  }

  document.getElementById("btn-detect-runtimes").onclick = async () => {
    const out = document.getElementById("detect-result");
    out.textContent = "scanning…";
    try {
      const { found } = await API.post("/api/runtimes/detect");
      out.textContent = found.length
        ? "found: " + found.map((f) => f.type + "@" + f.base_url.replace("http://127.0.0.1:", "")).join(", ")
        : "none found — is LM Studio running with the server started?";
      for (const f of found) {
        const existing = App.state.config.runtimes.some((r) => r.base_url === f.base_url);
        if (!existing) await API.post("/api/runtimes", f);
      }
      await App.refreshConfig();
      if (found.length) render();
    } catch (e) { out.textContent = e.message; }
  };

  document.getElementById("btn-add-runtime").onclick = () => {
    const m = modal({
      title: "Add local runtime",
      body: `<div class="field"><label>TYPE</label>
          <select id="ar-type">
            <option value="lmstudio">LM Studio (OpenAI-compatible, port 1234)</option>
            <option value="ollama">Ollama (port 11434)</option>
            <option value="llamacpp">llama.cpp server (port 8080)</option>
            <option value="llamacpp_embedded">llama.cpp embedded (llama-cpp-python)</option>
          </select></div>
        <div class="field"><label>NAME</label><input id="ar-name" value="My runtime"></div>
        <div class="field"><label>SERVER URL</label><input id="ar-url" value="http://127.0.0.1:1234"></div>
        <div class="field" id="ar-path-field" style="display:none"><label>GGUF MODEL PATH</label>
          <input id="ar-path" placeholder="C:\\models\\qwen2.5-3b-instruct-q4_k_m.gguf"></div>`,
      footer: `<button class="btn" data-close>Cancel</button><button class="btn primary" id="ar-ok">Add</button>`,
    });
    m.querySelector("#ar-type").onchange = (e) => {
      m.querySelector("#ar-path-field").style.display =
        e.target.value === "llamacpp_embedded" ? "" : "none";
    };
    m.querySelector("#ar-ok").onclick = async () => {
      try {
        await API.post("/api/runtimes", {
          type: m.querySelector("#ar-type").value,
          name: m.querySelector("#ar-name").value,
          base_url: m.querySelector("#ar-url").value,
          model_path: m.querySelector("#ar-path").value,
        });
        m.remove(); await App.refreshConfig(); render();
      } catch (e) { toast(e.message, "error"); }
    };
  };

  window.ModelHub = { render };
})();
