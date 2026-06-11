"""
ALP Hello Agent — server.py
Agent Load Protocol v0.9.0

Endpoints
---------
GET  /              → dashboard (HTML)
GET  /health        → {"status": "ok"}
GET  /agent         → Agent Card JSON
GET  /persona       → agent persona (v0.4.0 required)
GET  /agents        → all agent cards (v0.4.0 optional)
GET  /tools         → tool list (Claude Code / Claude Desktop)
POST /tools/{name}  → execute a tool
GET  /mcp           → MCP SSE stream (Kiro)
POST /mcp           → MCP JSON-RPC messages (Kiro)
GET  /logs          → recent log entries

Kiro MCP config (.kiro/settings/mcp.json)
-----------------------------------------
{
  "mcpServers": {
    "hello-agent": {
      "url": "http://localhost:8000/mcp"
    }
  }
}

Claude Code / Claude Desktop config
------------------------------------
{
  "mcpServers": {
    "hello-agent": {
      "url": "http://localhost:8000/tools"
    }
  }
}
"""

import asyncio
import json
import os
import logging
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import google.generativeai as genai
import groq
import httpx
import numpy as np
import supabase
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("alp-server")

AGENT_CARD_PATH = Path(os.getenv("AGENT_CARD_PATH", "agent.alp.json"))
PORT = int(os.getenv("PORT", "8000"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    _gemini = genai.GenerativeModel(GEMINI_MODEL)
else:
    _gemini = None

# ---------------------------------------------------------------------------
# Knowledge base — semantic search
# ---------------------------------------------------------------------------

KNOWLEDGE_PATH = Path("knowledge/alp-docs.json")
EMBEDDING_MODEL = "models/gemini-embedding-001"

_knowledge_entries: list[dict] = []
_knowledge_vectors: np.ndarray | None = None


def _load_knowledge() -> None:
    global _knowledge_entries, _knowledge_vectors
    if not KNOWLEDGE_PATH.exists():
        _log(
            "warning",
            f"Knowledge file not found at {KNOWLEDGE_PATH} — search_knowledge disabled",
        )
        return
    if not GEMINI_API_KEY:
        _log("warning", "GEMINI_API_KEY not set — search_knowledge disabled")
        return
    with open(KNOWLEDGE_PATH) as f:
        _knowledge_entries = json.load(f)
    _log(
        "info",
        f"Generating embeddings for {len(_knowledge_entries)} knowledge entries...",
    )
    vectors = []
    for entry in _knowledge_entries:
        text = f"{entry['question']} {entry['answer']}"
        result = genai.embed_content(
            model=EMBEDDING_MODEL, content=text, task_type="retrieval_document"
        )
        vectors.append(result["embedding"])
    _knowledge_vectors = np.array(vectors, dtype=np.float32)
    _log("info", f"Knowledge base ready — {len(_knowledge_entries)} entries embedded")


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a_norm = a / (np.linalg.norm(a) + 1e-10)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-10)
    return b_norm @ a_norm


def _search_knowledge(query: str, top_k: int = 3) -> list[dict]:
    if _knowledge_vectors is None or len(_knowledge_entries) == 0:
        raise RuntimeError("Knowledge base not loaded")
    result = genai.embed_content(
        model=EMBEDDING_MODEL, content=query, task_type="retrieval_query"
    )
    query_vector = np.array(result["embedding"], dtype=np.float32)
    scores = _cosine_similarity(query_vector, _knowledge_vectors)
    top_indices = np.argsort(scores)[::-1][:top_k]
    return [
        {
            "id": _knowledge_entries[i]["id"],
            "category": _knowledge_entries[i]["category"],
            "question": _knowledge_entries[i]["question"],
            "answer": _knowledge_entries[i]["answer"],
            "score": float(scores[i]),
        }
        for i in top_indices
    ]


app = FastAPI(title="ALP Hello Agent", version="0.9.0")

_log_buffer: list[dict] = []
_sse_clients: dict[str, asyncio.Queue] = {}


def _log(level: str, message: str) -> None:
    entry = {"ts": datetime.utcnow().isoformat() + "Z", "level": level, "msg": message}
    _log_buffer.append(entry)
    if len(_log_buffer) > 50:
        _log_buffer.pop(0)
    getattr(logger, level.lower(), logger.info)(message)


# ---------------------------------------------------------------------------
# Load Agent Card
# ---------------------------------------------------------------------------


def load_card() -> dict:
    if not AGENT_CARD_PATH.exists():
        raise RuntimeError(f"Agent Card not found at {AGENT_CARD_PATH}")
    with open(AGENT_CARD_PATH) as f:
        return json.load(f)


CARD = load_card()
_log("info", f"Loaded Agent Card: {CARD['name']} (ALP {CARD['alp_version']})")
_load_knowledge()


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


def _tool_greet(input_data: dict) -> dict:
    name = input_data.get("name", "stranger")
    return {"message": f"Hello, {name}! I'm {CARD['name']}, an ALP-powered agent."}


def _tool_echo(input_data: dict) -> dict:
    return {"echo": input_data.get("text", "")}


def _tool_get_agent_card(_input_data: dict) -> dict:
    return CARD


def _tool_chat(input_data: dict) -> dict:
    if not _gemini:
        raise RuntimeError("GEMINI_API_KEY not configured")
    message = input_data.get("message", "").strip()
    if not message:
        raise RuntimeError("message is required")
    response = _gemini.generate_content(message)
    return {"reply": response.text}


def _tool_search_knowledge(input_data: dict) -> dict:
    query = input_data.get("query", "").strip()
    top_k = int(input_data.get("top_k", 3))
    if not query:
        raise RuntimeError("query is required")
    results = _search_knowledge(query, top_k=top_k)
    return {"results": results, "count": len(results)}


def _tool_web_search(input_data: dict) -> dict:
    if not SERPER_API_KEY:
        raise RuntimeError("SERPER_API_KEY not configured")
    query = input_data.get("query", "").strip()
    count = int(input_data.get("count", 5))
    if not query:
        raise RuntimeError("query is required")
    import httpx

    response = httpx.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
        json={"q": query, "num": count},
        timeout=10.0,
    )
    response.raise_for_status()
    data = response.json()
    results = [
        {"title": r.get("title"), "url": r.get("link"), "description": r.get("snippet")}
        for r in data.get("organic", [])[:count]
    ]
    return {"results": results, "count": len(results), "query": query}


# ---------------------------------------------------------------------------
# Session Memory — in-process store, cleared on server restart
# ---------------------------------------------------------------------------
_session_store: dict[str, dict] = {}


def _tool_remember(input_data: dict) -> dict:
    session_id = input_data.get("session_id", "default")
    key = input_data.get("key", "").strip()
    value = input_data.get("value", "")
    if not key:
        raise RuntimeError("key is required")
    if session_id not in _session_store:
        _session_store[session_id] = {}
    _session_store[session_id][key] = {
        "value": value,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    _log("info", f"remember: [{session_id}] {key} = {value}")
    return {"stored": True, "session_id": session_id, "key": key}


def _tool_recall(input_data: dict) -> dict:
    session_id = input_data.get("session_id", "default")
    key = input_data.get("key", "").strip()
    session = _session_store.get(session_id, {})
    if key:
        entry = session.get(key)
        return {
            "key": key,
            "value": entry["value"] if entry else None,
            "found": entry is not None,
        }
    return {"memories": session, "count": len(session), "session_id": session_id}


def _tool_forget(input_data: dict) -> dict:
    session_id = input_data.get("session_id", "default")
    key = input_data.get("key", "").strip()
    if session_id not in _session_store:
        return {"cleared": False, "reason": "session not found"}
    if key:
        removed = _session_store[session_id].pop(key, None)
        return {"cleared": removed is not None, "key": key}
    _session_store.pop(session_id, None)
    return {"cleared": True, "session_id": session_id}


# ---------------------------------------------------------------------------
# search_knowledge_db — Supabase pgvector semantic search
# ---------------------------------------------------------------------------


async def _embed_text(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list:
    """Embed a single text string using Gemini embedding model."""
    result = genai.embed_content(
        model=EMBEDDING_MODEL, content=text, task_type=task_type
    )
    return result["embedding"]


async def _search_knowledge_db(
    query: str, top_k: int = 3, filter_source: str | None = None
) -> list[dict]:
    """Run cosine similarity search against Supabase knowledge_chunks via match_knowledge RPC."""
    embedding = await _embed_text(query, task_type="RETRIEVAL_QUERY")
    url = f"{os.getenv('SUPABASE_URL')}/rest/v1/rpc/match_knowledge"
    headers = {
        "apikey": os.getenv("SUPABASE_KEY"),
        "Authorization": f"Bearer {os.getenv('SUPABASE_KEY')}",
        "Content-Type": "application/json",
    }
    payload = {"query_embedding": embedding, "match_count": top_k}
    if filter_source:
        payload["filter_source"] = filter_source
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=payload, timeout=15.0)
        resp.raise_for_status()
    rows = resp.json()
    return [
        {
            "id": row["chunk_id"],
            "category": row.get("metadata", {}).get("category", row["source_type"]),
            "question": row.get("metadata", {}).get("question", row["source_name"]),
            "answer": row.get("metadata", {}).get("answer", row["text"]),
            "score": row["score"],
            "source": {
                "type": row["source_type"],
                "id": row["source_id"],
                "name": row["source_name"],
            },
        }
        for row in rows
    ]


async def tool_search_knowledge_db(input_data: dict) -> dict:
    query = input_data.get("query", "").strip()
    top_k = int(input_data.get("top_k", 3))
    filter_source = input_data.get("filter_source") or None
    if not query:
        raise RuntimeError("query is required")
    if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_KEY"):
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY are required")
    results = await _search_knowledge_db(
        query, top_k=top_k, filter_source=filter_source
    )
    return {"results": results, "count": len(results)}


# ---------------------------------------------------------------------------
# ingest_media — transcribe audio/video and store chunks in Supabase
# ---------------------------------------------------------------------------


def _split_into_chunks(
    text: str, chunk_words: int = 400, overlap_words: int = 50
) -> list[str]:
    """Split text into overlapping word-window chunks."""
    words = text.split()
    chunks = []
    step = chunk_words - overlap_words
    for i in range(0, len(words), step):
        chunk = " ".join(words[i : i + chunk_words])
        if chunk:
            chunks.append(chunk)
        if i + chunk_words >= len(words):
            break
    return chunks


def _extract_audio(video_path: str, out_path: str) -> None:
    """Extract audio track from video using ffmpeg. Output: mono MP3 at 16kHz."""
    subprocess.run(
        [
            "ffmpeg",
            "-i",
            video_path,
            "-vn",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-ab",
            "64k",
            out_path,
            "-y",
        ],
        check=True,
        capture_output=True,
    )


def _split_audio_into_segments(
    audio_path: str, segment_ms: int = 600_000, overlap_ms: int = 30_000
) -> list[str]:
    """Split audio file into overlapping segments using pydub. Returns list of /tmp file paths."""
    from pydub import AudioSegment

    audio = AudioSegment.from_file(audio_path)
    duration = len(audio)
    paths = []
    step = segment_ms - overlap_ms
    i = 0
    idx = 0
    while i < duration:
        segment = audio[i : i + segment_ms]
        seg_path = f"/tmp/{uuid.uuid4().hex}_seg_{idx}.mp3"
        segment.export(seg_path, format="mp3")
        paths.append(seg_path)
        i += step
        idx += 1
    return paths


async def _transcribe_audio_file(audio_path: str) -> str:
    """Transcribe a single audio file via Groq Whisper API. File must be under 25MB."""
    from groq import Groq

    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-large-v3", file=f, response_format="text"
        )
    return result if isinstance(result, str) else result.text


async def _transcribe_media(file_path: str) -> str:
    """
    Transcribe audio or video file.
    - Video: extract audio with ffmpeg first
    - Audio > 24MB: split with pydub, transcribe segments, join
    - Audio <= 24MB: transcribe directly
    All /tmp files cleaned up in finally block.
    """
    VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
    MAX_BYTES = 24 * 1024 * 1024

    ext = os.path.splitext(file_path)[1].lower()
    tmp_files: list[str] = []

    try:
        if ext in VIDEO_EXT:
            audio_path = f"/tmp/{uuid.uuid4().hex}_audio.mp3"
            tmp_files.append(audio_path)
            _extract_audio(file_path, audio_path)
        elif ext in AUDIO_EXT:
            audio_path = file_path
        else:
            raise ValueError(f"Unsupported file type: {ext}")

        file_size = os.path.getsize(audio_path)
        if file_size <= MAX_BYTES:
            return await _transcribe_audio_file(audio_path)

        segments = _split_audio_into_segments(audio_path)
        tmp_files.extend(segments)
        transcripts = []
        for seg_path in segments:
            text = await _transcribe_audio_file(seg_path)
            transcripts.append(text)
        return " ".join(transcripts)

    finally:
        for f in tmp_files:
            if f != file_path and os.path.exists(f):
                os.remove(f)


async def _upsert_chunks(rows: list[dict]) -> None:
    """Upsert a batch of chunk rows into Supabase knowledge_chunks."""
    url = f"{os.getenv('SUPABASE_URL')}/rest/v1/knowledge_chunks"
    headers = {
        "apikey": os.getenv("SUPABASE_KEY"),
        "Authorization": f"Bearer {os.getenv('SUPABASE_KEY')}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=rows, timeout=30.0)
        resp.raise_for_status()


async def tool_ingest_media(input_data: dict) -> dict:
    """
    Ingest an audio or video file into the knowledge base.
    Input:  { file_path, source_name (optional) }
    Output: { status, chunks_ingested, source_id }
    """
    file_path = input_data.get("file_path", "")
    source_name = input_data.get("source_name") or os.path.basename(file_path)

    if not file_path:
        return {"status": "error", "error": "Missing file_path argument"}
    elif not os.path.exists(file_path):
        return {"status": "error", "error": f"File not found: {file_path}"}
    else:
        result = await run_ingestion_pipeline(file_path, source_name)
        return result


TOOLS: dict[str, dict] = {
    tool["name"]: {
        "meta": tool,
        "fn": {
            "greet": _tool_greet,
            "echo": _tool_echo,
            "get_agent_card": _tool_get_agent_card,
            "chat": _tool_chat,
            "search_knowledge": _tool_search_knowledge,
            "web_search": _tool_web_search,
            "remember": _tool_remember,
            "recall": _tool_recall,
            "forget": _tool_forget,
            "search_knowledge_db": tool_search_knowledge_db,
            "ingest_media": tool_ingest_media,
        }[tool["name"]],
    }
    for tool in CARD["tools"]
}


async def run_ingestion_pipeline(file_path: str, source_name: str) -> dict:
    """
    Shared ingestion pipeline:
    1. Transcribe audio/video with Groq Whisper
    2. Chunk the transcript
    3. Embed each chunk with Gemini
    4. Store in Supabase pgvector table
    Returns a summary dict.
    """
    import math

    # --- Step 1: Transcribe with Groq Whisper ---
    groq_client = groq.Groq(api_key=os.environ.get("GROQ_API_KEY"))
    with open(file_path, "rb") as f:
        transcription = groq_client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=(os.path.basename(file_path), f),
            response_format="text"
        )
    transcript_text = transcription if isinstance(transcription, str) else transcription.text

    if not transcript_text.strip():
        return {"status": "error", "error": "Transcription returned empty text"}

    # --- Step 2: Chunk the transcript (500 chars with 50 char overlap) ---
    chunk_size = 500
    overlap = 50
    chunks = []
    start = 0
    while start < len(transcript_text):
        end = min(start + chunk_size, len(transcript_text))
        chunks.append(transcript_text[start:end])
        start += chunk_size - overlap

    # --- Step 3: Embed each chunk with Gemini ---
    genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

    supabase_client = supabase.create_client(
        os.environ.get("SUPABASE_URL"),
        os.environ.get("SUPABASE_KEY")
    )

    ingested = 0
    for i, chunk in enumerate(chunks):
        embedding_result = genai.embed_content(
            model="models/text-embedding-004",
            content=chunk,
            task_type="retrieval_document"
        )
        embedding_vector = embedding_result["embedding"]

        supabase_client.table("knowledge").insert({
            "content": chunk,
            "embedding": embedding_vector,
            "source": source_name,
            "source_type": "video",
            "chunk_index": i
        }).execute()
        ingested += 1

    return {
        "status": "ok",
        "source_name": source_name,
        "chunks_ingested": ingested,
        "transcript_preview": transcript_text[:300] + ("..." if len(transcript_text) > 300 else "")
    }


@app.post("/upload")
async def upload_and_ingest(
    file: UploadFile = File(...),
    source_name: str = Form(default=None)
):
    """
    Accepts a file upload (audio or video) via multipart/form-data.
    Transcribes with Groq Whisper, embeds with Gemini, stores in Supabase.
    Called by Claude when a user uploads a file directly in the chat.
    """
    ALLOWED_EXTENSIONS = {
        ".mp3", ".wav", ".m4a", ".ogg", ".flac",
        ".mp4", ".mov", ".avi", ".mkv", ".webm"
    }

    # Validate file extension
    original_filename = file.filename or "upload"
    ext = os.path.splitext(original_filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": f"Unsupported file type: {ext}"}
        )

    label = source_name or os.path.splitext(original_filename)[0]

    # Save uploaded file to a temp location on Render
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = await run_ingestion_pipeline(tmp_path, label)
        return JSONResponse(content=result)
    finally:
        # Always clean up the temp file
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.post("/ingest-url")
async def ingest_from_url(request: Request):
    """
    Accepts a JSON body: { "url": "https://...", "source_name": "optional-label" }
    Downloads the file from the URL, then transcribes + embeds + stores in Supabase.
    """
    body = await request.json()
    url = body.get("url", "").strip()
    source_name = body.get("source_name", "")

    if not url:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": "Missing 'url' in request body"}
        )

    # Handle Google Drive share links → convert to direct download URL
    if "drive.google.com" in url and "/file/d/" in url:
        file_id = url.split("/file/d/")[1].split("/")[0]
        url = f"https://drive.google.com/uc?export=download&id={file_id}"

    # Derive label and extension from URL
    url_path = url.split("?")[0]
    original_filename = url_path.split("/")[-1] or "download"
    ext = os.path.splitext(original_filename)[1].lower() or ".mp4"
    label = source_name or os.path.splitext(original_filename)[0]

    # Download the file
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
            response = await client.get(url)
            response.raise_for_status()
            file_bytes = response.content
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": f"Failed to download file: {str(e)}"}
        )

    # Save to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        result = await run_ingestion_pipeline(tmp_path, label)
        return JSONResponse(content=result)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ---------------------------------------------------------------------------
# MCP JSON-RPC helpers
# ---------------------------------------------------------------------------


def _mcp_tools_list() -> list[dict]:
    return [
        {
            "name": t["meta"]["name"],
            "description": t["meta"]["description"],
            "inputSchema": t["meta"].get(
                "input_schema", {"type": "object", "properties": {}}
            ),
        }
        for t in TOOLS.values()
    ]


async def _handle_mcp_message(msg: dict):
    method = msg.get("method", "")
    msg_id = msg.get("id")
    params = msg.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": CARD["name"], "version": CARD["alp_version"]},
            },
        }

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": _mcp_tools_list()}}

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_input = params.get("arguments", params.get("input", {}))
        if tool_name not in TOOLS:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"},
            }
        try:
            fn = TOOLS[tool_name]["fn"]
            result = (
                await fn(tool_input)
                if asyncio.iscoroutinefunction(fn)
                else fn(tool_input)
            )
            _log("info", f"MCP tool '{tool_name}' succeeded")
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result)}],
                    "isError": False,
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            }

    if method.startswith("notifications/"):
        return None

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


# ---------------------------------------------------------------------------
# MCP SSE endpoint (Kiro)
# ---------------------------------------------------------------------------


@app.get("/mcp")
async def mcp_sse(request: Request):
    """MCP SSE transport — Kiro connects here."""
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _sse_clients[session_id] = queue
    _log("info", f"MCP SSE client connected: {session_id}")

    async def event_stream():
        try:
            endpoint_url = f"http://localhost:{PORT}/mcp?session_id={session_id}"
            yield f"event: endpoint\ndata: {endpoint_url}\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: message\ndata: {json.dumps(message)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _sse_clients.pop(session_id, None)
            _log("info", f"MCP SSE client disconnected: {session_id}")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/mcp")
async def mcp_post(request: Request):
    """MCP JSON-RPC message receiver — Kiro POSTs here."""
    session_id = request.query_params.get("session_id")
    _log("info", f"MCP POST session={session_id}")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    messages = body if isinstance(body, list) else [body]
    last_response = None

    for msg in messages:
        response = await _handle_mcp_message(msg)
        if response is None:
            continue
        if session_id and session_id in _sse_clients:
            await _sse_clients[session_id].put(response)
        else:
            last_response = response

    if last_response:
        return JSONResponse(last_response)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Standard REST endpoints (Claude Code / Claude Desktop)
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    _log("info", "GET /health")
    return {"status": "ok", "alp_version": CARD.get("alp_version", "0.9.0")}


@app.get("/agent")
async def get_agent():
    _log("info", "GET /agent")
    return JSONResponse(content=CARD)


@app.get("/persona")
async def get_persona():
    """v0.4.0 REQUIRED — system prompt for any runtime to inject before tools."""
    _log("info", "GET /persona")
    return {
        "persona": CARD.get("persona", ""),
        "id": CARD.get("id", ""),
        "name": CARD.get("name", ""),
    }


@app.get("/agents")
async def list_agents():
    """v0.4.0 OPTIONAL — catalog of all cards hosted by this server."""
    _log("info", "GET /agents")
    agents_dir = os.getenv("AGENTS_DIR")
    if agents_dir:
        agents = []
        for card_path in Path(agents_dir).rglob("agent.alp.json"):
            try:
                with open(card_path) as f:
                    agents.append(json.load(f))
            except Exception as exc:
                _log("error", f"Failed to load card at {card_path}: {exc}")
        return {"agents": agents}
    return {"agents": [CARD]}


@app.get("/tools")
async def list_tools():
    """REST tool list — Claude Code / Claude Desktop."""
    _log("info", "GET /tools")
    return {"tools": _mcp_tools_list()}


@app.post("/tools/{tool_name}")
async def call_tool(tool_name: str, request: Request):
    """REST tool execution — Claude Code / Claude Desktop."""
    _log("info", f"POST /tools/{tool_name}")
    if tool_name not in TOOLS:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
    try:
        body = await request.json()
    except Exception:
        body = {}
    input_data = body.get("input", body)
    try:
        fn = TOOLS[tool_name]["fn"]
        result = (
            await fn(input_data) if asyncio.iscoroutinefunction(fn) else fn(input_data)
        )
        _log("info", f"Tool '{tool_name}' succeeded")
        return {"result": result, "error": None}
    except Exception as exc:
        return JSONResponse(
            status_code=500, content={"result": None, "error": str(exc)}
        )


@app.get("/logs")
async def get_logs():
    return {"logs": _log_buffer}


@app.post("/chat")
async def chat(request: Request):
    if not _gemini:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY not configured")
    body = await request.json()
    message = body.get("message", "").strip()
    history = body.get("history", [])  # [{"role": "user"|"model", "parts": "..."}]
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    try:
        convo = _gemini.start_chat(history=history)
        response = convo.send_message(message)
        _log("info", f"Chat message processed")
        return {"reply": response.text}
    except Exception as exc:
        _log("error", f"Chat error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Dashboard (HTML)
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>ALP Hello Agent</title>
  <style>
    :root {
      --bg:#0d1117;--surface:#161b22;--border:#30363d;
      --accent:#58a6ff;--green:#3fb950;--orange:#d29922;
      --text:#c9d1d9;--muted:#8b949e;--font:'Segoe UI',system-ui,sans-serif;
    }
    *{box-sizing:border-box;margin:0;padding:0}
    body{background:var(--bg);color:var(--text);font-family:var(--font);padding:2rem}
    header{border-bottom:1px solid var(--border);padding-bottom:1.25rem;margin-bottom:2rem}
    header h1{font-size:1.6rem;color:var(--accent)}
    header p{color:var(--muted);margin-top:.35rem;font-size:.95rem}
    .badge{display:inline-block;font-size:.75rem;padding:.15rem .5rem;border-radius:999px;
           background:var(--green);color:#000;font-weight:600;margin-left:.5rem;vertical-align:middle}
    .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:1rem;margin-bottom:1rem}
    .card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:1.25rem}
    .card.highlight{border-color:var(--orange)}
    .card h2{font-size:1rem;color:var(--accent);margin-bottom:.5rem}
    .card p{font-size:.875rem;color:var(--muted);margin-bottom:.85rem}
    .card a{display:inline-block;font-size:.8rem;padding:.3rem .75rem;background:var(--accent);
            color:#000;border-radius:5px;text-decoration:none;font-weight:600}
    .card a:hover{opacity:.85}
    .mono{font-family:monospace;font-size:.82rem;color:var(--green);background:#0d1117;
          border:1px solid var(--border);border-radius:5px;padding:.6rem .9rem;
          margin-top:1.25rem;white-space:pre-wrap;word-break:break-all}
    footer{margin-top:2.5rem;font-size:.8rem;color:var(--muted)}
    footer a{color:var(--accent);text-decoration:none}
  </style>
</head>
<body>
  <header>
    <h1>ALP Hello Agent <span class="badge">v0.9.0</span></h1>
    <p>Agent Load Protocol · HTTP MCP Server · Connect from Kiro or Claude Code</p>
  </header>
  <div class="grid">
    <div class="card highlight">
      <h2>⚡ MCP SSE</h2>
      <p>Kiro connects here — SSE transport (text/event-stream).</p>
      <a href="/mcp" target="_blank">GET /mcp</a>
    </div>
    <div class="card">
      <h2>🟢 Health</h2>
      <p>Server status check.</p>
      <a href="/health" target="_blank">GET /health</a>
    </div>
    <div class="card">
      <h2>🤖 Agent Card</h2>
      <p>Full ALP Agent Card JSON.</p>
      <a href="/agent" target="_blank">GET /agent</a>
    </div>
    <div class="card">
      <h2>🧠 Persona</h2>
      <p>v0.4.0 — system prompt for any runtime.</p>
      <a href="/persona" target="_blank">GET /persona</a>
    </div>
    <div class="card">
      <h2>📦 All Agents</h2>
      <p>v0.4.0 — catalog of all hosted cards.</p>
      <a href="/agents" target="_blank">GET /agents</a>
    </div>
    <div class="card">
      <h2>🔧 Tools (REST)</h2>
      <p>Claude Code / Claude Desktop.</p>
      <a href="/tools" target="_blank">GET /tools</a>
    </div>
    <div class="card">
      <h2>📋 Logs</h2>
      <p>Last 50 server log entries.</p>
      <a href="/logs" target="_blank">GET /logs</a>
    </div>
    <div class="card highlight">
      <h2>💬 Chat</h2>
      <p>Chat with the agent powered by Gemini.</p>
      <a href="/chat-ui">Open Chat</a>
    </div>
  </div>
  <div class="mono">Kiro MCP config (.kiro/settings/mcp.json):

{
  "mcpServers": {
    "hello-agent": {
      "url": "http://localhost:8000/mcp"
    }
  }
}</div>
  <div class="mono">Claude Code / Claude Desktop:

{
  "mcpServers": {
    "hello-agent": {
      "url": "http://localhost:8000/tools"
    }
  }
}</div>
  <footer>
    Built with <a href="https://github.com/RodrigoMvs123/agent-load-protocol" target="_blank">Agent Load Protocol</a>
    by <a href="https://github.com/RodrigoMvs123" target="_blank">@RodrigoMvs123</a>
  </footer>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    _log("info", "GET /")
    return HTMLResponse(content=DASHBOARD_HTML)


CHAT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>ALP Chat</title>
  <style>
    :root{--bg:#0d1117;--surface:#161b22;--border:#30363d;--accent:#58a6ff;
          --green:#3fb950;--text:#c9d1d9;--muted:#8b949e;--font:'Segoe UI',system-ui,sans-serif}
    *{box-sizing:border-box;margin:0;padding:0}
    body{background:var(--bg);color:var(--text);font-family:var(--font);
         display:flex;flex-direction:column;height:100vh}
    header{padding:1rem 1.5rem;border-bottom:1px solid var(--border);
           display:flex;align-items:center;gap:.75rem}
    header h1{font-size:1.1rem;color:var(--accent)}
    header a{margin-left:auto;font-size:.8rem;color:var(--muted);text-decoration:none}
    header a:hover{color:var(--accent)}
    #messages{flex:1;overflow-y:auto;padding:1.25rem 1.5rem;display:flex;flex-direction:column;gap:.75rem}
    .msg{max-width:72%;padding:.65rem 1rem;border-radius:10px;font-size:.9rem;line-height:1.5;white-space:pre-wrap;word-break:break-word}
    .msg.user{align-self:flex-end;background:var(--accent);color:#000}
    .msg.model{align-self:flex-start;background:var(--surface);border:1px solid var(--border)}
    .msg.error{align-self:flex-start;background:#3d1a1a;border:1px solid #8b2020;color:#ff7b7b}
    #form{display:flex;gap:.5rem;padding:1rem 1.5rem;border-top:1px solid var(--border)}
    #input{flex:1;background:var(--surface);border:1px solid var(--border);border-radius:8px;
           color:var(--text);padding:.65rem 1rem;font-size:.9rem;font-family:var(--font);resize:none;height:44px}
    #input:focus{outline:none;border-color:var(--accent)}
    button{background:var(--accent);color:#000;border:none;border-radius:8px;
           padding:.65rem 1.25rem;font-weight:600;cursor:pointer;font-size:.9rem}
    button:disabled{opacity:.5;cursor:not-allowed}
    .typing{color:var(--muted);font-size:.82rem;padding:0 1.5rem .5rem}
  </style>
</head>
<body>
  <header>
    <h1>💬 ALP Chat</h1>
    <span style="font-size:.8rem;color:var(--muted)">gemini-2.5-flash</span>
    <a href="/">← Dashboard</a>
  </header>
  <div id="messages"></div>
  <div class="typing" id="typing" style="display:none">Agent is typing…</div>
  <form id="form">
    <textarea id="input" placeholder="Type a message… (Enter to send, Shift+Enter for newline)"></textarea>
    <button type="submit" id="btn">Send</button>
  </form>
  <script>
    const messages = document.getElementById('messages');
    const form = document.getElementById('form');
    const input = document.getElementById('input');
    const btn = document.getElementById('btn');
    const typing = document.getElementById('typing');
    let history = [];

    function addMsg(role, text) {
      const div = document.createElement('div');
      div.className = 'msg ' + role;
      div.textContent = text;
      messages.appendChild(div);
      messages.scrollTop = messages.scrollHeight;
      return div;
    }

    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
    });

    form.addEventListener('submit', async e => {
      e.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      input.value = '';
      addMsg('user', text);
      btn.disabled = true;
      typing.style.display = 'block';
      try {
        const res = await fetch('/chat', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ message: text, history })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Error');
        history.push({ role: 'user', parts: text });
        history.push({ role: 'model', parts: data.reply });
        addMsg('model', data.reply);
      } catch (err) {
        addMsg('error', '⚠ ' + err.message);
      } finally {
        btn.disabled = false;
        typing.style.display = 'none';
        input.focus();
      }
    });
  </script>
</body>
</html>
"""


@app.get("/chat-ui", response_class=HTMLResponse)
async def chat_ui():
    _log("info", "GET /chat-ui")
    return HTMLResponse(content=CHAT_HTML)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    _log("info", f"Starting ALP server on port {PORT}")
    uvicorn.run("server:app", host="0.0.0.0", port=PORT, reload=True)
