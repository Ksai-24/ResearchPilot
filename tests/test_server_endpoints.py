"""Integration test for FastAPI server endpoints: auth, traffic, and batch uploads."""
import asyncio
import sys
from pathlib import Path
from httpx import ASGITransport, AsyncClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aira.server import app


async def run_tests():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        print("== 1. Test /api/health ==")
        res = await client.get("/api/health")
        assert res.status_code == 200, f"Health check failed: {res.text}"
        data = res.json()
        assert data["ok"] is True
        assert "traffic" in data
        assert "database" in data
        print(f"  Health OK: traffic_status={data['traffic']['traffic_status']}, db={data['database']['status']}")

        print("\n== 2. Test /api/system/traffic ==")
        res = await client.get("/api/system/traffic")
        assert res.status_code == 200
        traffic = res.json()
        assert "active_requests" in traffic
        assert "current_provider" in traffic
        print(f"  Traffic stats OK: active={traffic['active_requests']}, provider={traffic['current_provider']}")

        print("\n== 3. Test /api/auth/register & /api/auth/login ==")
        import os
        uid = os.urandom(4).hex()
        username = f"user_{uid}"
        email = f"user_{uid}@aira.local"
        password = "SecurePassword123!"

        # Register
        reg_res = await client.post(
            "/api/auth/register",
            json={"username": username, "email": email, "password": password},
        )
        assert reg_res.status_code == 200, f"Registration failed: {reg_res.text}"
        reg_data = reg_res.json()
        token = reg_data["token"]
        print(f"  Registered user: {reg_data['user']['username']}, token length: {len(token)}")

        # Login
        login_res = await client.post(
            "/api/auth/login",
            json={"username_or_email": email, "password": password},
        )
        assert login_res.status_code == 200
        login_data = login_res.json()
        auth_token = login_data["token"]
        print(f"  Login OK, issued token: {auth_token[:16]}...")

        # Auth Me
        me_res = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert me_res.status_code == 200
        me_data = me_res.json()
        assert me_data["user"]["username"] == username
        print(f"  /api/auth/me resolved to: {me_data['user']['username']}")

        print("\n== 4. Test /api/upload-batch ==")
        files = [
            ("files", (f"doc_{i}.txt", f"Sample research note #{i} with key data".encode(), "text/plain"))
            for i in range(1, 10)
        ]
        upload_res = await client.post("/api/upload-batch", files=files)
        assert upload_res.status_code == 200
        batch_data = upload_res.json()
        assert batch_data["successful"] == 9
        print(f"  Batch upload OK: uploaded {batch_data['uploaded_count']}, total indexed docs: {batch_data['total_indexed_documents']}")

        # Verify catalog endpoint
        catalog_res = await client.get("/api/documents/catalog")
        assert catalog_res.status_code == 200
        catalog_data = catalog_res.json()
        assert catalog_data["total_documents"] >= 9
        print(f"  Document catalog OK: {catalog_data['total_documents']} documents indexed")

        # Logout
        logout_res = await client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert logout_res.status_code == 200
        print("  Logout OK")

        # Verify me fails after logout
        me_after_logout = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert me_after_logout.status_code == 401
        print("  Post-logout unauthorized verification OK")

    print("\nALL SERVER ENDPOINT INTEGRATION TESTS PASSED!")


if __name__ == "__main__":
    asyncio.run(run_tests())
