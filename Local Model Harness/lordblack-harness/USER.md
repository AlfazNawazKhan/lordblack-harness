# USER.md — Your Action Checklist

The harness is built and verified with a mock model. Everything below is what **you** do to light it up with real models and keys. Follow the sections in order.

> Start the harness any time: double-click **`run.bat`** → browser opens at `http://127.0.0.1:8765`.
> (If you see "Port already in use", a previous instance is still running — close that console window first, or run `python app.py --port 8766`.)

---

## 1. Chat with your real LM Studio model (5 min)

1. Open **LM Studio**.
2. Go to the **Developer** tab (terminal icon, left sidebar) → make sure **Local Server** status toggle is **Running** (green).
3. Note the **"Reachable at"** URL — usually `http://127.0.0.1:1234`.
4. In the harness UI, open **Model Hub** (left rail) — your runtime should already show **CONNECTED** (your config already points at `127.0.0.1:1234`).
5. If no models are listed, click **Refresh models**, or load a model in LM Studio first (e.g. `qwen3-1.7b` — already downloaded on your machine).
6. Click the model pill you want → it becomes the **active chat model** (★).
7. Go to **workspace** and send a message. You should see:
   - streaming tokens, latency in the top bar,
   - pipeline steps in **AI Activity** (right panel),
   - "Answered by …" label under the reply.

**If it fails:** LM Studio → Developer → Server Settings → enable **Just-in-Time model loading**, or load a model manually. Then click **Test Connection** in Model Hub.

## 2. Memory (RAG) in real chat (already works — verify)

1. **memory** page → the sample "Project Stack" note is already indexed.
2. Add your own notes (**+ New note**) or upload `.md`/`.txt` files (**⬆ Upload**).
3. In workspace, keep the **◈ RAG Memory** chip on (it is on by default).
4. Ask something contained in your notes → the **MEMORY RETRIEVAL** panel (left column) shows exactly which chunks were pulled in, and the AI Activity trace shows the "Memory search" step with chunk count.

**Optional — better semantic search** (keyword BM25 is the current mode, which works fine):
- Easiest: in LM Studio, download and load an embedding model (search "nomic-embed-text" in LM Studio's model search, load it alongside). The harness auto-detects it on the next chat (Memory panel tag changes from `keyword` to `runtime embeddings`).
- Or fully local ONNX: Settings → General → **Download MiniLM (~90 MB)** — needs internet once. Then set *Embedding backend* to Auto or Local MiniLM.
- Lightest: do nothing — BM25 works on any machine.

## 3. File creation & edits by the model

1. **workspace → FILE ACCESS → +** — grant a folder, e.g. `C:\Users\USER\Desktop\Harness` (you already granted this one).
2. Ask the model: *"create index.html, styles.css and script.js for a landing page"*.
3. The reply contains code blocks tagged with the target file → click **✓ (Apply)** to write it (a `.lbk.bak` backup is kept) or **✎ (View Diff)** first.
4. Only granted folders are ever touched; the model is told which folders it may use.

## 4. External delegation via OpenRouter (optional, needs internet)

1. Get a free API key: <https://openrouter.ai/keys> → Create key.
2. Harness: **cloud** page → OpenRouter card → paste key → **Connect** (key is encrypted with Windows DPAPI before saving).
3. **Settings → Security → Internet access: ON** (nothing leaves the device while it's off).
4. **Settings → Model & API Integrations** → in *Model Mapping & Nodes* find the **OpenRouter API** row → toggle it **ALLOWED** (external chips/providers stay hidden until you do this — by design).
5. **cloud** page → **Refresh catalog** → pick a preferred free model or leave **Auto**.
6. In workspace, flip **⚡ Agentic Mode** on for a message that needs more firepower. Watch the trace: *routing → external API call → extraction → synthesis*. Only the composed sub-task is ever sent — never your memory or files.

Claude/GPT keys work the same way (Connect → Verify → Pick model → allow in Model Mapping).

## 5. Duet mode (two local models)

1. Load a second model in LM Studio (e.g. a 3B and a 1.7B).
2. **Model Hub** → click **◎ Use for Duet** on the runtime, pick the partner model (or Settings → General → *Duet mode partner*).
3. In workspace, flip **◎ Duet Mode** on. Model A drafts, model B refines; both steps show in the trace, and the reply is labeled "Answered by B".
> On 4 GB RAM use small models; expect slower responses (two generations per message).

## 6. Agents (Node Networks)

1. **agents** page → **+ New agent**. The sample **Note Tidier** agent already exists — click it, give it a task, **Run**.
2. Build your own: name, system prompt, model, tools (memory search/write, read files, list folders, external lookup, bluetooth), trigger.
3. **Host as endpoint**: enable in the editor → other local programs call:
   ```
   POST http://127.0.0.1:8765/agents/<slug>/chat   {"message": "..."}
   ```
   (Optional but recommended: set a **bearer token** in the editor; callers send `Authorization: Bearer <token>`.)
4. **Scheduled agents**: trigger = Scheduled + interval → runs in the background automatically (harness must be running).

## 7. LAN access (only on trusted networks)

1. Settings → General → **Allow LAN access** → restart the harness.
2. Other devices on your network open `http://<your-ip>:8765` (your IP is shown bottom-right of the status bar).
3. Set bearer tokens on hosted agents before doing this.

## 8. Bluetooth tool (optional)

1. `pip install bleak` (in the harness venv: `.venv\Scripts\pip install bleak`).
2. Settings → Security → **Bluetooth module: ON**.
3. Agents can now use the `bluetooth_scan` tool.

## 9. Untested runtimes (advanced, optional)

- **llama.cpp server**: run `llama-server -m model.gguf --port 8080` → Model Hub → **+ Add runtime manually** → type *llama.cpp server*, URL `http://127.0.0.1:8080`.
- **Embedded llama.cpp**: `pip install llama-cpp-python` → add runtime type *llama.cpp embedded* with a GGUF path. (Code present, not yet exercised — report issues in REMAIN.md.)

## 10. Quick sanity checklist after setup

- [ ] Health: top-right latency shows a number, status bar shows CPU/RAM
- [ ] Model Hub: runtime CONNECTED, model pill selected (★)
- [ ] Chat: streaming reply + AI Activity steps + no red errors
- [ ] Memory: MEMORY RETRIEVAL panel fills on a memory-relevant question
- [ ] Files: grant folder → ask for a file → Apply writes it (backup created)
- [ ] `python tests/test_core.py` → 21/21 pass

---

### Where your data lives (all local)
`data/config.json` (settings, encrypted keys) · `data/harness.db` (memory index) · `data/memory/*.md` (your notes) · `data/agents/` (agent manifests + run history) · `data/logs/harness.log`
