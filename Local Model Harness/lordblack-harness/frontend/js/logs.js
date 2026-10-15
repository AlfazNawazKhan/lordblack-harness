/* Logs page: live console fed by the same SSE log stream. */
(() => {
  let started = false;

  function start() {
    if (started) return;
    started = true;
    const box = document.getElementById("logs-console");
    let count = 0;
    const pump = () => API.sse("/api/logs/stream", undefined, {
      onEvent: (name, data) => {
        const line = (data.line || "").replace(/</g, "&lt;");
        const lv = line.includes("[WARNING]") ? "WARNING" : line.includes("[ERROR]") ? "ERROR"
          : line.includes("[DEBUG]") ? "DEBUG" : "INFO";
        const div = document.createElement("div");
        div.className = "lv-" + lv;
        div.innerHTML = line;
        box.appendChild(div);
        if (++count > 600) { box.removeChild(box.firstChild); count--; }
        box.scrollTop = box.scrollHeight;
      },
      onError: () => setTimeout(pump, 3000),
    });
    pump();
  }

  async function init() {
    start();
    const box = document.getElementById("logs-console");
    if (!box.childElementCount) {
      try {
        const { lines } = await API.get("/api/logs/recent");
        box.innerHTML = lines.map((l) =>
          `<div class="${l.includes("[WARNING]") ? "lv-WARNING" : l.includes("[ERROR]") ? "lv-ERROR" : "lv-INFO"}">${l.replace(/</g, "&lt;")}</div>`).join("")
          || `<div class="empty">no log lines yet</div>`;
        box.scrollTop = box.scrollHeight;
      } catch (e) {}
    }
  }

  document.getElementById("log-level").onchange = async (e) => {
    await API.post("/api/config", { log_level: e.target.value });
    UI.toast("Log level set to " + e.target.value + " (new lines)");
  };

  window.LogsPage = { init };
})();
