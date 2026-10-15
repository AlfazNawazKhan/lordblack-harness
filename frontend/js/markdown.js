/* Minimal safe markdown renderer: escape first, then format.
   Code blocks with a file="..." attribute get Apply/Diff actions. */
window.MD = (() => {
  function esc(s) {
    return String(s).replace(/[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function inline(s) {
    // escape first: model output / file content is untrusted, formatting is
    // applied on the escaped text so no raw HTML can reach innerHTML
    s = esc(s);
    s = s
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/(^|\W)\*([^*\n]+)\*(?=\W|$)/g, "$1<i>$2</i>");
    // links: only real web/mailto schemes survive; href is already escaped
    // (quotes are &quot;) so no attribute breakout is possible
    return s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, href) => {
      const url = href.replace(/&amp;/g, "&");
      if (/^(https?:\/\/|mailto:)/i.test(url))
        return `<a href="${href}" target="_blank" rel="noopener">${label}</a>`;
      return `${label} (${href})`;
    });
  }

  function render(src) {
    const blocks = [];
    // extract fenced code blocks first: ```lang file="path" ... ```
    src = src.replace(/```([\w+-]*)((?:\s+\w+="[^"]*")*)\n?([\s\S]*?)(?:```|$)/g,
      (m, lang, attrs, code) => {
        const file = (attrs.match(/file="([^"]+)"/) || [])[1] || "";
        const i = blocks.length;
        blocks.push({ lang, file, code });
        return `\u0000CB${i}\u0000`;
      });

    const lines = String(src).split("\n");
    let out = "", inUl = false, para = [];
    const flushPara = () => {
      if (para.length) { out += `<p>${inline(para.join(" "))}</p>`; para = []; }
    };
    for (const raw of lines) {
      const line = raw.replace(/^\s+/, "");
      const cb = line.match(/^\u0000CB(\d+)\u0000$/);
      if (cb) { flushPara(); if (inUl) { out += "</ul>"; inUl = false; }
        out += codeBlockHtml(blocks[+cb[1]]); continue; }
      if (!line) { flushPara(); if (inUl) { out += "</ul>"; inUl = false; } continue; }
      const h = line.match(/^(#{1,4})\s+(.*)/);
      if (h) { flushPara(); if (inUl) { out += "</ul>"; inUl = false; }
        out += `<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`; continue; }
      if (/^[-*•]\s+/.test(line)) {
        flushPara();
        if (!inUl) { out += "<ul>"; inUl = true; }
        out += `<li>${inline(line.replace(/^[-*•]\s+/, ""))}</li>`; continue;
      }
      if (inUl) { out += "</ul>"; inUl = false; }
      para.push(line);
    }
    flushPara();
    if (inUl) out += "</ul>";
    return out;
  }

  function codeBlockHtml(b) {
    const fileTag = b.file
      ? ` · <span class="cb-file">${UI.esc(b.file)}</span>` : "";
    // Save-as-file is on EVERY block: small models often skip the file=""
    // tag, and the user still needs a one-click way to write the code out.
    // Apply/Diff (in-place edit) only make sense when a target file is known.
    const actions = b.file
      ? `<button class="icon-btn" data-act="apply" title="Apply to file">${UI.icon("check", 13)}</button>
         <button class="icon-btn" data-act="diff" title="View diff">${UI.icon("edit", 13)}</button>` : "";
    return `<div class="codeblock" data-file="${UI.esc(b.file)}" data-code="${UI.esc(b.code)}">
      <div class="cb-head"><span>${UI.esc(b.lang || "text")}${fileTag}</span>
        <div class="cb-actions">
          <button class="icon-btn" data-act="copy" title="Copy">${UI.icon("copy", 13)}</button>
          <button class="icon-btn" data-act="save" title="Save as file">${UI.icon("folder", 13)}</button>${actions}
        </div></div>
      <pre>${UI.esc(b.code)}</pre></div>`;
  }

  /* wire code-block buttons inside a container */
  function wire(container, handlers) {
    container.querySelectorAll(".codeblock").forEach((cb) => {
      cb.querySelectorAll("[data-act]").forEach((btn) => {
        btn.onclick = () => {
          const act = btn.dataset.act;
          const code = cb.dataset.code, file = cb.dataset.file;
          if (act === "copy") {
            navigator.clipboard.writeText(code).then(() => UI.toast("Copied to clipboard"));
          } else if (act === "save" && handlers.onSave) {
            handlers.onSave(file, code);
          } else if (act === "apply" && handlers.onApply) {
            handlers.onApply(file, code);
          } else if (act === "diff" && handlers.onDiff) {
            handlers.onDiff(file, code);
          }
        };
      });
    });
  }

  return { render, wire, esc };
})();
