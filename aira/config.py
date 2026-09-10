"""Central config: reads .env (never overriding real env vars) and exposes settings."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


load_env()

# API and Model configuration
API_KEY = os.environ.get("AIRA_API_KEY", "")
BASE_URL = os.environ.get("AIRA_BASE_URL", "https://api.openai.com/v1")
MODEL = os.environ.get("AIRA_MODEL", "gpt-4o-mini")
JUDGE_MODEL = os.environ.get("AIRA_JUDGE_MODEL", MODEL)
# If the primary model fails or returns a bad response, retry with these in order.
FALLBACK_MODELS = [
    m.strip()
    for m in os.environ.get(
        "AIRA_FALLBACK_MODELS",
        "openai/gpt-oss-20b,mistralai/mistral-nemo",
    ).split(",")
    if m.strip()
]
MAX_TOOL_ROUNDS = int(os.environ.get("AIRA_MAX_TOOL_ROUNDS", "8"))
REQUEST_TIMEOUT = float(os.environ.get("AIRA_TIMEOUT", "180"))
# Temperature: set AIRA_TEMPERATURE="" in .env to omit the parameter entirely
# (needed by some reasoning models that reject temperature).
TEMPERATURE = os.environ.get("AIRA_TEMPERATURE", "0.2")
MAX_TOKENS = os.environ.get("AIRA_MAX_TOKENS", "4096")

# Secondary / High-Traffic API configuration
SECONDARY_API_KEY = os.environ.get("AIRA_SECONDARY_API_KEY", "") or API_KEY
SECONDARY_BASE_URL = os.environ.get("AIRA_SECONDARY_BASE_URL", BASE_URL)
SECONDARY_MODEL = os.environ.get("AIRA_SECONDARY_MODEL", "google/gemini-2.5-flash-lite")

# Concurrency & Load Balancing (tuned for 50 concurrent users)
MAX_CONCURRENT_REQUESTS = int(os.environ.get("AIRA_MAX_CONCURRENT_REQUESTS", "50"))
HIGH_TRAFFIC_CONCURRENCY = int(os.environ.get("AIRA_HIGH_TRAFFIC_CONCURRENCY", "10"))
HIGH_TRAFFIC_RPM = int(os.environ.get("AIRA_HIGH_TRAFFIC_RPM", "60"))

# MySQL Database & ORM Configuration
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Fallback to individual DB_* or MYSQL_* variables
MYSQL_HOST = os.environ.get("DB_HOST") or os.environ.get("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.environ.get("DB_PORT") or os.environ.get("MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("DB_USER") or os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("DB_PASSWORD") or os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.environ.get("DB_NAME") or os.environ.get("MYSQL_DATABASE", "database.py")
MYSQL_POOL_SIZE = int(os.environ.get("MYSQL_POOL_SIZE", "20"))
MYSQL_POOL_RECYCLE = int(os.environ.get("MYSQL_POOL_RECYCLE", "1800"))

# Parse DATABASE_URL if present
if DATABASE_URL:
    import re
    # Handle mysql://user:pass@host:port/dbname (including special chars in pass)
    m = re.match(r"^mysql(?:\+\w+)?://([^:]+):(.*)@([^:/]+)(?::(\d+))?/(.+)$", DATABASE_URL)
    if m:
        MYSQL_USER = m.group(1)
        MYSQL_PASSWORD = m.group(2).replace("%40", "@")
        MYSQL_HOST = m.group(3)
        if m.group(4):
            MYSQL_PORT = int(m.group(4))
        MYSQL_DATABASE = m.group(5)

# Local SQL Database Storage (SQLite)
LOCAL_SQL_PATH = Path(os.environ.get("AIRA_LOCAL_SQL_PATH", str(ROOT / "aira_storage.db")))

# Security & CORS settings
CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get("AIRA_CORS_ORIGINS", "*").split(",")
    if o.strip()
]
RATE_LIMIT_ENABLED = os.environ.get("AIRA_RATE_LIMIT_ENABLED", "1").lower() in ("1", "true", "yes")
AUTH_REQUIRED_FOR_DOCS = os.environ.get("AIRA_AUTH_REQUIRED_FOR_DOCS", "0").lower() in ("1", "true", "yes")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

