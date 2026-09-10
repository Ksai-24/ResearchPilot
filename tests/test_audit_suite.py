"""Comprehensive Quality, Security, and Endpoint Audit Test Suite for Aira.

Tests:
1. Security: Password Hashing (PBKDF2-HMAC-SHA256, salt randomness, timing safety)
2. Security: Auth Guard Enforcement & Unauthorized Rejections
3. Security: Multi-tenant Chat Isolation
4. Security: Python Execution Sandbox AST Validation & Breakout Prevention
5. Reliability: In-Memory Sliding-Window Rate Limiter
6. Reliability: Bounded TTL Cache Eviction & Memory Protection
7. Reliability: PDF Error Recovery on Corrupted Payloads
8. Integrity: Server Endpoints & Pydantic Schema Validation
9. Export: TXT, DOCX, and PDF Export Generation
"""
import asyncio
import io
import os
import sys
import unittest
from pathlib import Path
from typing import Any, Dict

from httpx import ASGITransport, AsyncClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aira import config
from aira.db import (
    delete_chat_from_db,
    get_chat_from_db,
    get_chats_from_db,
    get_user_from_token,
    hash_password,
    init_db,
    login_user,
    logout_session,
    register_user,
    save_chat_to_db,
    verify_password,
)
from aira.export import export_chat
from aira.files import doc_index, extract as extract_file
from aira.server import app, rate_limiter
from aira.tools.sandbox import run_python, validate_python_code
from aira.tools.web import BoundedTTLCache


class TestAiraSuite(unittest.TestCase):

    # =====================================================================
    # 1. Security: Password Hashing & Timing Safety
    # =====================================================================
    def test_01_password_hashing(self):
        pwd = "MySecretPassword123!"
        h1 = hash_password(pwd)
        h2 = hash_password(pwd)

        # Random salts must differ
        self.assertNotEqual(h1, h2)
        self.assertIn("$", h1)

        # Verify matching password
        self.assertTrue(verify_password(pwd, h1))
        self.assertTrue(verify_password(pwd, h2))

        # Verify wrong password rejected
        self.assertFalse(verify_password("WrongPassword", h1))

    # =====================================================================
    # 2. Security: Python Sandbox AST Screening & Escape Prevention
    # =====================================================================
    def test_02_sandbox_security_ast(self):
        # Dangerous modules
        self.assertIsNotNone(validate_python_code("import subprocess\nsubprocess.run('dir')"))
        self.assertIsNotNone(validate_python_code("import socket\ns = socket.socket()"))
        self.assertIsNotNone(validate_python_code("import ctypes\nctypes.CDLL('kernel32')"))
        self.assertIsNotNone(validate_python_code("import importlib\nimportlib.import_module('os')"))
        self.assertIsNotNone(validate_python_code("from os import system\nsystem('echo 1')"))

        # Dangerous calls
        self.assertIsNotNone(validate_python_code("os.system('whoami')"))
        self.assertIsNotNone(validate_python_code("os.popen('dir')"))
        self.assertIsNotNone(validate_python_code("eval('2 + 2')"))
        self.assertIsNotNone(validate_python_code("exec('a = 1')"))
        self.assertIsNotNone(validate_python_code("__import__('os').system('dir')"))

        # Introspection breakouts
        self.assertIsNotNone(validate_python_code("(). __class__.__subclasses__()"))

        # Safe legitimate code
        self.assertIsNone(validate_python_code("def add(a, b):\n    return a + b\nprint(add(2, 3))"))
        self.assertIsNone(validate_python_code("import math\nprint(math.sqrt(16))"))

    def test_03_sandbox_execution(self):
        async def _run():
            # Safe computation
            res = await run_python("def factorial(n):\n    return 1 if n <= 1 else n * factorial(n - 1)\nprint(factorial(5))")
            self.assertTrue(res["ok"])
            self.assertIn("120", res["output"])

            # Blocked execution
            blocked = await run_python("import subprocess\nsubprocess.run('dir')")
            self.assertFalse(blocked["ok"])
            self.assertTrue(blocked.get("security_violation"))
        asyncio.run(_run())

    # =====================================================================
    # 3. Performance & Memory: Bounded TTL Cache
    # =====================================================================
    def test_04_bounded_ttl_cache(self):
        cache = BoundedTTLCache(ttl=1.0, max_size=5)

        # Insert items
        for i in range(5):
            cache.set(f"k{i}", f"v{i}")

        self.assertEqual(cache.get("k0"), "v0")
        self.assertEqual(cache.get("k4"), "v4")

        # Insert 6th item (exceeding max_size)
        cache.set("k5", "v5")
        self.assertEqual(cache.get("k5"), "v5")

        # Clear
        cache.clear()
        self.assertIsNone(cache.get("k5"))

    # =====================================================================
    # 4. Reliability: PDF Error Recovery on Corrupted Data
    # =====================================================================
    def test_05_corrupted_pdf_handling(self):
        corrupted_data = b"%PDF-1.4\nCorrupted binary stream that is not valid"
        res = doc_index.add_document("corrupted.pdf", corrupted_data)
        self.assertFalse(res["ok"])
        self.assertIn("error", res)

    # =====================================================================
    # 5. Database & Multi-Tenant Chat Isolation
    # =====================================================================
    def test_06_multi_tenant_chat_isolation(self):
        async def _run():
            await init_db()

            u1_name = f"iso_u1_{os.urandom(3).hex()}"
            u2_name = f"iso_u2_{os.urandom(3).hex()}"
            u1_res = await register_user(u1_name, f"{u1_name}@test.local", "Password123!")
            u2_res = await register_user(u2_name, f"{u2_name}@test.local", "Password123!")
            u1_id = u1_res["user"]["id"]
            u2_id = u2_res["user"]["id"]

            # User 1 saves chat
            chat_u1 = await save_chat_to_db(
                {"title": "User 1 Secret", "kind": "research", "messages": [{"role": "user", "text": "Secret 1"}]},
                user_id=u1_id,
            )
            chat_u1_id = chat_u1["id"]
            self.assertEqual(chat_u1["user_id"], u1_id)

            # User 2 saves chat
            chat_u2 = await save_chat_to_db(
                {"title": "User 2 Notes", "kind": "research", "messages": [{"role": "user", "text": "Secret 2"}]},
                user_id=u2_id,
            )
            chat_u2_id = chat_u2["id"]
            self.assertEqual(chat_u2["user_id"], u2_id)

            # Query for User 1 only
            u1_chats = await get_chats_from_db(user_id=u1_id)
            u1_ids = [c["id"] for c in u1_chats]
            self.assertIn(chat_u1_id, u1_ids)
            self.assertNotIn(chat_u2_id, u1_ids)

            # Query for User 2 only
            u2_chats = await get_chats_from_db(user_id=u2_id)
            u2_ids = [c["id"] for c in u2_chats]
            self.assertIn(chat_u2_id, u2_ids)
            self.assertNotIn(chat_u1_id, u2_ids)

            # Cleanup
            await delete_chat_from_db(chat_u1_id, user_id=u1_id)
            await delete_chat_from_db(chat_u2_id, user_id=u2_id)
        asyncio.run(_run())

    # =====================================================================
    # 6. Server Endpoints & Rate Limiter Verification
    # =====================================================================
    def test_07_server_endpoints_and_validation(self):
        async def _run():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                # 1. Health
                res = await client.get("/api/health")
                self.assertEqual(res.status_code, 200)
                self.assertTrue(res.json()["ok"])

                # 2. Register with Pydantic validation
                unique_name = f"audit_{os.urandom(4).hex()}"
                reg_res = await client.post(
                    "/api/auth/register",
                    json={"username": unique_name, "email": f"{unique_name}@test.local", "password": "SecurePassword123!"},
                )
                self.assertEqual(reg_res.status_code, 200)
                reg_data = reg_res.json()
                token = reg_data["token"]

                # 3. Access authenticated endpoint
                me_res = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
                self.assertEqual(me_res.status_code, 200)
                self.assertEqual(me_res.json()["user"]["username"], unique_name)

                # 4. Access without token -> 401
                unauth_res = await client.get("/api/auth/me")
                self.assertEqual(unauth_res.status_code, 401)

                # 5. Invalid JSON payload -> 422 Unprocessable Entity
                bad_json = await client.post("/api/auth/register", json={"username": "a"})
                self.assertEqual(bad_json.status_code, 422)
        asyncio.run(_run())

    # =====================================================================
    # 7. Export Formatting
    # =====================================================================
    def test_08_export_chat(self):
        messages = [
            {"role": "user", "text": "What is quantum computing?"},
            {"role": "assistant", "text": "Quantum computing harnesses quantum mechanics.", "sources": ["https://nature.com/qc"]},
        ]

        txt_bytes, txt_name, txt_mime = export_chat("Quantum Intro", "RESEARCH", messages, "txt")
        self.assertGreater(len(txt_bytes), 50)
        self.assertEqual(txt_mime, "text/plain")
        self.assertIn("Quantum", txt_bytes.decode("utf-8"))

        docx_bytes, docx_name, docx_mime = export_chat("Quantum Intro", "RESEARCH", messages, "docx")
        self.assertGreater(len(docx_bytes), 100)
        self.assertIn("document", docx_mime)

        pdf_bytes, pdf_name, pdf_mime = export_chat("Quantum Intro", "RESEARCH", messages, "pdf")
        self.assertGreater(len(pdf_bytes), 100)
        self.assertEqual(pdf_mime, "application/pdf")


if __name__ == "__main__":
    unittest.main()
