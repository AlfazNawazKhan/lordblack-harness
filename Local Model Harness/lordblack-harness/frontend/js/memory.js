/* Memory page: documents CRUD, upload, retrieval tester. */
(() => {
  const { esc, toast, confirmModal, fmtBytes } = UI;
  let selected = null;

  async function render() {
    try {
      const { documents } = await API.get("/api/memory/documents");
      const list = document.getElementById("doc-list");
      document.getElementById("doc-count").textContent = documents.length;
      list.innerHTML = documents.length ? "" : `<div class="empty">No notes yet.<br>Add one or upload .md files — your local models will search them automatically.</div>`;
      for (const d of documents) {
        const item = UI.el(`<div class="doc-item ${d.id === selected ? "sel" : ""}">
          <div class="d-title">${esc(d.title)}</div>
          <div class="d-meta"><span>${d.chunks} chunks</span><span>${fmtBytes(d.size)}</span>
          <span>${new Date(d.updated * 1000).toLocaleDateString()}</span></div></div>`);
        item.onclick = () => { selected = d.id; openDoc(); };
        list.appendChild(item);
      }
      const st = await API.get("/api/memory/status");
      document.getElementById("mem-stats-row").innerHTML =
        `<span class="tag cyan">${esc(st.backend_detail)}</span>
         <span class="tag">${st.chunks} chunks</span>
         <span class="tag">${st.documents} docs</span>`;
      const tag = document.getElementById("mem-mode-tag");
      if (tag) tag.textContent = st.backend + " · " + st.chunks + " chunks";
    } catch (e) {
      document.getElementById("doc-list").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    }
  }

  async function openDoc() {
    if (!selected) return;
    try {
      const d = await API.get("/api/memory/documents/" + selected);
      document.getElementById("doc-title").value = d.title;
      document.getElementById("doc-content").value = d.content;
      document.getElementById("doc-meta").textContent =
        "id " + d.id + " · updated " + new Date(d.updated * 1000).toLocaleString();
    } catch (e) { toast(e.message, "error"); }
  }

  document.getElementById("btn-new-doc").onclick = () => {
    selected = null;
    document.getElementById("doc-title").value = "";
    document.getElementById("doc-content").value = "";
    document.getElementById("doc-meta").textContent = "new note — save to index";
    document.getElementById("doc-title").focus();
  };

  document.getElementById("btn-save-doc").onclick = async () => {
    const title = document.getElementById("doc-title").value.trim() || "Untitled note";
    const content = document.getElementById("doc-content").value;
    if (!content.trim()) return toast("Nothing to save", "warn");
    try {
      if (selected) await API.put("/api/memory/documents/" + selected, { title, content });
      else { const d = await API.post("/api/memory/documents", { title, content }); selected = d.id; }
      toast("Saved & queued for indexing");
      render();
    } catch (e) { toast(e.message, "error"); }
  };

  document.getElementById("btn-del-doc").onclick = async () => {
    if (!selected) return toast("Select a note first", "warn");
    if (!await confirmModal("Delete note", "Remove this note and its chunks from memory? This cannot be undone.", "Delete")) return;
    await API.del("/api/memory/documents/" + selected);
    selected = null;
    document.getElementById("doc-title").value = "";
    document.getElementById("doc-content").value = "";
    render();
  };

  document.getElementById("btn-upload-doc").onclick = () => document.getElementById("upload-input").click();
  document.getElementById("upload-input").onchange = async (e) => {
    if (!e.target.files.length) return;
    const fd = new FormData();
    for (const f of e.target.files) fd.append("files", f);
    try {
      const { added } = await API.upload("/api/memory/upload", fd);
      toast(`Indexed ${added.length} file(s)`);
      render();
    } catch (err) { toast(err.message, "error"); }
    e.target.value = "";
  };

  document.getElementById("btn-reindex").onclick = async () => {
    const r = await API.post("/api/memory/reindex");
    toast(`Reindexed ${r.reindexed} document(s)`); render();
  };

  document.getElementById("btn-test-search").onclick = async () => {
    const q = prompt("Test query:");
    if (!q) return;
    const box = document.getElementById("search-results");
    box.style.display = "";
    box.innerHTML = `<div class="empty">searching…</div>`;
    try {
      const { results } = await API.post("/api/memory/search", { query: q, k: 6 });
      box.innerHTML = results.length ? results.map((r) =>
        `<div class="sr"><div class="src">${esc(r.doc_title)}${r.heading ? " › " + esc(r.heading) : ""} · score ${r.score}</div>
         <div class="txt">${esc(r.text.slice(0, 220))}…</div></div>`).join("")
        : `<div class="empty">no matches</div>`;
    } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  };

  window.MemoryPage = { render };
})();
