/* UI helpers: global state, event bus, dom(), toast, modal, palette. */
window.App = { state: { config: null, page: "workspace" }, bus: new EventTarget() };

window.UI = (() => {
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function el(html) {
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }

  function toast(msg, kind = "") {
    const t = el(`<div class="toast ${kind}">${esc(msg)}</div>`);
    document.getElementById("toast-root").appendChild(t);
    setTimeout(() => t.remove(), 4200);
  }

  function icon(name, size = 15) {
    return `<svg width="${size}" height="${size}"><use href="#i-${name}"/></svg>`;
  }

  /* ---------- modal ---------- */
  function modal({ title, body, footer, wide }) {
    const root = document.getElementById("modal-root");
    const back = el(`<div class="modal-backdrop"><div class="modal ${wide ? "wide" : ""}">
      <div class="modal-head"><span>${esc(title)}</span>
        <button class="icon-btn" data-close>${icon("x")}</button></div>
      <div class="modal-body">${body}</div>
      ${footer ? `<div class="modal-foot">${footer}</div>` : ""}
    </div></div>`);
    back.addEventListener("click", (e) => {
      if (e.target === back || e.target.closest("[data-close]")) back.remove();
    });
    root.appendChild(back);
    return back;
  }

  function confirmModal(title, text, okLabel = "Confirm") {
    return new Promise((resolve) => {
      const m = modal({
        title, body: `<p class="muted" style="line-height:1.6">${esc(text)}</p>`,
        footer: `<button class="btn" data-no>Cancel</button>
                 <button class="btn danger" data-yes>${esc(okLabel)}</button>`,
      });
      m.querySelector("[data-no]").onclick = () => { m.remove(); resolve(false); };
      m.querySelector("[data-yes]").onclick = () => { m.remove(); resolve(true); };
      // backdrop click / X remove the modal without touching the buttons —
      // resolve false so awaiting callers don't hang forever
      m.parentElement.addEventListener("click", (e) => {
        if (e.target === m.parentElement || e.target.closest("[data-close]")) resolve(false);
      }, true);
    });
  }

  /* ---------- command palette ---------- */
  let commands = [];
  function registerCommands(list) { commands = commands.concat(list); }

  function openPalette() {
    const root = document.getElementById("palette-root");
    if (root.childElementCount) return;
    let sel = 0;
    const back = el(`<div class="modal-backdrop" style="align-items:flex-start;justify-content:center">
      <div class="palette"><input placeholder="Type a command or page…" autofocus>
      <div class="opts"></div></div></div>`);
    const input = back.querySelector("input");
    const opts = back.querySelector(".opts");

    function render(filter = "") {
      const f = filter.toLowerCase();
      const list = commands.filter((c) => c.label.toLowerCase().includes(f)).slice(0, 12);
      sel = Math.min(sel, Math.max(0, list.length - 1));
      opts.innerHTML = list.map((c, i) =>
        `<div class="opt ${i === sel ? "sel" : ""}" data-i="${i}">${icon(c.icon || "zap", 14)}
         <span>${esc(c.label)}</span>${c.kbd ? `<span class="kbd">${c.kbd}</span>` : ""}</div>`).join("")
        || `<div class="empty">no matching command</div>`;
      opts.querySelectorAll(".opt").forEach((o) => {
        o.onclick = () => { run(list[+o.dataset.i]); };
      });
      return list;
    }
    function run(cmd) {
      back.remove();
      cmd.run && cmd.run();
    }
    input.addEventListener("input", () => { sel = 0; render(input.value); });
    input.addEventListener("keydown", (e) => {
      const list = render(input.value);
      if (e.key === "ArrowDown") { sel = Math.min(sel + 1, list.length - 1); render(input.value); e.preventDefault(); }
      else if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); render(input.value); e.preventDefault(); }
      else if (e.key === "Enter" && list[sel]) { run(list[sel]); }
      else if (e.key === "Escape") back.remove();
    });
    back.addEventListener("click", (e) => { if (e.target === back) back.remove(); });
    root.appendChild(back);
    render();
    input.focus();
  }

  /* ---------- misc ---------- */
  function fmtBytes(n) {
    if (n == null) return "—";
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(2) + " MB";
  }
  function fmtTime(ts) {
    return new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
  }
  function toggleSwitch(id, checked) {
    return `<label class="switch"><input type="checkbox" id="${id}" ${checked ? "checked" : ""}><span class="track"></span></label>`;
  }

  return { esc, el, toast, icon, modal, confirmModal, registerCommands, openPalette,
           fmtBytes, fmtTime, toggleSwitch };
})();
