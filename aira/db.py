"""Database and Credential Storage Module for Aira with SQLAlchemy ORM and MySQL connectivity.

Features:
- Direct MySQL Connectivity: Connects to MySQL `chat_app` database (with fallback to SQLite).
- Strict Schema Alignment: Matches `users`, `login_logs`, and `chats` tables.
- Google Sign-In & OAuth Integration: Finds or auto-registers Google users with secure session generation.
- Login History & IP Logging: Records every authentication event in `login_logs` table.
- Credential Protection: Salted PBKDF2-HMAC-SHA256 password hashing (100,000 rounds).
- Session Lifecycle: Secure session tokens with automatic expiration and timing-attack-safe validation.
- User Tenancy: Multi-tenant chat isolation keyed by authenticated user ID.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, BigInteger, DateTime, ForeignKey, Index, text, select, func
)
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session, relationship

from . import config

logger = logging.getLogger("aira.db")

LOCAL_SQL_FILE = config.LOCAL_SQL_PATH
LEGACY_AUTH_FILE = config.ROOT / "aira_auth_store.json"
LEGACY_CHAT_FILE = config.ROOT / "aira_chat_store.json"

Base = declarative_base()


def utc_now() -> datetime:
    """Return timezone-safe UTC datetime object."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return timezone-safe ISO 8601 UTC timestamp string."""
    return datetime.now(timezone.utc).isoformat()


# =====================================================================
# SQLAlchemy ORM Models (Matching MySQL chat_app schema)
# =====================================================================
class User(Base):
    __tablename__ = "users"

    user_id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=utc_now)

    # Relationships
    login_logs = relationship("LoginLogModel", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("SessionModel", back_populates="user", cascade="all, delete-orphan")
    chats = relationship("ChatModel", back_populates="user", cascade="all, delete-orphan")

    @property
    def id(self) -> int:
        """Alias user_id as id for application-wide compatibility."""
        return int(self.user_id)  # type: ignore

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.user_id,
            "user_id": self.user_id,
            "username": self.username,
            "email": self.email,
            "created_at": str(self.created_at) if self.created_at else utc_now_iso(),
        }


class LoginLogModel(Base):
    __tablename__ = "login_logs"

    log_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=True, index=True)
    login_time = Column(DateTime, default=utc_now)
    ip_address = Column(String(45), nullable=True)

    user = relationship("User", back_populates="login_logs")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "log_id": self.log_id,
            "user_id": self.user_id,
            "login_time": str(self.login_time),
            "ip_address": self.ip_address,
        }


class SessionModel(Base):
    __tablename__ = "sessions"

    token = Column(String(96), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=utc_now)
    expires_at = Column(String(64), nullable=False, index=True)

    user = relationship("User", back_populates="sessions")


class ChatModel(Base):
    __tablename__ = "chats"

    chat_id = Column(Integer, primary_key=True, autoincrement=True)
    sender_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=True, index=True)
    message_text = Column(Text, nullable=False)
    sent_at = Column(DateTime, default=utc_now)

    user = relationship("User", back_populates="chats")

    def to_dict(self) -> Dict[str, Any]:
        # Parse JSON payload if encoded, otherwise return plain message text
        msg_content = str(self.message_text or "")
        try:
            parsed = json.loads(msg_content)
            if isinstance(parsed, dict) and "messages" in parsed:
                return {
                    "id": f"c_{self.chat_id}",
                    "chat_id": self.chat_id,
                    "user_id": self.sender_id,
                    "sender_id": self.sender_id,
                    "title": parsed.get("title") or "Chat",
                    "kind": parsed.get("kind") or "research",
                    "messages": parsed.get("messages", []),
                    "created": parsed.get("created") or int(time.time() * 1000),
                    "updated": parsed.get("updated") or int(time.time() * 1000),
                    "sent_at": str(self.sent_at),
                }
        except Exception:
            pass

        return {
            "chat_id": self.chat_id,
            "sender_id": self.sender_id,
            "user_id": self.sender_id,
            "message_text": self.message_text,
            "sent_at": str(self.sent_at),
        }


# =====================================================================
# Database State & Session Factory
# =====================================================================
_engine = None
_SessionFactory = None
_active_backend: str = "uninitialized"
_db_initialized: bool = False


def utc_now_iso() -> str:
    """Return timezone-safe ISO 8601 UTC timestamp string."""
    return datetime.now(timezone.utc).isoformat()


# =====================================================================
# Credential Hashing & Security (PBKDF2-HMAC-SHA256)
# =====================================================================
def hash_password(password: str, salt: Optional[str] = None) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with 100,000 iterations.
    
    Returns a unified string `salt$derived_hex` that easily fits in varchar(255).
    """
    if salt is None:
        salt = secrets.token_hex(16)

    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations=100_000,
    )
    return f"{salt}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Timing-attack-safe password verification against stored `salt$hash`."""
    if not stored_hash or "$" not in stored_hash:
        return False
    try:
        salt, expected_derived = stored_hash.split("$", 1)
        test_hash = hash_password(password, salt=salt)
        return secrets.compare_digest(test_hash, stored_hash)
    except Exception:
        return False


# =====================================================================
# Automatic Database & Table Generation (ORM)
# =====================================================================
def _ensure_mysql_database(host: str, port: int, user: str, password: str, database: str):
    """Ensure MySQL database exists before creating tables."""
    import pymysql  # type: ignore
    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        connect_timeout=3,
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            safe_db = database.replace("`", "``")
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{safe_db}` DEFAULT CHARACTER SET utf8mb4;")
    finally:
        conn.close()


def _migrate_sqlite_schema(engine: Any):
    """Automatically upgrades legacy SQLite tables to matching SQLAlchemy ORM schema."""
    if "sqlite" not in str(engine.url):
        return

    with engine.begin() as con:
        # 1. Check users table
        has_users = con.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='users';")).fetchone()
        if has_users:
            cols = [r[1] for r in con.execute(text("PRAGMA table_info(users);")).fetchall()]
            if "user_id" not in cols and "id" in cols:
                logger.info("Migrating legacy SQLite users table to new ORM schema (user_id)...")
                legacy_users = con.execute(text("SELECT id, username, email, password_hash, salt, created_at FROM users;")).fetchall()
                con.execute(text("ALTER TABLE users RENAME TO users_legacy_backup;"))
                con.execute(text("""
                    CREATE TABLE users (
                        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username VARCHAR(50) NOT NULL UNIQUE,
                        email VARCHAR(100) NOT NULL UNIQUE,
                        password_hash VARCHAR(255) NOT NULL,
                        created_at DATETIME
                    );
                """))
                for row in legacy_users:
                    uid, uname, uemail, phash, salt, cat = row
                    combined_hash = f"{salt}${phash}" if salt and "$" not in str(phash) else phash
                    con.execute(
                        text("INSERT INTO users (user_id, username, email, password_hash, created_at) VALUES (:uid, :un, :em, :ph, :ca);"),
                        {"uid": uid, "un": uname, "em": uemail, "ph": combined_hash, "ca": cat}
                    )
                con.execute(text("DROP TABLE users_legacy_backup;"))

        # 2. Check chats table
        has_chats = con.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='chats';")).fetchone()
        if has_chats:
            cols = [r[1] for r in con.execute(text("PRAGMA table_info(chats);")).fetchall()]
            if "chat_id" not in cols and "messages_json" in cols:
                logger.info("Migrating legacy SQLite chats table to new ORM schema (chat_id, sender_id, message_text)...")
                legacy_chats = con.execute(text("SELECT id, title, kind, messages_json, created, updated, user_id FROM chats;")).fetchall()
                con.execute(text("ALTER TABLE chats RENAME TO chats_legacy_backup;"))
                con.execute(text("""
                    CREATE TABLE chats (
                        chat_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        sender_id INTEGER,
                        message_text TEXT NOT NULL,
                        sent_at DATETIME,
                        FOREIGN KEY(sender_id) REFERENCES users (user_id) ON DELETE CASCADE
                    );
                """))
                for row in legacy_chats:
                    cid_raw, title, kind, msgs_json, created, updated, uid = row
                    int_id = None
                    if isinstance(cid_raw, str):
                        digits = re.sub(r"\D", "", cid_raw)
                        if digits:
                            int_id = int(digits)
                    elif isinstance(cid_raw, int):
                        int_id = cid_raw

                    try:
                        msgs = json.loads(msgs_json) if msgs_json else []
                    except Exception:
                        msgs = []

                    payload = json.dumps({
                        "title": title or "Chat",
                        "kind": kind or "research",
                        "messages": msgs,
                        "created": created or int(time.time() * 1000),
                        "updated": updated or int(time.time() * 1000),
                    }, ensure_ascii=False)

                    if int_id is not None:
                        con.execute(
                            text("INSERT INTO chats (chat_id, sender_id, message_text, sent_at) VALUES (:cid, :sid, :msg, :sent);"),
                            {"cid": int_id, "sid": uid, "msg": payload, "sent": datetime.now(timezone.utc)}
                        )
                    else:
                        con.execute(
                            text("INSERT INTO chats (sender_id, message_text, sent_at) VALUES (:sid, :msg, :sent);"),
                            {"sid": uid, "msg": payload, "sent": datetime.now(timezone.utc)}
                        )
                con.execute(text("DROP TABLE chats_legacy_backup;"))


def _init_orm_engine() -> Tuple[Any, str, Dict[str, Any]]:
    """Initialize SQLAlchemy ORM engine and ensure all tables exist."""
    global _engine, _SessionFactory, _active_backend

    # 1. Try MySQL if configured
    if config.MYSQL_PASSWORD or os.environ.get("USE_MYSQL") == "1":
        try:
            _ensure_mysql_database(
                host=config.MYSQL_HOST,
                port=config.MYSQL_PORT,
                user=config.MYSQL_USER,
                password=config.MYSQL_PASSWORD,
                database=config.MYSQL_DATABASE,
            )

            encoded_user = urllib.parse.quote_plus(config.MYSQL_USER)
            encoded_pwd = urllib.parse.quote_plus(config.MYSQL_PASSWORD)
            encoded_db = config.MYSQL_DATABASE

            mysql_url = f"mysql+pymysql://{encoded_user}:{encoded_pwd}@{config.MYSQL_HOST}:{config.MYSQL_PORT}/{encoded_db}?charset=utf8mb4"

            engine = create_engine(
                mysql_url,
                pool_size=config.MYSQL_POOL_SIZE,
                pool_recycle=config.MYSQL_POOL_RECYCLE,
                pool_pre_ping=True,
                echo=False,
            )
            # Auto-create all tables in MySQL
            Base.metadata.create_all(bind=engine)

            _engine = engine
            _SessionFactory = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False))
            _active_backend = "mysql"

            logger.info("SQLAlchemy ORM connected to MySQL at %s:%d/%s (tables verified)", config.MYSQL_HOST, config.MYSQL_PORT, config.MYSQL_DATABASE)
            return engine, "mysql", {
                "backend": "mysql",
                "host": config.MYSQL_HOST,
                "port": config.MYSQL_PORT,
                "database": config.MYSQL_DATABASE,
                "status": "connected",
                "tables_created": ["users", "login_logs", "sessions", "chats"],
            }
        except Exception as e:
            logger.info("MySQL connection unavailable (%s). Falling back to SQLite Local SQL.", e)

    # 2. SQLite Local SQL Engine fallback
    LOCAL_SQL_FILE.parent.mkdir(parents=True, exist_ok=True)
    sqlite_url = f"sqlite:///{LOCAL_SQL_FILE}"
    engine = create_engine(
        sqlite_url,
        connect_args={"check_same_thread": False, "timeout": 10.0},
        echo=False,
    )
    with engine.connect() as con:
        con.execute(text("PRAGMA journal_mode=WAL;"))
        con.execute(text("PRAGMA foreign_keys=ON;"))
        con.commit()

    _migrate_sqlite_schema(engine)
    Base.metadata.create_all(bind=engine)

    _engine = engine
    _SessionFactory = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False))
    _active_backend = "local_sql"

    logger.info("SQLAlchemy ORM active on SQLite at %s", LOCAL_SQL_FILE)
    return engine, "local_sql", {
        "backend": "local_sql",
        "engine": "sqlite3",
        "database_file": LOCAL_SQL_FILE.name,
        "status": "connected",
        "tables_created": ["users", "login_logs", "sessions", "chats"],
    }


def _get_session():
    """Return a scoped database session, initializing the ORM engine if needed."""
    global _SessionFactory
    if _SessionFactory is None:
        _init_orm_engine()
    return _SessionFactory()  # type: ignore


def _migrate_legacy_data():
    """Migrate legacy JSON credentials and chat history into ORM database if present."""
    session = _get_session()
    try:
        # Migrate users
        if LEGACY_AUTH_FILE.exists():
            try:
                raw = json.loads(LEGACY_AUTH_FILE.read_text(encoding="utf-8"))
                users = raw.get("users", [])
                for u in users:
                    if not session.query(User).filter_by(username=u["username"]).first():
                        pwd_val = u.get("password_hash", "")
                        salt_val = u.get("salt", "")
                        combined_hash = f"{salt_val}${pwd_val}" if salt_val and "$" not in pwd_val else pwd_val
                        user_obj = User(
                            username=u["username"],
                            email=u["email"],
                            password_hash=combined_hash,
                        )
                        session.add(user_obj)
                session.commit()
            except Exception as e:
                logger.warning("Could not migrate legacy auth data: %s", e)
                session.rollback()
    finally:
        session.close()


async def init_db() -> Dict[str, Any]:
    """Initialize database tables and connections."""
    global _db_initialized
    _, _, status_info = await asyncio.to_thread(_init_orm_engine)
    await asyncio.to_thread(_migrate_legacy_data)
    _db_initialized = True
    return status_info


async def shutdown_db():
    """Cleanup database connections on shutdown."""
    global _engine, _SessionFactory
    if _SessionFactory is not None:
        _SessionFactory.remove()
    if _engine is not None:
        _engine.dispose()


# =====================================================================
# Login Logging & IP Tracking
# =====================================================================
def _orm_log_login(user_id: int, ip_address: Optional[str] = None):
    """Insert a record into the login_logs table."""
    session = _get_session()
    try:
        log_entry = LoginLogModel(
            user_id=user_id,
            login_time=utc_now(),
            ip_address=ip_address or "127.0.0.1",
        )
        session.add(log_entry)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.warning("Could not record login log: %s", e)
    finally:
        session.close()


async def log_user_login(user_id: int, ip_address: Optional[str] = None):
    """Asynchronously record user login into the database."""
    if _SessionFactory is None:
        await init_db()
    await asyncio.to_thread(_orm_log_login, user_id, ip_address)


# =====================================================================
# User Registration & Authentication (ORM)
# =====================================================================
def _orm_register_user(username: str, email: str, pwd_hash: str) -> Dict[str, Any]:
    session = _get_session()
    try:
        existing = session.query(User).filter(
            (User.username == username) | (User.email == email)
        ).first()
        if existing:
            return {"ok": False, "error": "Username or email is already registered"}

        new_user = User(
            username=username,
            email=email,
            password_hash=pwd_hash,
            created_at=utc_now(),
        )
        session.add(new_user)
        session.commit()
        return {"ok": True, "user_id": new_user.user_id}
    except Exception as e:
        session.rollback()
        return {"ok": False, "error": str(e)}
    finally:
        session.close()


def _orm_login_user(target: str) -> Optional[Dict[str, Any]]:
    session = _get_session()
    try:
        user = session.query(User).filter(
            (User.username == target) | (User.email == target)
        ).first()
        if not user:
            return None
        return {
            "id": user.user_id,
            "user_id": user.user_id,
            "username": user.username,
            "email": user.email,
            "password_hash": user.password_hash,
            "created_at": str(user.created_at),
        }
    finally:
        session.close()


def _orm_create_session(user_id: int, token: str, expires_at: str):
    session = _get_session()
    try:
        sess = SessionModel(
            token=token,
            user_id=user_id,
            created_at=utc_now(),
            expires_at=expires_at,
        )
        session.add(sess)
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def _orm_get_user_from_token(token: str, now_iso: str) -> Optional[Dict[str, Any]]:
    session = _get_session()
    try:
        sess = session.query(SessionModel).filter(
            SessionModel.token == token,
            SessionModel.expires_at > now_iso,
        ).first()
        if sess and sess.user:
            return sess.user.to_dict()
        return None
    finally:
        session.close()


def _orm_logout(token: str) -> bool:
    session = _get_session()
    try:
        deleted = session.query(SessionModel).filter_by(token=token).delete()
        session.commit()
        return deleted > 0
    except Exception:
        session.rollback()
        return False
    finally:
        session.close()


async def register_user(username: str, email: str, password: str, ip_address: Optional[str] = None) -> Dict[str, Any]:
    """Register a new user account and log the event in MySQL database."""
    if _SessionFactory is None:
        await init_db()

    username = username.strip()
    email = email.strip().lower()
    if not username or len(username) < 3:
        return {"ok": False, "error": "Username must be at least 3 characters"}
    if not email or "@" not in email:
        return {"ok": False, "error": "A valid email address is required"}
    if not password or len(password) < 6:
        return {"ok": False, "error": "Password must be at least 6 characters"}

    pwd_hash = hash_password(password)
    res = await asyncio.to_thread(_orm_register_user, username, email, pwd_hash)
    if not res.get("ok"):
        return res

    user_id = res["user_id"]
    # Log the registration event into login_logs
    await log_user_login(user_id, ip_address)
    token_info = await create_session(user_id)
    return {
        "ok": True,
        "user": {"id": user_id, "user_id": user_id, "username": username, "email": email},
        "token": token_info["token"],
    }


async def login_user(username_or_email: str, password: str, ip_address: Optional[str] = None) -> Dict[str, Any]:
    """Authenticate a user using timing-safe password hash check, log to login_logs, and issue session token."""
    if _SessionFactory is None:
        await init_db()

    target = username_or_email.strip()
    if not target or not password:
        return {"ok": False, "error": "Username/email and password are required"}

    user = await asyncio.to_thread(_orm_login_user, target)
    if not user:
        return {"ok": False, "error": "Invalid username or password"}

    if not verify_password(password, user["password_hash"]):
        return {"ok": False, "error": "Invalid username or password"}

    # Log successful login to login_logs table
    await log_user_login(user["user_id"], ip_address)
    token_info = await create_session(user["user_id"])
    return {
        "ok": True,
        "user": {"id": user["user_id"], "user_id": user["user_id"], "username": user["username"], "email": user["email"]},
        "token": token_info["token"],
    }


# =====================================================================
# Google Sign-In & OAuth Handling
# =====================================================================
def _orm_google_auth(google_id: str, email: str, name: str) -> Tuple[Dict[str, Any], bool]:
    """Find or register a user logging in via Google OAuth."""
    session = _get_session()
    try:
        # Check by email or Google ID
        user = session.query(User).filter(
            (User.email == email) | (User.password_hash == f"google_oauth:{google_id}")
        ).first()

        is_new = False
        if not user:
            # Generate unique clean username
            clean_name = re.sub(r"[^a-zA-Z0-9_]", "", name.lower().replace(" ", "_"))[:20]
            if not clean_name or len(clean_name) < 3:
                clean_name = email.split("@")[0][:20]
            candidate_username = clean_name
            suffix = 1
            while session.query(User).filter_by(username=candidate_username).first():
                candidate_username = f"{clean_name[:15]}_{secrets.token_hex(2)}"
                suffix += 1

            user = User(
                username=candidate_username,
                email=email,
                password_hash=f"google_oauth:{google_id}",
                created_at=utc_now(),
            )
            session.add(user)
            session.commit()
            is_new = True

        user_dict = user.to_dict()
        return user_dict, is_new
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def google_auth_or_register(
    google_id: str,
    email: str,
    name: str,
    avatar_url: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> Dict[str, Any]:
    """Process Google Sign-In: find or create user in MySQL, log login event, and return session token."""
    if _SessionFactory is None:
        await init_db()

    email = email.strip().lower()
    google_id = (google_id or "").strip()
    name = (name or email.split("@")[0]).strip()

    if not email or "@" not in email:
        return {"ok": False, "error": "Invalid email from Google account"}
    if not google_id:
        return {"ok": False, "error": "Invalid Google account ID"}

    user_dict, is_new = await asyncio.to_thread(_orm_google_auth, google_id, email, name)
    user_id = user_dict["user_id"]

    # Log Google login in login_logs
    await log_user_login(user_id, ip_address)

    token_info = await create_session(user_id)
    if avatar_url:
        user_dict["avatar_url"] = avatar_url

    return {
        "ok": True,
        "is_new_user": is_new,
        "user": user_dict,
        "token": token_info["token"],
    }


# =====================================================================
# Session Management
# =====================================================================
async def create_session(user_id: int, duration_days: int = 7) -> Dict[str, Any]:
    """Create a secure session token in ORM database."""
    if _SessionFactory is None:
        await init_db()

    token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
    expires_str = expires_at.isoformat()

    await asyncio.to_thread(_orm_create_session, user_id, token, expires_str)
    return {"token": token, "expires_at": expires_str}


async def get_user_from_token(token: str) -> Optional[Dict[str, Any]]:
    """Retrieve user corresponding to an active session token from ORM database."""
    if not token:
        return None
    if _SessionFactory is None:
        await init_db()

    now_iso = utc_now_iso()
    return await asyncio.to_thread(_orm_get_user_from_token, token, now_iso)


async def logout_session(token: str) -> bool:
    """Invalidate a session token in ORM database."""
    if not token:
        return False
    if _SessionFactory is None:
        await init_db()

    return await asyncio.to_thread(_orm_logout, token)


# =====================================================================
# Chat Persistence & Multi-Tenancy (ORM)
# =====================================================================
def _orm_save_or_update_chat_record(sender_id: Optional[int], chat_id_val: Optional[int], message_payload: str) -> int:
    session = _get_session()
    try:
        chat = None
        if chat_id_val is not None:
            q = session.query(ChatModel).filter(ChatModel.chat_id == chat_id_val)
            if sender_id is not None:
                q = q.filter(ChatModel.sender_id == sender_id)
            chat = q.first()

        if chat:
            chat.message_text = message_payload
            chat.sent_at = utc_now()
            session.commit()
            return int(chat.chat_id)  # type: ignore
        else:
            new_chat = ChatModel(
                sender_id=sender_id,
                message_text=message_payload,
                sent_at=utc_now(),
            )
            session.add(new_chat)
            session.commit()
            return int(new_chat.chat_id)  # type: ignore
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _orm_get_chats_list(user_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
    session = _get_session()
    try:
        if user_id is None:
            return []
        q = session.query(ChatModel).filter(ChatModel.sender_id == user_id)
        q = q.order_by(ChatModel.chat_id.desc()).limit(limit)

        results = []
        for c in q.all():
            results.append(c.to_dict())
        return results
    finally:
        session.close()


def _orm_get_chat_by_id(chat_id_val: int, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    session = _get_session()
    try:
        q = session.query(ChatModel).filter(ChatModel.chat_id == chat_id_val)
        if user_id is not None:
            q = q.filter(ChatModel.sender_id == user_id)
        chat = q.first()
        return chat.to_dict() if chat else None
    finally:
        session.close()


def _orm_delete_chat(chat_id_val: int, user_id: Optional[int] = None) -> bool:
    session = _get_session()
    try:
        q = session.query(ChatModel).filter(ChatModel.chat_id == chat_id_val)
        if user_id is not None:
            q = q.filter(ChatModel.sender_id == user_id)
        deleted = q.delete(synchronize_session=False)
        session.commit()
        return deleted > 0
    except Exception:
        session.rollback()
        return False
    finally:
        session.close()


async def save_chat_to_db(chat_data: Dict[str, Any], user_id: Optional[int] = None) -> Dict[str, Any]:
    """Persist or update chat conversation and messages into MySQL `chats` table."""
    if _SessionFactory is None:
        await init_db()

    uid = user_id if user_id is not None else chat_data.get("user_id")
    title = (chat_data.get("title") or "New Chat")[:200]
    kind = chat_data.get("kind") or "research"
    messages = chat_data.get("messages") or []
    created = int(chat_data.get("created") or time.time() * 1000)
    updated = int(chat_data.get("updated") or time.time() * 1000)

    # Encode structured payload into message_text for full multi-message fidelity
    payload_json = json.dumps({
        "title": title,
        "kind": kind,
        "messages": messages,
        "created": created,
        "updated": updated,
    }, ensure_ascii=False)

    raw_id = chat_data.get("id") or chat_data.get("chat_id")
    int_id = None
    if isinstance(raw_id, int):
        int_id = raw_id
    elif isinstance(raw_id, str):
        digits = re.sub(r"\D", "", raw_id)
        if digits:
            int_id = int(digits)

    chat_id = await asyncio.to_thread(_orm_save_or_update_chat_record, uid, int_id, payload_json)

    return {
        "id": f"c_{chat_id}",
        "chat_id": chat_id,
        "user_id": uid,
        "sender_id": uid,
        "title": title,
        "kind": kind,
        "messages": messages,
        "created": created,
        "updated": updated,
    }


async def get_chats_from_db(kind: Optional[str] = None, user_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieve chats from ORM database."""
    if _SessionFactory is None:
        await init_db()
    raw_chats = await asyncio.to_thread(_orm_get_chats_list, user_id, limit)
    if kind:
        return [c for c in raw_chats if c.get("kind") == kind]
    return raw_chats


async def get_chat_from_db(chat_id: str, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Retrieve a single chat with its messages from database."""
    if _SessionFactory is None:
        await init_db()
    # Handle both integer chat_id and prefixed "c_123"
    int_id = None
    if isinstance(chat_id, int):
        int_id = chat_id
    elif isinstance(chat_id, str):
        digits = re.sub(r"\D", "", chat_id)
        if digits:
            int_id = int(digits)
    if int_id is None:
        return None
    return await asyncio.to_thread(_orm_get_chat_by_id, int_id, user_id)


async def delete_chat_from_db(chat_id: str, user_id: Optional[int] = None) -> bool:
    """Delete a chat from database."""
    if _SessionFactory is None:
        await init_db()
    int_id = None
    if isinstance(chat_id, int):
        int_id = chat_id
    elif isinstance(chat_id, str):
        digits = re.sub(r"\D", "", chat_id)
        if digits:
            int_id = int(digits)
    if int_id is None:
        return False
    return await asyncio.to_thread(_orm_delete_chat, int_id, user_id)
