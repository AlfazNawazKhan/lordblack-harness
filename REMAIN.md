# LordBlack Harness — Remaining Tasks

_Last reviewed: 2026-10-09 (post-build code review + API smoke tests)_

> **2026-10-14:** independent audit pass — 7 major security/correctness bugs + 8 mid-tier issues fixed. Full list: [CHANGES-2026-10-14.md](CHANGES-2026-10-14.md).

Status legend: ☐ todo · ◐ partial · ☑ done/verified

---

## 1. Verified working (smoke-tested via live API)

- ☑ Backend boots, serves UI (`/`, `/js/*`, `/css/*` → 200), `/api/health` OK
- ☑ Memory: create → chunk → BM25 search roundtrip (`/api/memory/*`)
- ☑ Folder sandbox: grant, tree (incl. **subfolders**), read, apply-with-backup, diff, and **403 outside granted roots** (path traversal blocked)
- ☑ Path normalization: `C:/x`, `C:\x`, Git-Bash `/c/x`, `~` all accepted
- ☑ Agents: create/list/hosted-info/delete; hosted `/agents/<slug>/chat` returns clean error when no model loaded; rate-limit code in place
- ☑ Chat SSE: clean `event: error` when no runtime/model available
- ☑ API keys: encrypted at rest with Windows DPAPI (`dpapi1:` ciphertext on disk, no plaintext), masked in `/api/config`
- ☑ Internet toggle hard gate: `/api/config/verify/*` → 403 when internet off

## 2. P0 — known bugs to fix before daily use ✅ ALL FIXED (2026-10-10)

- ☑ **`Workspace.updateModelChips` is never exported** — FIXED: attached via `S.updateModelChips = updateModelChips` in `workspace.js`; model chips now refresh whenever config reloads.
- ☑ **Duplicate `WorkspaceBoot()` on app boot** — FIXED: removed the explicit call in `app.js` boot IIFE (page routing already triggers it) and added a `logStreamStarted` re-entry guard in `startLogStream` (no more duplicate raw-log SSE streams / connection leak on workspace revisits).
- ☑ **`encrypt_secret` could fall back to `plain:`** — FIXED: `_resolve_storage_mode()` now runs at module import in `config.py`. Verified: a fresh process with no `get_config()` call encrypts as `dpapi1:` ciphertext.
- ☑ **Unicode paths crash the workspace file tree** — FIXED: `btoa(path)` replaced with a codepoint-hash `pathId()` in `workspace.js` (and the same crash-class `btoa(model)` in `settings.js` replaced with a safe slug). No `btoa` on paths/names remains.
- ☑ **Newly created scheduled agents never started scheduling** — FIXED: `POST /api/agents` now calls `SCHEDULER.schedule()` when `trigger.type == "scheduled"`. Verified live: 1-minute-interval agent fired exactly 60 s after creation.

## 3. P1 — incomplete features & UX gaps ✅ ALL DONE (2026-10-10)

- ☑ **Visual browser pass** — all 7 pages opened in a real browser: zero console errors, all panels render, live status bar verified, real chat sent through the UI with streaming + memory-grounded reply + working codeblock buttons. Extra bugs found & fixed during the pass: duplicate runtime entries (deduped + config cleaned), `asyncio.Queue(maxlen=)` crash that killed the stats/log SSE streams, hash navigation not updating pages on back/forward, status-bar text clipping.
- ☑ **Open-data-dir** — `POST /api/system/open-data-dir` (os.startfile/xdg-open) + working button in Settings → System.
- ☑ **OpenRouter catalog via backend proxy** — `POST /api/cloud/openrouter/catalog`, internet-gated; browser no longer calls external hosts directly.
- ☑ **Per-provider cloud model pickers** — "Pick model" buttons (Settings + pipeline reads `cloud.<provider>.model`); hardcoded ids only as fallback defaults.
- ☑ **Ollama list_models TTL cache** (30 s) + `force=True` on Test Connection.
- ☑ **Duet labeling** — `done.answered_by` names the partner model; chat bubble shows "Answered by …".
- ☑ **Apply-to-File target picker** — modal folder chooser when several folders are granted.
- ☑ **Hosted-agent bearer tokens** — optional `hosting.token` field (agent editor); enforced with 401 on mismatch.
- ☑ **Startup embedding probe** — one live probe at boot so the Memory panel shows the real backend on first paint.
- ☑ **run.bat first-run verified** — clean venv + pip install + app boot tested (`.venv-test` deleted afterwards).
- ☑ **Cleanup** — test-folder/, server-test.log, venv-test removed; sample "Project Stack" memory doc + "Note Tidier" agent kept as demo content.

## 4. P2 — needs YOUR action (see USER.md for step-by-step instructions)

These are not code bugs — they need a real local model / API keys / your hardware.
**→ All moved to [USER.md](USER.md) with detailed checklists.** Summary:
- ☐ Connect your real LM Studio model and chat (streaming, RAG, file proposals)
- ☐ Agentic delegation with a free OpenRouter key · Duet with two local models
- ☐ Agent tool-loop runs · scheduled agents · LAN-hosted agent
- ☐ Runtime embeddings (nomic-embed-text) or MiniLM download · Bluetooth (bleak)
- ☐ llama.cpp server / embedded llama.cpp runtimes (untested, code present)

## 5. Remaining known bugs (categorized)

### 1. Major — none known open
All Major bugs found during testing are fixed (see P0/P1 + FIX.md section).

### 2. Mid Tier
- ☐ Duet mode streams the partner's tokens without a per-token "draft vs refined" visual split (final label is correct).
- ☐ Model-switcher and Node-map don't auto-refresh if a runtime loads/unloads models while a page is open (manual refresh needed).
- ☐ Agentic intent probe adds one extra model call per message when Agentic Mode is on (cost on slow CPUs; it's opt-in per message).

### 3. Minor (accepted, ignore for v1)
- ☐ Memory-panel chunk previews are plain text (no markdown rendering).
- ☐ Settings → Model Mapping can show duplicate toggle ids if two runtimes host the same model name.
- ☐ Attachments aren't persisted in localStorage chat history.
- ☐ VRAM shows "n/a" without an NVIDIA GPU + pynvml.
- ☐ Log console capped at ~400–600 lines (older lines drop off, file log has everything).
- ☐ "Test retrieval" uses a browser prompt() dialog.

## 5b. Post-release fixes (user-reported, ✅ 2026-10-11)
- ☑ **No "New Chat" option** — added a **+ New Chat** button in the CHAT & WORKSPACE header and a "New chat" command (Ctrl+K). Starting a new chat aborts any in-flight reply, clears the thread + localStorage history + run trace + memory-retrieval panel (memory notes are untouched), and refocuses the input. Verified in-browser: 10 messages → clean welcome state.

## 6. FIX.md fixes (user-reported) ✅ 2026-10-10
- ☑ **"anthropic (External)" chip without a connected key** — root cause: a leftover fake test key; removed. Additionally external chips now appear ONLY when you explicitly allow the provider in Settings → Model Mapping & Nodes (manual toggle), never just for storing a key.
- ☑ **Dead tick/copy/edit buttons on code blocks** — the final markdown render after streaming wiped the click handlers; handlers are now re-wired after the stream completes (verified working in the real UI).
- ☑ **Local model couldn't create files** — system prompt now always tells the model it may create/edit files inside granted folders (with the folder list) and to output complete file content in `file="path"` tagged blocks; Apply flow verified.
- ☑ **"Laudk4" console banner** — replaced with a clean text banner; also added a friendly "port already in use" message instead of the raw WinError 10048.

## 7. P3 — nice to have / next version
- ☑ Conversation persistence across refresh (localStorage, last 60 messages, "Clear chat history" command)
- ☐ "View Diff"/"Apply" for brand-new files in subfolders that don't exist yet (backend already creates dirs — frontend edge cases)
- ☑ Model chips clickable → quick model switcher from the workspace
- ☐ Settings → Node Networks tab could embed the SVG node map
- ☑ Unit tests (21 passing: chunker, BM25, vector helpers, pipeline JSON/trim, agent parser, rate limiter, fs sandbox, key encryption)
- ☐ pyinstaller one-exe packaging (optional extra)
- ☐ Daily auto-refresh of the OpenRouter free catalog

## 8. Non-goals (per spec — do NOT build in v1)
- Mobile UI · multi-user/team · cloud/SaaS hosting · heavy vector DB · React/build step

---
### Status
**v0.8.4 feature-complete and verified end-to-end** (mock-model E2E: streaming, RAG injection, traces, file proposals, buttons, all 7 pages error-free, 21 unit tests). What remains is in USER.md — actions only you can do with real models/keys.
