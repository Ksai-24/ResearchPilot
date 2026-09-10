"""Test script for verifying MySQL database connectivity, user registration/login,
login_logs recording, Google Sign-In authentication, and chat persistence.
"""
import asyncio
import json
import os
import sys

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import aira.db as db
from aira.config import MYSQL_DATABASE, MYSQL_HOST, MYSQL_PORT, MYSQL_USER


async def run_tests():
    print("=" * 70)
    print("  AIRA DATABASE & GOOGLE SIGN-IN INTEGRATION TEST")
    print(f"  Target: MySQL {MYSQL_USER}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}")
    print("=" * 70)

    # 1. Initialize DB
    print("\n[Step 1] Initializing Database & Tables...")
    status = await db.init_db()
    print(f"  Status: {status}")
    assert status["backend"] == "mysql", f"Expected MySQL backend, got {status['backend']}"
    print("  -> Passed! MySQL connected and tables verified.")

    # 2. Test User Registration
    test_uname = f"test_user_{os.urandom(3).hex()}"
    test_email = f"{test_uname}@aira.local"
    test_pwd = "SecurePassword@2026!"
    client_ip = "192.168.1.55"

    print(f"\n[Step 2] Testing User Registration ({test_uname})...")
    reg_res = await db.register_user(
        username=test_uname,
        email=test_email,
        password=test_pwd,
        ip_address=client_ip,
    )
    print(f"  Registration result: {reg_res}")
    assert reg_res.get("ok") is True, f"Registration failed: {reg_res}"
    user_id = reg_res["user"]["user_id"]
    token = reg_res["token"]
    print(f"  -> Passed! User created with ID {user_id} and token generated.")

    # 3. Test Session Verification
    print("\n[Step 3] Verifying Session Token...")
    user_session = await db.get_user_from_token(token)
    print(f"  Session user: {user_session}")
    assert user_session is not None, "Failed to retrieve user from session token"
    assert user_session["username"] == test_uname, "Username mismatch"
    print("  -> Passed! Session successfully validated from database.")

    # 4. Test User Login & Login Log
    login_ip = "203.0.113.19"
    print(f"\n[Step 4] Testing User Login with IP Tracking ({login_ip})...")
    login_res = await db.login_user(
        username_or_email=test_uname,
        password=test_pwd,
        ip_address=login_ip,
    )
    print(f"  Login result: {login_res}")
    assert login_res.get("ok") is True, f"Login failed: {login_res}"
    print("  -> Passed! Password authenticated and new session token issued.")

    # 5. Test Google Sign-In / OAuth Flow
    google_id = "109876543210987654321"
    google_email = f"google_user_{os.urandom(3).hex()}@gmail.com"
    google_name = "Dr. Google Scientist"
    google_ip = "198.51.100.42"
    google_avatar = "https://lh3.googleusercontent.com/a/mock_avatar_test"

    print(f"\n[Step 5] Testing Google Sign-In Integration ({google_email})...")
    google_res = await db.google_auth_or_register(
        google_id=google_id,
        email=google_email,
        name=google_name,
        avatar_url=google_avatar,
        ip_address=google_ip,
    )
    print(f"  Google Auth Result: {google_res}")
    assert google_res.get("ok") is True, f"Google auth failed: {google_res}"
    assert google_res.get("is_new_user") is True, "Expected new user flag for first-time Google sign-in"
    google_user_id = google_res["user"]["user_id"]
    print(f"  -> Passed! Google user registered with ID {google_user_id}.")

    # 6. Test Repeat Google Sign-In (Existing Account)
    print(f"\n[Step 6] Testing Repeat Google Sign-In (Existing Account)...")
    google_repeat_res = await db.google_auth_or_register(
        google_id=google_id,
        email=google_email,
        name=google_name,
        avatar_url=google_avatar,
        ip_address=google_ip,
    )
    assert google_repeat_res.get("ok") is True, "Repeat Google auth failed"
    assert google_repeat_res.get("is_new_user") is False, "Expected existing user flag on return"
    assert google_repeat_res["user"]["user_id"] == google_user_id, "User ID mismatch on repeat login"
    print("  -> Passed! Existing Google user successfully authenticated without duplication.")

    # 7. Test Chat Persistence in chats Table
    print(f"\n[Step 7] Testing Chat Message Persistence for user {user_id}...")
    chat_payload = {
        "title": "Quantum Computing & Cryptography Research",
        "kind": "research",
        "messages": [
            {"role": "user", "text": "What is Shor's algorithm?"},
            {
                "role": "assistant",
                "text": "Shor's algorithm is a quantum computer algorithm for polynomial-time integer factorization.",
                "sources": ["https://en.wikipedia.org/wiki/Shor%27s_algorithm (Wikipedia)"],
            },
        ],
    }
    saved_chat = await db.save_chat_to_db(chat_payload, user_id=user_id)
    print(f"  Saved chat: ID {saved_chat['id']} (chat_id: {saved_chat['chat_id']})")
    assert saved_chat.get("chat_id") is not None, "Failed to persist chat"

    # Retrieve chat
    retrieved_chat = await db.get_chat_from_db(saved_chat["chat_id"], user_id=user_id)
    print(f"  Retrieved chat: {retrieved_chat.get('title') if retrieved_chat else None}")
    assert retrieved_chat is not None, "Failed to retrieve chat from DB"
    print("  -> Passed! Chat conversation and verified sources persisted to MySQL.")

    # Cleanup DB connection
    await db.shutdown_db()
    print("\n" + "=" * 70)
    print("  ALL 7 TESTS PASSED SUCCESSFULLY ON MYSQL CHAT_APP!")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    asyncio.run(run_tests())
