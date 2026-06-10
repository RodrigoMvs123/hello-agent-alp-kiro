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
| 10 | `search_knowledge_db` | Semantic search over Supabase (pgvector) — covers ALP docs, audio, and video |
| 11 | `ingest_media` | Transcribe audio/video with Groq Whisper, chunk, embed with Gemini, store in Supabase |

---

## Media Ingestion Pipeline (`ingest_media`)

Two-stage chunking pipeline for audio and video files:

**Stage 1 — API size chunking (Whisper limit)**
- Splits audio into 10-minute segments with 30s overlap using `pydub`
- Temp files stored in `/tmp`, deleted after transcription

**Stage 2 — Semantic chunking (stored in DB)**
- Splits transcript into 400-word chunks with 50-word overlap
- Each chunk embedded with Gemini and upserted into Supabase

**Supported formats:** `.mp3` `.wav` `.m4a` `.ogg` `.flac` `.mp4` `.mov` `.avi` `.mkv` `.webm`

```bash
# Example: ingest an audio file
curl -X POST http://localhost:8000/tools/ingest_media \
  -H "Content-Type: application/json" \
  -d '{"input": {"file_path": "/tmp/recording.mp3", "source_name": "My Recording"}}'

# Example: search the ingested content
curl -X POST http://localhost:8000/tools/search_knowledge_db \
  -H "Content-Type: application/json" \
  -d '{"input": {"query": "what did the speaker say about agents?"}}'
```

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
| `SUPABASE_KEY` | Supabase anon key — Project Settings → API |
| `GROQ_API_KEY` | Groq API key — free at [console.groq.com](https://console.groq.com) |

### Step 3 — Set up Supabase (for `search_knowledge_db` and `ingest_media`)

1. Create a free project at [supabase.com](https://supabase.com)
2. Go to **SQL Editor** → paste the contents of `db/migrations/001_knowledge.sql` → Run
3. Copy `Project URL` and `anon` key from **Project Settings → API**

### Step 4 — Create the Render service

1. Go to [render.com](https://render.com) → New → Web Service
2. Connect your GitHub repo
3. Render detects `render.yaml` automatically (includes `ffmpeg` install)
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

## Protocol

Implements [ALP v0.9.0](https://github.com/RodrigoMvs123/agent-load-protocol/blob/main/releases/v0.9.0.md).

## License

MIT
This is the project README.
