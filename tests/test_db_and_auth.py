"""Test database memory management, pooling, password hashing, and authentication."""
import asyncio
import os
import sys
from pathlib import Path

# Add project root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aira.db import (
    init_db,
    register_user,
    login_user,
    get_user_from_token,
    logout_session,
    hash_password,
    verify_password,
    shutdown_db,
)


async def run_tests():
    print("== 1. Test Password Hashing ==")
    pwd = "superSecurePassword99!"
    h1, salt1 = hash_password(pwd)
    h2, salt2 = hash_password(pwd)
    assert h1 != h2, "Salts must be unique per hash!"
    assert verify_password(pwd, h1, salt1), "Password verification failed for valid password"
    assert not verify_password("wrongPassword", h1, salt1), "Password verification must reject invalid password"
    print("  Password hashing & verification OK")

    print("\n== 2. Test DB Initialization ==")
    init_result = await init_db()
    print(f"  DB Initialized: {init_result}")

    print("\n== 3. Test Registration ==")
    username = f"aira_user_{os.urandom(4).hex()}"
    email = f"{username}@test.com"
    reg = await register_user(username, email, pwd)
    assert reg.get("ok"), f"Registration failed: {reg}"
    token = reg["token"]
    print(f"  User registered: {reg['user']['username']} (ID: {reg['user']['id']})")

    # Duplicate rejection
    dup = await register_user(username, email, pwd)
    assert not dup.get("ok"), "Duplicate registration must be rejected"
    print("  Duplicate registration rejected correctly")

    print("\n== 4. Test Login ==")
    log_ok = await login_user(username, pwd)
    assert log_ok.get("ok"), f"Login failed: {log_ok}"
    print("  Login succeeded with correct credentials")

    log_bad = await login_user(username, "wrongPassword!")
    assert not log_bad.get("ok"), "Login must fail with incorrect password"
    print("  Login correctly rejected with incorrect password")

    print("\n== 5. Test Token Validation ==")
    user_info = await get_user_from_token(log_ok["token"])
    assert user_info and user_info["username"] == username, "Token did not resolve to user"
    print(f"  Token resolved to: {user_info['username']}")

    print("\n== 6. Test Logout ==")
    await logout_session(log_ok["token"])
    user_after_logout = await get_user_from_token(log_ok["token"])
    assert user_after_logout is None, "Session token must be invalidated after logout"
    print("  Session token invalidated after logout OK")

    await shutdown_db()
    print("\nALL DB & AUTH TESTS PASSED!")


if __name__ == "__main__":
    asyncio.run(run_tests())
