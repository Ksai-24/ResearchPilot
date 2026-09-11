"""FastAPI server for Aira.

Features:
- Database lifecycle (lifespan startup/shutdown)
- Authentication API (PBKDF2-HMAC-SHA256 salted credentials & sessions)
- Multi-tenant chat history persistence with user isolation
- In-memory rate limiting and brute-force protection
- CORS configuration
- Structured Pydantic request and response schemas
- Load balancer and traffic monitoring (/api/system/traffic)
- 50+ PDF Multi-Document batch processing and indexing (/api/upload-batch)
- SSE streaming agent loop (/api/ask) with dynamic multi-API routing
- Static SPA web interface
"""
import asyncio
import json
import logging
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .agent import run_agent
from .db import (
    delete_chat_from_db,
    get_chat_from_db,
    get_chats_from_db,
    get_user_from_token,
    google_auth_or_register,
    init_db,
    login_user,
    logout_session,
    register_user,
    save_chat_to_db,
    shutdown_db,
)
from .export import export_chat
from .files import doc_index, extract as extract_file
from .load_balancer import load_balancer
from .tools import web_search
from .tools.web import close_web_client

logger = logging.getLogger("aira.server")
STATIC_DIR = Path(__file__).resolve().parent / "static"


# =====================================================================
# Pydantic Schemas for Request & Response Validation
# =====================================================================
class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, description="Unique username")
    email: str = Field(..., min_length=5, max_length=128, description="Valid email address")
    password: str = Field(..., min_length=6, max_length=128, description="Password")


class LoginRequest(BaseModel):
    username_or_email: Optional[str] = Field(None, max_length=128)
    username: Optional[str] = Field(None, max_length=128)
    password: str = Field(..., min_length=1, max_length=128)


class GoogleAuthRequest(BaseModel):
    credential: Optional[str] = Field(None, description="Google ID Token JWT")
    client_id: Optional[str] = Field(None, description="Google Client ID")
    email: Optional[str] = Field(None, description="User email")
    name: Optional[str] = Field(None, description="User full name")
    picture: Optional[str] = Field(None, description="Avatar image URL")
    google_id: Optional[str] = Field(None, description="Google account ID")


class ChatMessageSchema(BaseModel):
    role: str
    text: Optional[str] = ""
    sources: Optional[List[str]] = []
    tools: Optional[List[str]] = []
    elapsed: Optional[str] = None
    files: Optional[List[str]] = []


class ChatSaveRequest(BaseModel):
    id: Optional[str] = None
    title: Optional[str] = "New Chat"
    kind: Optional[str] = "research"
    messages: Optional[List[Dict[str, Any]]] = []
    created: Optional[int] = None
    updated: Optional[int] = None
    user_id: Optional[int] = None


class AskRequest(BaseModel):
    mode: Optional[str] = "RESEARCH"
    depth: Optional[str] = "short"
    question: str = Field(..., min_length=1, max_length=50000, description="Question or task for the agent")


class ExportRequest(BaseModel):
    format: str = Field("txt", description="Export format: pdf, docx, or txt")
    mode: Optional[str] = "RESEARCH"
    title: Optional[str] = "chat"
    messages: List[Dict[str, Any]] = Field(..., min_length=1, description="Messages to export")


# =====================================================================
# In-Memory Sliding-Window Rate Limiter
# =====================================================================
class InMemoryRateLimiter:
    """Sliding-window rate limiter keyed by client identifier and route scope."""

    def __init__(self):
        self._requests: Dict[str, deque] = {}
        self._lock = asyncio.Lock()

    async def is_rate_limited(self, key: str, max_requests: int, window_seconds: int = 60) -> Tuple[bool, int]:
        if not config.RATE_LIMIT_ENABLED:
            return False, 0

        now = time.time()
        cutoff = now - window_seconds
        async with self._lock:
            if key not in self._requests:
                self._requests[key] = deque()

            req_deque = self._requests[key]
            # Prune timestamps older than window
            while req_deque and req_deque[0] < cutoff:
                req_deque.popleft()

            if len(req_deque) >= max_requests:
                retry_after = int(window_seconds - (now - req_deque[0])) + 1
                return True, max(1, retry_after)

            req_deque.append(now)
            return False, 0


rate_limiter = InMemoryRateLimiter()


def rate_limit(max_requests: int, window_seconds: int = 60, scope: str = "default"):
    """FastAPI dependency for rate-limiting endpoints."""
    async def dependency(request: Request):
        client_ip = request.client.host if request.client else "127.0.0.1"
        # Combine IP and route scope for fine-grained limits
        key = f"{client_ip}:{scope}"
        limited, retry_after = await rate_limiter.is_rate_limited(key, max_requests, window_seconds)
        if limited:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)},
            )
    return dependency


# =====================================================================
# Lifespan and App Initialization
# =====================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database tables & pool
    db_info = await init_db()
    app.state.db_info = db_info
    yield
    # Shutdown: clean up pooled connections and memory
    await shutdown_db()
    await close_web_client()


app = FastAPI(
    title="Aira API",
    description="Next-Gen Agentic Research and Code Debugging Platform API",
    version="3.0.0",
    lifespan=lifespan,
)

# CORS Middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ---- Helper: Extract Bearer token from headers ----
def _get_token_from_header(auth_header: Optional[str]) -> Optional[str]:
    if not auth_header:
        return None
    parts = auth_header.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return auth_header.strip()


async def get_current_user_optional(authorization: Optional[str] = Header(None)) -> Optional[Dict[str, Any]]:
    """Optional authentication dependency: returns user dict if valid token, else None."""
    token = _get_token_from_header(authorization)
    if not token:
        return None
    return await get_user_from_token(token)


async def get_current_user_required(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    """Strict authentication dependency: raises 401 if missing or invalid session token."""
    token = _get_token_from_header(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = await get_user_from_token(token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# =====================================================================
# System & Health Endpoints
# =====================================================================
@app.get("/api/health", dependencies=[Depends(rate_limit(120, 60, "health"))])
async def health():
    """System health check, provider configuration, and runtime status."""
    config.reload_config()
    traffic_stats = load_balancer.get_stats()
    db_info = getattr(app.state, "db_info", {"status": "uninitialized"})
    return {
        "ok": True,
        "model": config.MODEL,
        "secondary_model": config.SECONDARY_MODEL,
        "base_url": config.BASE_URL,
        "key_loaded": bool(config.API_KEY),
        "traffic": traffic_stats,
        "database": db_info,
        "indexed_documents": len(doc_index.documents),
    }


@app.get("/api/system/traffic", dependencies=[Depends(rate_limit(60, 60, "traffic"))])
async def system_traffic():
    """Return real-time concurrency, RPM, and provider routing status."""
    return load_balancer.get_stats()


# =====================================================================
# Authentication Endpoints (Database-Connected with MySQL chat_app)
# =====================================================================
def _extract_client_ip(request: Request) -> str:
    """Extract real client IP address from proxy headers or connection host."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


@app.get("/api/auth/google/config")
async def google_auth_config():
    """Return Google Client ID configuration for client-side Google Sign-In."""
    return {
        "ok": True,
        "client_id": config.GOOGLE_CLIENT_ID or "",
        "enabled": bool(config.GOOGLE_CLIENT_ID),
    }


@app.post("/api/auth/google", dependencies=[Depends(rate_limit(20, 60, "auth_google"))])
async def auth_google(payload: GoogleAuthRequest, request: Request):
    """Authenticate with Google token/credential, persist user & login log to MySQL chat_app, and issue session."""
    client_ip = _extract_client_ip(request)
    email = (payload.email or "").strip().lower()
    name = (payload.name or "").strip()
    google_id = (payload.google_id or "").strip()
    picture = payload.picture

    # If Google ID Token JWT is provided, verify via Google TokenInfo or JWT payload
    if payload.credential:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(f"https://oauth2.googleapis.com/tokeninfo?id_token={payload.credential}")
                if resp.status_code == 200:
                    info = resp.json()
                    email = info.get("email", email).lower()
                    name = info.get("name", name) or email.split("@")[0]
                    google_id = str(info.get("sub", google_id))
                    picture = info.get("picture", picture)
                else:
                    # Parse JWT claims directly (header.payload.signature)
                    parts = payload.credential.split(".")
                    if len(parts) == 3:
                        import base64
                        padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
                        decoded = base64.urlsafe_b64decode(padded.encode("utf-8"))
                        claims = json.loads(decoded.decode("utf-8"))
                        email = claims.get("email", email).lower()
                        name = claims.get("name", name) or email.split("@")[0]
                        google_id = str(claims.get("sub", google_id))
                        picture = claims.get("picture", picture)
        except Exception as e:
            logger.warning("Google token verification warning: %s", e)

    if not email or not google_id:
        return JSONResponse(
            {"ok": False, "error": "Unable to verify Google credential (missing email or Google ID)"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    res = await google_auth_or_register(
        google_id=google_id,
        email=email,
        name=name,
        avatar_url=picture,
        ip_address=client_ip,
    )
    if not res.get("ok"):
        return JSONResponse(res, status_code=status.HTTP_400_BAD_REQUEST)
    return res


@app.post("/api/auth/register", dependencies=[Depends(rate_limit(10, 60, "auth_register"))])
async def auth_register(payload: RegisterRequest, request: Request):
    """Register a new user account with PBKDF2-HMAC-SHA256 password protection in MySQL chat_app."""
    client_ip = _extract_client_ip(request)
    res = await register_user(payload.username, payload.email, payload.password, ip_address=client_ip)
    if not res.get("ok"):
        return JSONResponse(res, status_code=status.HTTP_400_BAD_REQUEST)
    return res


@app.post("/api/auth/login", dependencies=[Depends(rate_limit(10, 60, "auth_login"))])
async def auth_login(payload: LoginRequest, request: Request):
    """Authenticate with username/email and password, logging event to MySQL chat_app login_logs."""
    client_ip = _extract_client_ip(request)
    username_or_email = payload.username_or_email or payload.username or ""
    res = await login_user(username_or_email, payload.password, ip_address=client_ip)
    if not res.get("ok"):
        return JSONResponse(res, status_code=status.HTTP_401_UNAUTHORIZED)
    return res


@app.get("/api/auth/me")
async def auth_me(user: Dict[str, Any] = Depends(get_current_user_required)):
    """Retrieve details of the currently authenticated user."""
    return {"ok": True, "user": user}


@app.post("/api/auth/logout")
async def auth_logout(request: Request, authorization: Optional[str] = Header(None)):
    """Invalidate current session."""
    token = _get_token_from_header(authorization)
    if not token:
        try:
            body = await request.json()
            if isinstance(body, dict):
                token = body.get("token")
        except Exception:
            token = None
    if token:
        await logout_session(token)
    return {"ok": True, "message": "Logged out"}



# =====================================================================
# Chat History Persistence & Tenancy Endpoints
# =====================================================================
@app.get("/api/chats", dependencies=[Depends(rate_limit(60, 60, "chats_list"))])
async def list_chats(kind: Optional[str] = None, user: Optional[Dict[str, Any]] = Depends(get_current_user_optional)):
    """List chats stored in the database with user tenancy isolation."""
    user_id = user["id"] if user else None
    chats = await get_chats_from_db(kind=kind, user_id=user_id)
    return {"ok": True, "chats": chats}


@app.get("/api/chats/{chat_id}", dependencies=[Depends(rate_limit(60, 60, "chats_get"))])
async def get_chat_endpoint(chat_id: str, user: Optional[Dict[str, Any]] = Depends(get_current_user_optional)):
    """Retrieve a single chat with its full messages in original form."""
    user_id = user["id"] if user else None
    chat = await get_chat_from_db(chat_id, user_id=user_id)
    if not chat:
        return JSONResponse({"ok": False, "error": "Chat not found"}, status_code=status.HTTP_404_NOT_FOUND)
    return {"ok": True, "chat": chat}


@app.post("/api/chats", dependencies=[Depends(rate_limit(60, 60, "chats_save"))])
async def save_chat_endpoint(payload: ChatSaveRequest, user: Optional[Dict[str, Any]] = Depends(get_current_user_optional)):
    """Save or update a chat with user tenancy association."""
    user_id = user["id"] if user else payload.user_id
    saved = await save_chat_to_db(payload.model_dump(), user_id=user_id)
    return {"ok": True, "chat": saved}


@app.delete("/api/chats/{chat_id}", dependencies=[Depends(rate_limit(60, 60, "chats_delete"))])
async def delete_chat_endpoint(chat_id: str, user: Optional[Dict[str, Any]] = Depends(get_current_user_optional)):
    """Delete a chat from the local system database."""
    user_id = user["id"] if user else None
    deleted = await delete_chat_from_db(chat_id, user_id=user_id)
    return {"ok": True, "deleted": deleted}


# =====================================================================
# Document & PDF Handling (Supports 50+ PDFs)
# =====================================================================
@app.post("/api/upload", dependencies=[Depends(rate_limit(20, 60, "upload"))])
async def upload(file: UploadFile = File(...)):
    """Single file upload with text extraction and document indexing."""
    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        return JSONResponse({"error": "file too large (max 25 MB)"}, status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    filename = file.filename or "uploaded_file"
    result = extract_file(filename, data)
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error", "extraction failed")}, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)

    # Index into document store for agent search
    doc_index.add_document(filename, data)
    result["indexed_total"] = len(doc_index.documents)
    return result


@app.post("/api/upload-batch", dependencies=[Depends(rate_limit(10, 60, "upload_batch"))])
async def upload_batch(files: List[UploadFile] = File(...)):
    """Batch upload designed to handle 50+ PDFs with bounded parallel extraction."""
    if not files:
        return JSONResponse({"error": "No files provided"}, status_code=status.HTTP_400_BAD_REQUEST)

    if len(files) > 100:
        return JSONResponse({"error": "Batch limit exceeded (maximum 100 files per upload)"}, status_code=status.HTTP_400_BAD_REQUEST)

    # Limit extraction concurrency to 4 workers to prevent memory spikes
    semaphore = asyncio.Semaphore(4)

    async def process_one(f: UploadFile):
        async with semaphore:
            content = await f.read()
            fname = f.filename or "uploaded_file"
            if len(content) > 25 * 1024 * 1024:
                return {"ok": False, "name": fname, "error": "file exceeds 25 MB"}
            return doc_index.add_document(fname, content)

    tasks = [process_one(f) for f in files]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    successful = 0
    failed = 0
    details = []
    for r in results:
        if isinstance(r, Exception):
            failed += 1
            details.append({"ok": False, "error": str(r)})
        elif isinstance(r, dict) and r.get("ok"):
            successful += 1
            details.append(r)
        else:
            failed += 1
            details.append(r)

    catalog_preview = doc_index.get_catalog_summary(max_docs=10)
    return {
        "ok": True,
        "uploaded_count": len(files),
        "successful": successful,
        "failed": failed,
        "total_indexed_documents": len(doc_index.documents),
        "total_chunks": len(doc_index.chunks),
        "catalog_preview": catalog_preview,
        "details": details,
    }


@app.get("/api/documents/catalog", dependencies=[Depends(rate_limit(60, 60, "documents_catalog"))])
async def get_documents_catalog():
    """Return summary catalog of all uploaded and indexed documents."""
    return {
        "ok": True,
        "total_documents": len(doc_index.documents),
        "total_chunks": len(doc_index.chunks),
        "catalog": doc_index.get_catalog_summary(max_docs=100),
        "documents": [
            {
                "name": doc["name"],
                "pages": doc["pages"],
                "chars": doc["chars"],
                "chunks": doc["chunks"],
                "summary": doc["summary"][:200],
            }
            for doc in doc_index.documents.values()
        ],
    }


@app.delete("/api/documents", dependencies=[Depends(rate_limit(10, 60, "documents_clear"))])
async def clear_documents(user: Optional[Dict[str, Any]] = Depends(get_current_user_optional)):
    """Clear all indexed documents. When authentication is configured, requires a valid session."""
    if config.AUTH_REQUIRED_FOR_DOCS and not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to clear indexed documents",
        )
    count = len(doc_index.documents)
    doc_index.clear()
    return {"ok": True, "cleared_count": count}


# =====================================================================
# Agent Asking Endpoint (Streaming SSE)
# =====================================================================
@app.post("/api/ask", dependencies=[Depends(rate_limit(30, 60, "ask"))])
async def ask(payload: AskRequest):
    """Execute streaming agent research or debugging loop via Server-Sent Events (SSE)."""
    mode = (payload.mode or "RESEARCH").upper()
    if mode not in ("RESEARCH", "BUG_FIX"):
        mode = "RESEARCH"
    depth = (payload.depth or "short").lower()
    if depth not in ("1-mark", "short", "full theory"):
        depth = "short"
    question = payload.question.strip()
    if not question:
        return JSONResponse({"error": "question is required"}, status_code=status.HTTP_400_BAD_REQUEST)

    # If documents are uploaded, append the catalog summary so the model is aware
    if doc_index.documents and "--- UPLOADED DOCUMENTS CATALOG" not in question:
        catalog = doc_index.get_catalog_summary(max_docs=40)
        question = (
            f"{question}\n\n"
            f"{catalog}\n\n"
            "INSTRUCTION FOR DOCUMENTS: Use the `search_documents` tool to look up specific chapters, "
            "data points, experiments, or passages across any of the above indexed files before answering."
        )

    async def event_stream():
        # Immediate connection acknowledgment keep-alive so client connection never drops
        yield ": connected\n\n"
        try:
            async for ev in run_agent(mode, depth, question):
                if "final" in ev:
                    yield f"data: {json.dumps({'type': 'final', 'answer': ev['final']}, ensure_ascii=False)}\n\n"
                elif "tool" in ev:
                    yield f"data: {json.dumps({'type': 'tool', **ev}, ensure_ascii=False)}\n\n"
                elif "tool_result" in ev:
                    payload_json = json.dumps({'type': 'tool_result', **ev}, ensure_ascii=False)
                    yield f"data: {payload_json}\n\n"
                elif "status" in ev:
                    yield f"data: {json.dumps({'type': 'status', **ev}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error("Error in agent stream: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# =====================================================================
# Demo Search & Export Endpoints
# =====================================================================
@app.get("/api/demo-search", dependencies=[Depends(rate_limit(30, 60, "demo_search"))])
async def demo_search(q: str = "herd immunity"):
    """Keyless search sanity check used by tests and first-run diagnostics."""
    clean_q = q.strip()[:200]
    return await web_search(clean_q)


@app.post("/api/export", dependencies=[Depends(rate_limit(30, 60, "export"))])
async def export_endpoint(payload: ExportRequest):
    """Export chat conversation to Word (.docx), PDF, or plain text."""
    fmt = payload.format.lower()
    if fmt not in ("pdf", "docx", "txt"):
        return JSONResponse({"error": "format must be pdf, docx, or txt"}, status_code=status.HTTP_400_BAD_REQUEST)
    mode = payload.mode.upper() if payload.mode else "RESEARCH"
    if mode not in ("RESEARCH", "BUG_FIX"):
        mode = "RESEARCH"
    if not payload.messages:
        return JSONResponse({"error": "nothing to export"}, status_code=status.HTTP_400_BAD_REQUEST)

    data, filename, mime = export_chat(
        payload.title or "chat", mode, payload.messages, fmt
    )
    return Response(
        content=data,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


# Serve the SPA last so /api routes keep priority.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
