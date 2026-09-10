# Aira — Next-Gen Agentic Research & Debugging Assistant

A resilient, multi-tenant agentic platform operating across three dedicated modes:
1. **RESEARCH** — Fast live web search, verification, and cited synthesis (decompose -> retrieve -> verify -> reconcile -> synthesize).
2. **DEEP SEARCH** — Exhaustive multi-round decomposition, deep page scraping, and cross-source disagreement analysis.
3. **CODE & FIX** — Sandboxed code reproduction, live web documentation scraping, and minimal verified repair with an AST-hardened Python execution sandbox.

---

## Architecture & Features

- **Multi-Tenant SQL Storage**: Built-in SQLite3 (with WAL mode concurrency) and optional remote MySQL for persistent user accounts, PBKDF2-HMAC-SHA256 password hashing, and user-isolated chat history.
- **50+ PDF Multi-Document Indexing**: High-capacity document store with memory-bounded parallel extraction, chunking, and BM25-style keyword search.
- **Adaptive Load Balancer**: Concurrency limiting (50 concurrent slots), real-time traffic monitoring, and round-robin multi-API routing (Primary/Secondary model failover).
- **Hardened Security & Rate Limiting**: In-memory sliding window rate limiter, CORS middleware, Pydantic schema validation, and pre-execution AST screening for Python sandbox scripts.
- **High-Performance Web Tools**: Multi-tier search (Bing, Wikipedia, StackExchange, DuckDuckGo) with tracking URL unwrapping, persistent connection pooling, and bounded TTL caching.
- **Export Formats**: Download conversations cleanly formatted in Microsoft Word (`.docx`), Adobe PDF (`.pdf`), or Plain Text (`.txt`).

---

## Setup & Quickstart

```bash
cd researchagent
# Activate environment
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure credentials
copy .env.example .env
```

Configure your LLM provider in `.env`:

| Provider    | AIRA_BASE_URL                    | Example AIRA_MODEL        |
|-------------|----------------------------------|---------------------------|
| OpenAI      | https://api.openai.com/v1        | gpt-4o-mini               |
| Groq        | https://api.groq.com/openai/v1   | llama-3.3-70b-versatile   |
| OpenRouter  | https://openrouter.ai/api/v1     | anthropic/claude-sonnet-4 |

---

## Running the Application

```bash
# Run server using batch launcher:
run.bat

# Or directly with Uvicorn:
.venv\Scripts\python -m uvicorn aira.server:app --port 8000
```

Access the web interface at `http://localhost:8000`.

---

## API Endpoints & Security Specifications

| Endpoint | Method | Auth Guard | Rate Limit | Description |
|----------|--------|------------|------------|-------------|
| `/api/health` | GET | None (Public) | 120 req/min | System health check and model status |
| `/api/system/traffic` | GET | None (Public) | 60 req/min | Concurrency, RPM, and load balancer status |
| `/api/auth/register` | POST | None (Public) | 10 req/min | Register user account with PBKDF2 hashing |
| `/api/auth/login` | POST | None (Public) | 10 req/min | Authenticate credentials and issue session token |
| `/api/auth/me` | GET | Required (Bearer) | 60 req/min | Retrieve current authenticated user profile |
| `/api/auth/logout` | POST | Required (Bearer) | 60 req/min | Invalidate session token |
| `/api/chats` | GET | Optional | 60 req/min | List chats (scoped to authenticated user) |
| `/api/chats/{chat_id}` | GET | Optional | 60 req/min | Retrieve single chat history |
| `/api/chats` | POST | Optional | 60 req/min | Save/update chat with user ownership |
| `/api/chats/{chat_id}` | DELETE | Optional | 60 req/min | Delete chat from database |
| `/api/upload` | POST | Optional | 20 req/min | Upload single document (max 25 MB) |
| `/api/upload-batch` | POST | Optional | 10 req/min | Upload batch documents (max 100 files, bounded concurrency) |
| `/api/documents/catalog` | GET | None (Public) | 60 req/min | Document catalog overview |
| `/api/documents` | DELETE | Required / Optional | 10 req/min | Clear indexed document repository |
| `/api/ask` | POST | None (Public) | 30 req/min | SSE streaming agent research/debugging loop |
| `/api/demo-search` | GET | None (Public) | 30 req/min | Web search test query |
| `/api/export` | POST | None (Public) | 30 req/min | Export chat to PDF, DOCX, or TXT |

---

## Running the Automated Test Suite

```bash
# Run comprehensive audit suite:
.venv\Scripts\python -m unittest tests/test_audit_suite.py

# Run all endpoint & database verification tests:
.venv\Scripts\python tests/test_server_endpoints.py
.venv\Scripts\python tests/test_db_and_auth.py
.venv\Scripts\python tests/test_load_balancer.py
.venv\Scripts\python tests/test_batch_pdf.py
.venv\Scripts\python tests/test_full_verification.py
```

---

## Eval Harness (Spec Rubric)

```bash
.venv\Scripts\python -m aira.eval                 # Run all 3 evaluation cases
.venv\Scripts\python -m aira.eval --only 3         # Run specific test case
.venv\Scripts\python -m aira.eval --save out.json  # Save judge results
```
