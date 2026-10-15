# LordBlack Harness — Master Build Prompt

_A complete specification and build-instruction set for a local-first AI orchestration harness. Paste this document as the opening prompt to an AI coding agent (Claude Code or similar) to scaffold and build the project section by section._

## 0. How to Use This Prompt

Treat this document as the system brief for the entire build. Work through it section by section rather than all at once — each section is scoped so it can be handed to a coding agent as a standalone milestone. Where a choice was left open by the product owner, a recommended default is given; flag it for confirmation rather than silently locking in a decision that affects architecture (naming, embedding model, license).

## 1. Identity & Mission

Build **LordBlack Harness**, a fully local, privacy-first AI orchestration platform. It lets a user run one or more local LLMs through LM Studio, Ollama, or llama.cpp, gives those local models their own persistent memory, and — only when the user explicitly enables it — lets a local model delegate specific sub-tasks to external APIs (OpenRouter, Anthropic Claude, OpenAI) and fold the result back into its own answer. Everything must work completely offline once models are loaded; cloud APIs are an opt-in enhancement, never a dependency.

Primary design pressure: it must run acceptably on a low-end laptop — Intel i3 class CPU, no dedicated GPU (or a weak shared/integrated one), and as little as 4 GB of RAM. Every architectural choice below is made with that machine in mind, not a developer workstation.

## 2. Hard Technical Constraints

- **Backend:** Python. Use a lightweight async framework (FastAPI + uvicorn is the recommended default — small footprint, native streaming support for token-by-token chat). Avoid heavy ORMs or frameworks with large dependency trees.
- **Frontend:** Plain HTML, CSS, and JavaScript. No React/Vue/build step — the UI must run from static files served by the local backend, with no compile pipeline the user has to run.
- **Local runtimes to support:**
    - LM Studio, via its OpenAI-compatible REST endpoint (e.g. `http://localhost:1234/v1`)
    - Ollama, via its REST API (`http://localhost:11434`)
    - llama.cpp, via `llama-cpp-python` bindings or the `llama.cpp` server's REST API All three should be normalized behind one internal "LocalModelProvider" interface so the rest of the app never needs to know which runtime is active.
- **Packaging:** clone-or-download-zip-and-run. No Docker requirement, no Node build step. A single launcher script (`run.sh` / `run.bat` or `python app.py`) should start the backend and open the UI in the user's default browser — this replaces the literal "open index.html" instruction, since a Python backend has to be running for the app to function; `index.html` is the page that script serves.
- Must function with zero internet access once local models are downloaded and the app is launched.

## 3. Core Feature Set

1. **Local runtime connectivity** — connect to LM Studio, Ollama, or llama.cpp as the primary inference engine.
2. **Python backend / HTML-CSS-JS frontend**, as above.
3. **Low-resource operation** — must remain usable on i3 / no-GPU / 4–8 GB RAM hardware (see §4).
4. **Harness Memory** — a local, persistent knowledge store the model can search instead of holding everything in its context window. Users can add, edit, delete, and upload text/markdown files into it. (§6)
5. **External API delegation** — a local model can decide to call an external API (e.g. a free OpenRouter model), pick a suitable model from what's available, get its output, extract only the relevant part, and surface that to the user. (§7)
6. **Local-to-local model chat** — two locally hosted models can converse with each other (e.g. a planner model talking to a worker model).
7. **Device & folder access** — user grants the harness access to specific folders, plus optional internet and Bluetooth access. (§8)
8. **Fully offline capability** for all core (non-cloud) features.
9. **Agent builder** — users can define, run, and locally host their own AI agents built on local models. (§9)
10. **Setup documentation** — an auto-maintained `README.md` with install instructions.

## 4. Low-Resource Performance Budget

Design around a 4–8 GB RAM / integrated-or-no-GPU / i3 CPU baseline:

- Stream all model output (SSE or chunked HTTP) rather than buffering full responses.
- Don't bundle a heavy vector database. A personal memory store of markdown notes is small enough (typically thousands, not millions, of chunks) that SQLite with a brute-force cosine-similarity scan over stored embeddings is fast enough and far lighter than Chroma/Milvus/Weaviate.
- Use a small quantized local embedding model (e.g. an ONNX-exported MiniLM-class model, ~80–100 MB) for memory search; fall back to keyword/BM25 search if even that's too heavy for the device, and let the user pick in Settings.
- Avoid polling-heavy JavaScript; use WebSockets or SSE instead of frequent `setInterval` fetches for live panels (AI Activity trace, system stats).
- Lazy-load UI panels (don't parse the whole workspace file tree eagerly on large folders).
- Keep default context window and memory-retrieval chunk counts conservative, configurable upward for users with more RAM.

## 5. Local Model Runtime Integration

Build a `LocalModelProvider` abstraction with one implementation per runtime (LM Studio, Ollama, llama.cpp), each exposing: `list_models()`, `chat(messages, stream=True)`, `embed(text)` (where supported), and `health_check()`. The Settings UI's "LM Studio / Local Hub" card (per the UI mock) should call `health_check()` to show connection status and auto-detect available models (as in the mockup's "Detection active: Found Qwen (7B/14B) & Gemma models").

## 6. Harness Memory

This is the system that stops a local model from exhausting its context window:

- Users manage memory entries (add / edit / delete / upload) as plain text or markdown files through a dedicated Memory panel.
- On save, the backend chunks each file, embeds the chunks (§4), and stores them with metadata (source file, heading path, timestamp).
- When a local model needs context, the backend runs a similarity search over memory and injects only the top matching chunks into the prompt — the "Memory Retrieval" panel in the UI mock (showing short bullet facts like "Project Stack: Python, FastAPI, Pydantic") is exactly this: a visible, user-auditable trace of what was pulled from memory for the current turn.
- **Hard boundary (see §10):** only requests flagged as going to a local model may query this store. Requests destined for an external/cloud model must never receive raw memory content.

## 7. Multi-Model Orchestration & Routing

Two distinct capabilities:

- **Delegation to external APIs:** when the user has connected a provider (OpenRouter, Claude, GPT), the local model can treat other models as tools it can call. For OpenRouter specifically, since it mixes free and paid models, the router should maintain a small catalog of currently-free models and prefer those unless the user has opted into paid usage. The flow: local model identifies a sub-task it wants outside help with → selects a suitable (free, by default) external model → calls it → extracts only the information actually needed → folds that back into its own answer to the user. The "AI Activity" trace panel in the UI mock should log each of these steps (routing decision, external call, response extraction) exactly as shown.
- **Local-to-local chat:** allow two locally hosted models (potentially from different runtimes) to exchange messages as part of solving a task — e.g. one model drafts, another critiques — orchestrated by the backend, with the exchange visible to the user if they expand the trace.

## 8. Device & System Access

- **Folder access:** the user explicitly grants one or more folders through the UI (the "FILE ACCESS" panel in the main mock). Access is scoped to only the granted folders — never the whole filesystem — and each file can be toggled in/out of the model's working context individually (matching the per-row toggle switches in the mockup).
- **Internet access:** a global, user-controlled toggle. When off, all external-API and update-check code paths must be fully inert, not just discouraged.
- **Bluetooth and other device capabilities:** treat as an advanced, opt-in module (e.g. via `bleak` for Bluetooth on Python) rather than a core dependency — most users on low-end laptops won't need it, and it shouldn't be in the critical path for startup or chat.
- Every one of these permissions should be individually grantable, revocable, and visible in Settings → Security, consistent with the Privacy Policy below.

## 9. Agent Builder & Local Hosting

- Agents are defined as a simple manifest (YAML or JSON): name, system prompt, assigned model(s), allowed tools (memory, folder access, external API delegation, etc.), and trigger type (manual chat, scheduled, or exposed as an endpoint).
- The harness can host a built agent locally, exposing it at something like `http://localhost:<port>/agents/<name>` so other local tools or scripts can call it.
- Hosted agents should run in a constrained execution context (limited tool access, rate-limited) since they can be reached by other local processes.

## 10. Privacy & Security Model (non-negotiable)

Directly from the project's Privacy Policy:

- All user data stays on the user's device by default — no implicit uploading, syncing, or telemetry.
- External/cloud models (Claude, GPT, OpenRouter models) must never be able to read, list, or search the Harness Memory store. This should be enforced at the request-routing layer, not just by convention — e.g. the function/tool that reads memory simply isn't available in the toolset passed to an external-API call.
- Only local models (via the `LocalModelProvider` interface) may query memory.
- API keys for external providers are stored encrypted at rest, never in plaintext config files, and masked in the UI (shown truncated, e.g. `sk-ant-••••••••`, with a reveal/hide toggle and separate Verify/Modify actions — exactly as in the Settings mock).
- Any time data would leave the device (an external API call), it should be something the user can see happened — the AI Activity trace serves this transparency role.

## 11. UI/UX Specification

**Name:** LordBlack Harness. (The source files use three different spellings/spacings — "LordBack Harness," "LordBlackharness," "LordBlack Harness" — pick one canonical form before locking in branding assets; see §15.)

**Visual language:** dark theme, near-black backgrounds, a single cyan/teal accent color for active states and highlights, monospace font for code and logs, clean sans-serif for UI chrome.

**Primary workspace (3-column layout):**

- _Left — File Access & Memory:_ an expandable file tree of connected folders with a per-file access toggle, and below it a Memory Retrieval panel showing the specific facts pulled into the current context.
- _Center — Chat & Workspace:_ shows the currently active model(s) (local + external, labeled), the conversation thread, inline code blocks with "Apply to File" / "View Diff" actions when the model proposes an edit, an expandable "Live Agent Run Trace," and an input bar with a token counter plus RAG Memory / Agentic Mode toggles.
- _Right — AI Activity:_ a live, timestamped trace of the pipeline (input parsed → local routing/intent → memory search → external API call if any → response extraction → final answer), each step showing status and latency, with a raw log console beneath it.

**Top bar:** app name + version + online/offline status, a global search/command bar, a latency indicator, and standard window controls.

**Bottom status bar:** a small live CPU/RAM usage sparkline, numeric CPU / VRAM / RAM readouts, and the name of the currently active agent.

**Settings screen:** left-hand nav (General, Model & API Integrations, Node Networks, System & Logs, Database, Security). The Model & API Integrations page has an "LM Studio / Local Hub" card (server URL field, Connect/Disconnect/Test Connection, auto-detected model list), a "Model Mapping & Nodes" list (toggle which models/memory nodes are active), and a "Cloud API Providers" panel with one card per provider (Anthropic Claude, OpenAI GPT, OpenRouter) showing connection status and masked key fields with Verify/Modify.

## 12. Installation & Onboarding Flow

1. Clone or download-and-unzip the repository.
2. Run the launcher script, which starts the local backend and opens the UI (serving `index.html`) in the browser.
3. Go to Settings → Model & API Integrations, paste the server URL shown in LM Studio's Developer mode, click Connect.
4. Start chatting with the connected local model.
5. Optionally add API keys for Claude, GPT, or OpenRouter to enable external delegation.

## 13. README.md Requirements

Maintain a `README.md` covering: what the project is, hardware/software prerequisites, the install flow above (with the LM Studio screenshot step called out explicitly), first-run troubleshooting (connection fails, no models detected), a short privacy summary (what never leaves the device), and the repo's folder structure.

## 14. Non-Goals for v1

- No mobile app or mobile-optimized UI.
- No multi-user accounts or team features.
- No cloud-hosted / SaaS deployment — this is a local-run tool only.

Canonical name/casing: "LordBlack Harness," "LordBlackharness," or "LordBack Harness"?: LordBlack Harness

Embedding model for memory: is a small local model acceptable, or should memory fall back to keyword search by default on the lowest-spec machines?: bassed on the device capability

License: open source (and which license), or closed?: MIT License

"Node Networks" appears as a Settings tab in the mockups but isn't defined in the feature docs — what should it actually control?: i don't know. handle it in a suitable way. (e.g. we can add agen building dashboard there

Agent hosting scope: should hosted agents be reachable only from `localhost`, or also from other devices on the same LAN?: localhost and lan both.