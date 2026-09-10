r"""AIRA Database Manager & Data Viewer (database.py)

This file manages the MySQL database connection using mysql.connector and SQLAlchemy ORM,
automatically creating schemas, tables, and displaying stored conversation data.

Usage:
  Run directly:
    .venv\Scripts\python.exe database.py
"""
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure UTF-8 printing in Windows console
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import mysql.connector
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, BigInteger, DateTime, ForeignKey, Index, text, select
)
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session, relationship

# Load configuration from .env
ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"

if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val

# Database settings
MYSQL_HOST = os.environ.get("DB_HOST") or os.environ.get("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.environ.get("DB_PORT") or os.environ.get("MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("DB_USER") or os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("DB_PASSWORD") or os.environ.get("MYSQL_PASSWORD", "Naidu@2728")
MYSQL_DATABASE = os.environ.get("DB_NAME") or os.environ.get("MYSQL_DATABASE", "database.py")
LOCAL_SQL_FILE = ROOT / "aira_storage.db"

Base = declarative_base()


def utc_now() -> datetime:
    """Return timezone-safe UTC datetime."""
    return datetime.now(timezone.utc)


# =====================================================================
# Direct MySQL Connector Test Function
# =====================================================================
def test_mysql_connector():
    """Establish MySQL connection with mysql.connector, auto-create database, and query version."""
    print(f"\n[Connecting to MySQL on {MYSQL_HOST}:{MYSQL_PORT} with user '{MYSQL_USER}']...")
    try:
        # 1. Connect without specific DB to ensure schema exists
        conn_init = mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            connection_timeout=3,
        )
        cur_init = conn_init.cursor()
        cur_init.execute(f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DATABASE}` DEFAULT CHARACTER SET utf8mb4;")
        cur_init.close()
        conn_init.close()

        # 2. Establish connection to the target database
        db = mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DATABASE,
            connection_timeout=3,
        )

        cursor = db.cursor()

        # Test the connection by running a simple query
        cursor.execute("SELECT VERSION()")
        row = cursor.fetchone()
        version = row[0] if (row and isinstance(row, (tuple, list))) else "Unknown"
        print(f"Connected successfully! MySQL Version: {version}")

        # Always close the connection when done
        cursor.close()
        db.close()
        return True
    except Exception as e:
        print(f"[!] Notice: Could not connect to MySQL: {e}")
        print(f"    --> Falling back to local SQLite storage ({LOCAL_SQL_FILE.name}) to keep system online.\n")
        return False


# =====================================================================
# SQLAlchemy ORM Models
# =====================================================================
class User(Base):
    __tablename__ = "users"

    user_id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=utc_now)

    login_logs = relationship("LoginLogModel", back_populates="user", cascade="all, delete-orphan")
    chats = relationship("ChatModel", back_populates="user", cascade="all, delete-orphan")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "email": self.email,
            "created_at": str(self.created_at),
        }


class LoginLogModel(Base):
    __tablename__ = "login_logs"

    log_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    login_time = Column(DateTime, default=utc_now)
    ip_address = Column(String(45), nullable=True)

    user = relationship("User", back_populates="login_logs")


class ChatModel(Base):
    __tablename__ = "chats"

    chat_id = Column(Integer, primary_key=True, autoincrement=True)
    sender_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=True, index=True)
    message_text = Column(Text, nullable=False)
    sent_at = Column(DateTime, default=utc_now)

    user = relationship("User", back_populates="chats")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "sender_id": self.sender_id,
            "message_text": self.message_text,
            "sent_at": str(self.sent_at),
        }


# =====================================================================
# Database Engine Initialization
# =====================================================================
def get_db_engine():
    """Connects to MySQL or falls back to SQLite, auto-creating all tables."""
    import urllib.parse

    if MYSQL_PASSWORD or os.environ.get("USE_MYSQL") == "1":
        try:
            # First ensure database exists via mysql.connector
            conn_init = mysql.connector.connect(
                host=MYSQL_HOST,
                port=MYSQL_PORT,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD,
                connection_timeout=2,
            )
            cur_init = conn_init.cursor()
            cur_init.execute(f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DATABASE}` DEFAULT CHARACTER SET utf8mb4;")
            cur_init.close()
            conn_init.close()

            encoded_user = urllib.parse.quote_plus(MYSQL_USER)
            encoded_pwd = urllib.parse.quote_plus(MYSQL_PASSWORD)
            mysql_url = f"mysql+pymysql://{encoded_user}:{encoded_pwd}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"
            
            engine = create_engine(mysql_url, pool_pre_ping=True)
            Base.metadata.create_all(bind=engine)
            return engine, f"MySQL ({MYSQL_USER}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE})"
        except Exception:
            pass

    # Local SQLite database fallback
    sqlite_url = f"sqlite:///{LOCAL_SQL_FILE}"
    engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})
    with engine.connect() as con:
        con.execute(text("PRAGMA journal_mode=WAL;"))
        con.execute(text("PRAGMA foreign_keys=ON;"))
        con.commit()
    Base.metadata.create_all(bind=engine)
    return engine, f"SQLite ({LOCAL_SQL_FILE.name})"


# =====================================================================
# Data Viewing & Storing Functions
# =====================================================================
def view_all_data() -> Dict[str, Any]:
    """Fetch all users, login logs, and chat messages from chat_app."""
    engine, backend = get_db_engine()
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        users = [u.to_dict() for u in session.query(User).all()]
        logs = [
            {"log_id": l.log_id, "user_id": l.user_id, "login_time": str(l.login_time), "ip_address": l.ip_address}
            for l in session.query(LoginLogModel).all()
        ]
        chats = [c.to_dict() for c in session.query(ChatModel).order_by(ChatModel.chat_id.desc()).all()]

        return {
            "backend": backend,
            "total_users": len(users),
            "total_logs": len(logs),
            "total_chats": len(chats),
            "users": users,
            "login_logs": logs,
            "chats": chats,
        }
    finally:
        session.close()


def export_data_to_file(filepath: str = "view_my_data.json") -> str:
    """Export all stored database records into a clean, human-readable JSON file."""
    data = view_all_data()
    target = ROOT / filepath
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(target)


def print_data_report():
    """Prints a clear, human-readable report of all data stored in the chat_app database."""
    # Run direct connector check
    test_mysql_connector()

    data = view_all_data()
    print("=" * 70)
    print(f"  AIRA CHAT_APP DATABASE REPORT  [Storage: {data['backend']}]")
    print("=" * 70)

    # 1. Users
    print(f"\n[1] USERS TABLE ({data['total_users']} registered):")
    if not data["users"]:
        print("    (No registered users yet)")
    else:
        for u in data["users"][:10]:
            print(f"    - User ID: {u['user_id']} | Username: {u['username']} | Email: {u['email']} | Created: {u['created_at']}")
        if len(data["users"]) > 10:
            print(f"    ... and {len(data['users']) - 10} more registered users.")

    # 2. Login Logs
    print(f"\n[2] LOGIN LOGS TABLE ({data['total_logs']} login records):")
    if not data["login_logs"]:
        print("    (No login logs yet)")
    else:
        for l in data["login_logs"][:10]:
            print(f"    - Log ID: {l['log_id']} | User ID: {l['user_id']} | Time: {l['login_time']} | IP: {l['ip_address']}")
        if len(data["login_logs"]) > 10:
            print(f"    ... and {len(data['login_logs']) - 10} more login records.")

    # 3. Chats
    print(f"\n[3] CHATS TABLE ({data['total_chats']} saved messages):")
    if not data["chats"]:
        print("    (No chat messages yet)")
    else:
        for c in data["chats"][:10]:
            text_preview = (c.get("message_text") or "").strip()
            if len(text_preview) > 100:
                text_preview = text_preview[:97] + "..."
            print(f"    - Chat ID: {c['chat_id']} | Sender ID: {c['sender_id']} | Time: {c['sent_at']}")
            print(f"      Message: {text_preview}")
        if len(data["chats"]) > 10:
            print(f"    ... and {len(data['chats']) - 10} more chat records.")

    print("\n" + "=" * 70)
    exported_path = export_data_to_file("view_my_data.json")
    print(f"  Exported readable copy to: {exported_path}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    print_data_report()
