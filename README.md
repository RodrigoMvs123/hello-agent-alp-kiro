# Hello Agent

> A minimal ALP-compliant agent implementing [Agent Load Protocol v0.9.0](https://github.com/RodrigoMvs123/agent-load-protocol).
> Lives in GitHub, deploys automatically via GitHub Actions, secrets managed by GitHub — no manual config.

---

## What's inside

```
hello-agent-alp-kiro/
├── agent.alp.json                   ← ALP v0.9.0 Agent Card (runtime.deploy block)
├── server.py                        ← FastAPI server — /mcp, /agent, /tools, /chat, /health
├── requirements.txt
├── render.yaml                      ← Render deploy config (includes ffmpeg install)
├── .env                             ← Local secrets (git-ignored)
├── .env.example                     ← Safe template to copy
├── kiro-mcp.json                    ← Paste into .kiro/settings/mcp.json
├── .gitignore
├── knowledge/
│   └── alp-docs.json                ← ALP knowledge base (19 entries, in-memory semantic search)
├── db/
│   └── migrations/
│       └── 001_knowledge.sql        ← Supabase schema: pgvector table + match_knowledge RPC
└── .github/
    └── workflows/
        └── deploy.yml               ← GitHub Actions deploy workflow
```

---

## Tools

| # | Tool | Description |
|---|---|---|
| 1 | `greet` | Greet a user by name |
| 2 | `echo` | Echo back any text |
| 3 | `get_agent_card` | Return the full ALP Agent Card JSON |
| 4 | `chat` | Send a message to Gemini AI and get a response |
| 5 | `search_knowledge` | Semantic search over the in-memory ALP knowledge base (alp-docs.json) |
| 6 | `web_search` | Search the live web using Serper (Google Search API) |
| 7 | `remember` | Store a key-value memory for the current session |
| 8 | `recall` | Retrieve a stored memory by key (or all memories for the session) |
| 9 | `forget` | Delete a specific memory key or clear the entire session |
| 10 | `search_knowledge_db` | Semantic search over Supabase (pgvector) — covers all ingested content |
| 11 | `ingest_media` | **Analyzer pipeline** — routes by file type: audio/video → Whisper, PDF → pdfplumber, DOCX → python-docx, image → Gemini Vision, text/md → direct read. Chunks, embeds, stores in Supabase. |
| 12 | `research_topic` | Longitudinal topic research — loads existing entity state, searches the web, matches results to known entities, ingests only new/changed evidence into `topic_events`. |
| 13 | `summarize_topic_updates` | Weekly digest — compares each entity's current status to its prior state, produces a structured report with BASELINE / STATUS CHANGES / NO CHANGE / UNVERIFIED sections. |

---

## Multi-Modal Analyzer Pipeline (`ingest_media`) — v2.0.0

Every file type goes through **Input → Classify → Extract → Chunk → Embed → Store**.

| File Type | Extension(s) | Extractor |
|---|---|---|
| Audio | `.mp3 .wav .m4a .ogg .flac` | Groq Whisper (direct if ≤24MB, pydub-split if larger) |
| Video | `.mp4 .mov .avi .mkv .webm` | ffmpeg extracts audio → Groq Whisper |
| PDF | `.pdf` | `pdfplumber` (per-page text) |
| Word | `.docx` | `python-docx` (paragraphs + tables) |
| Image | `.jpg .jpeg .png .webp` | Gemini Vision (OCR + description) |
| Plain text | `.txt .md` | direct read |

After extraction, all content is split into **400-word chunks with 50-word overlap**, embedded with Gemini, and upserted into Supabase `knowledge_chunks`.

```bash
# Ingest a PDF
curl -X POST http://localhost:8000/tools/ingest_media \
  -H "Content-Type: application/json" \
  -d '{"input": {"file_path": "/tmp/report.pdf", "source_name": "Q1 Report"}}'

# Ingest a video from Google Drive
curl -X POST http://localhost:8000/ingest-url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://drive.google.com/file/d/FILE_ID/view", "source_name": "Demo Video"}'

# Search all ingested content
curl -X POST http://localhost:8000/tools/search_knowledge_db \
  -H "Content-Type: application/json" \
  -d '{"input": {"query": "what did the speaker say about agents?"}}'
```

Adding a new file type later only requires:
1. A new `extract_*` function
2. One entry in `classify_file()`
3. One entry in the `EXTRACTORS` dict

---

## GitHub-native deploy (recommended)

This repo follows the ALP v0.9.0 GitHub-native pattern.
Secrets stay in GitHub. Deployment is automatic. No manual server setup.

### Step 1 — Fork this repo

Fork or clone into your own GitHub account.

### Step 2 — Add GitHub Secrets

Go to your repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret | What it is |
|---|---|
| `GEMINI_API_KEY` | Google Gemini API key — free at [aistudio.google.com](https://aistudio.google.com) |
| `RENDER_API_KEY` | Render API key — Render dashboard → Account → API Keys |
| `RENDER_SERVICE_ID` | Render service ID — visible in the Render dashboard URL |
| `SERPER_API_KEY` | Serper API key — free at [serper.dev](https://serper.dev) (2,500 free queries) |
| `SUPABASE_URL` | Supabase project URL — Project Settings → API |
| `SUPABASE_KEY` | Supabase service_role key — Project Settings → API (use service_role, not anon, to bypass RLS) |
| `GROQ_API_KEY` | Groq API key — free at [console.groq.com](https://console.groq.com) |

### Step 3 — Set up Supabase (for `search_knowledge_db` and `ingest_media`)

1. Create a free project at [supabase.com](https://supabase.com)
2. Go to **SQL Editor** → paste the contents of `db/migrations/001_knowledge.sql` → Run
   - Schema uses `vector(3072)` to match `gemini-embedding-001` output dimensions
   - No ivfflat/hnsw index — both are limited to 2000 dims max; sequential scan used instead
3. In a new SQL query, run: `ALTER TABLE knowledge_chunks DISABLE ROW LEVEL SECURITY;`
4. Copy `Project URL` and `service_role` key from **Project Settings → API**

### Step 4 — Create the Render service

1. Go to [render.com](https://render.com) → New → Web Service
2. Connect your GitHub repo
3. Render uses the `Dockerfile` which installs `ffmpeg` and all dependencies automatically
4. Add all environment variables in the Render dashboard
5. Copy the service ID from the URL → add as `RENDER_SERVICE_ID` GitHub Secret

### Step 5 — Push to main

The deploy workflow triggers automatically on every push to `main` that touches `agent.alp.json`, `server.py`, or `requirements.txt`.

You can also trigger it manually: **Actions → Deploy ALP Agent → Run workflow**

### Step 6 — Load in Kiro

Once deployed:

1. Open Kiro
2. Connect GitHub MCP (one-time OAuth — Kiro will prompt you)
3. Say: `"Load my agent from github.com/YOUR-USERNAME/hello-agent-alp-kiro"`
4. Kiro reads `agent.alp.json` via GitHub MCP
5. Kiro triggers the deploy workflow
6. GitHub Actions deploys with secrets injected
7. Kiro connects to `/mcp` — chat with your agent

---

## Run locally

```bash
git clone https://github.com/RodrigoMvs123/hello-agent-alp-kiro
cd hello-agent-alp-kiro

pip install -r requirements.txt
cp .env.example .env        # then fill in all keys
python server.py
```

> **Note:** `ffmpeg` must be installed locally for video ingestion.
> Install with: `winget install ffmpeg` (Windows) or `brew install ffmpeg` (Mac)

Server starts at **http://localhost:8000** — open the dashboard for all links.

---

## Live Demo

| Endpoint | URL |
|---|---|
| Dashboard | https://hello-agent-alp-kiro.onrender.com/ |
| Health | https://hello-agent-alp-kiro.onrender.com/health |
| Agent Card | https://hello-agent-alp-kiro.onrender.com/agent |
| MCP | https://hello-agent-alp-kiro.onrender.com/mcp |
| Chat UI | https://hello-agent-alp-kiro.onrender.com/chat-ui |

---

## Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Dashboard |
| `GET /health` | `{"status": "ok", "alp_version": "0.9.0"}` |
| `GET /agent` | Full Agent Card JSON |
| `GET /persona` | System prompt for any runtime |
| `GET /agents` | All hosted agent cards |
| `GET /tools` | Tool list (Claude Code / Claude Desktop) |
| `POST /tools/{name}` | Execute any of the 11 tools |
| `POST /upload` | Upload audio/video file directly (multipart/form-data) |
| `POST /ingest-url` | Ingest audio/video from a public URL or Google Drive share link |
| `GET /mcp` | MCP SSE stream (Kiro) |
| `POST /mcp` | MCP JSON-RPC receiver (Kiro) |
| `POST /chat` | Gemini chat API |
| `GET /chat-ui` | Chat UI in the browser |
| `GET /logs` | Last 50 log entries |

---

## Connect to Kiro

Paste into `.kiro/settings/mcp.json`:

```json
{
  "mcpServers": {
    "hello-agent": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

For the deployed version replace the URL with `https://hello-agent-alp-kiro.onrender.com/mcp`.

---

## How secrets stay safe

- GitHub Secrets are stored encrypted in your repo
- They are **never** returned by the GitHub API
- They are injected **only** into the workflow runner at runtime
- They never appear in `agent.alp.json`, in logs, or in any config file

---

## Topic Research Pipeline (`research_topic` + `summarize_topic_updates`) — v3.0.0

A longitudinal research system that tracks named entities over time, detects status changes, and produces a structured weekly digest. `brazil-rbc` is the first supported topic.

### What is a "topic"?

A topic is a named research domain (e.g. `brazil-rbc`). Each topic has:
- a set of **search terms** used to discover new evidence
- a **watchlist** of high-priority entity ids to monitor explicitly
- a **known_entities** map of entity ids → alias patterns for matching

### Entity-tracking model

Every ingested finding is stored as a row in `topic_events` with:

| Field | Description |
|---|---|
| `topic` | e.g. `brazil-rbc` |
| `entity` | normalized id, e.g. `sp-pl-107-2023`, or `discovery-XXXX` for unmatched results |
| `status` | extracted legislative/program stage: `introduced`, `sanctioned`, `operational`, `discovered`, etc. |
| `event_date` | date of the event if extractable from the source |
| `source_url` | canonical URL of the evidence |
| `source_type` | `official_primary` / `official_secondary` / `secondary` / `discovery` |
| `official_source` | boolean — true if from a government/legislative domain |
| `last_verified_at` | timestamp of last check |

Rows are **append-only** — prior states are never overwritten, preserving a full timeline per entity.

### Supabase setup

Run `db/migrations/002_topic_research.sql` in the Supabase SQL Editor (same as `001_knowledge.sql`). It creates the `topic_events` table and four indexes. It is **additive and safe** — it does not touch `knowledge_chunks`.

```sql
-- In Supabase SQL Editor:
-- 1. Paste and run db/migrations/001_knowledge.sql  (if not already done)
-- 2. Paste and run db/migrations/002_topic_research.sql
```

### Weekly GitHub Action

`.github/workflows/topic-research-weekly.yml` runs every **Monday at 08:00 UTC** (`cron: '0 8 * * 1'`). It also supports `workflow_dispatch` for manual runs.

Three steps: `research_topic` → `summarize_topic_updates` → deliver report.

Delivery channel is controlled by the `DELIVERY_CHANNEL` repo variable:

| `DELIVERY_CHANNEL` | Secrets required |
|---|---|
| `email` (default) | `RESEND_API_KEY`, `REPORT_EMAIL_TO` |
| `slack` | `SLACK_WEBHOOK_URL` |

### curl examples

```bash
# Run a research cycle for brazil-rbc
curl -X POST http://localhost:8000/tools/research_topic \
  -H "Content-Type: application/json" \
  -d '{"input": {"topic": "brazil-rbc"}}'

# Get the weekly digest
curl -X POST http://localhost:8000/tools/summarize_topic_updates \
  -H "Content-Type: application/json" \
  -d '{"input": {"topic": "brazil-rbc"}}'

# Re-research a specific window
curl -X POST http://localhost:8000/tools/summarize_topic_updates \
  -H "Content-Type: application/json" \
  -d '{"input": {"topic": "brazil-rbc", "since": "2026-09-01"}}'
```

### Known limitations

**a. JS-rendered legislative tracking pages** — `camara.leg.br/proposicoesWeb/fichadetramitacao` pages are JavaScript-rendered. `_fetch_page_text` receives only the JS scaffolding HTML, not the rendered tramitação table, so Gemini cannot extract a real legislative stage and falls back to `status: discovered` for those rows. Future fix: add a Playwright/Splash fetch path for JS-heavy domains.

**b. PDF-only bill text** — `sp-pl-107-2023`'s baseline status is `discovered` because the actual bill text lives in a PDF (`saopaulo.sp.leg.br/iah/fulltext/projeto/PL0107-2023.pdf`) which the page fetcher intentionally skips (PDFs are handled by the separate `ingest_media` pipeline). The diff logic will surface a real `CHANGED` entry once a fetchable HTML page reflects legislative movement.

> The original domain brief (`Prompt1.txt`) and build specification (`Prompt2.txt`, `Prompt3.txt`) live in `docs/` for reference.

---

## Protocol

Implements [ALP v0.9.0](https://github.com/RodrigoMvs123/agent-load-protocol/blob/main/releases/v0.9.0.md).

## License

MIT
This is the project README.
