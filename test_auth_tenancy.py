import httpx
import json
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_URL = "http://127.0.0.1:8000"

def test_full_auth_and_chat_tenancy():
    print("--- Starting Multi-User Tenancy and Verification Test ---")
    client = httpx.Client(base_url=BASE_URL, timeout=10.0)

    # 1. Check health
    h = client.get("/api/health").json()
    assert h["ok"] is True
    assert h["database"]["backend"] == "mysql"
    print("✓ Health Check Passed: Connected to MySQL chat_app database")

    # 2. Register / Login User A
    user_a_email = "user_alpha@test.local"
    user_a_name = "user_alpha"
    pwd = "SecurePassword123!"

    reg_a = client.post("/api/auth/register", json={"username": user_a_name, "email": user_a_email, "password": pwd})
    if reg_a.status_code == 200:
        token_a = reg_a.json()["token"]
        print("✓ Registered User Alpha")
    else:
        login_a = client.post("/api/auth/login", json={"username_or_email": user_a_name, "password": pwd})
        assert login_a.status_code == 200, f"Login failed: {login_a.text}"
        token_a = login_a.json()["token"]
        print("✓ Logged in existing User Alpha")

    # 3. Register / Login User B
    user_b_email = "user_beta@test.local"
    user_b_name = "user_beta"
    reg_b = client.post("/api/auth/register", json={"username": user_b_name, "email": user_b_email, "password": pwd})
    if reg_b.status_code == 200:
        token_b = reg_b.json()["token"]
        print("✓ Registered User Beta")
    else:
        login_b = client.post("/api/auth/login", json={"username_or_email": user_b_name, "password": pwd})
        assert login_b.status_code == 200, f"Login failed: {login_b.text}"
        token_b = login_b.json()["token"]
        print("✓ Logged in existing User Beta")

    # 4. User A saves a research chat
    chat_a_payload = {
        "title": "Alpha Research on Superconductors",
        "kind": "research",
        "messages": [
            {"role": "user", "text": "What are high-temperature superconductors?"},
            {"role": "assistant", "text": "High-temperature superconductors operate above 77K.", "sources": ["https://nature.com/articles/s12345"]}
        ]
    }
    res_save_a = client.post("/api/chats", headers={"Authorization": f"Bearer {token_a}"}, json=chat_a_payload)
    assert res_save_a.status_code == 200
    saved_a = res_save_a.json()["chat"]
    chat_a_id = saved_a["id"]
    print(f"✓ User Alpha saved private chat: {chat_a_id}")

    # 5. User B saves a code fix chat
    chat_b_payload = {
        "title": "Beta Rust Memory Safety Debugging",
        "kind": "bugfix",
        "messages": [
            {"role": "user", "text": "Fix borrow checker issue in LinkedList"},
            {"role": "assistant", "text": "Use Box<Option<Node>> with Rc and RefCell.", "sources": []}
        ]
    }
    res_save_b = client.post("/api/chats", headers={"Authorization": f"Bearer {token_b}"}, json=chat_b_payload)
    assert res_save_b.status_code == 200
    saved_b = res_save_b.json()["chat"]
    chat_b_id = saved_b["id"]
    print(f"✓ User Beta saved private chat: {chat_b_id}")

    # 6. Verify User Alpha ONLY sees Alpha's chats
    list_a = client.get("/api/chats", headers={"Authorization": f"Bearer {token_a}"}).json()["chats"]
    a_ids = [c["id"] for c in list_a]
    assert chat_a_id in a_ids
    assert chat_b_id not in a_ids
    print(f"✓ User Alpha chats verified ({len(list_a)} chats loaded, Beta chats strictly isolated)")

    # 7. Verify User Beta ONLY sees Beta's chats
    list_b = client.get("/api/chats", headers={"Authorization": f"Bearer {token_b}"}).json()["chats"]
    b_ids = [c["id"] for c in list_b]
    assert chat_b_id in b_ids
    assert chat_a_id not in b_ids
    print(f"✓ User Beta chats verified ({len(list_b)} chats loaded, Alpha chats strictly isolated)")

    # 8. Test updating an existing chat for User Alpha (no duplicate rows created)
    chat_a_payload["id"] = chat_a_id
    chat_a_payload["messages"].append({"role": "user", "text": "Tell me more."})
    chat_a_payload["messages"].append({"role": "assistant", "text": "Here is additional data on cuprates."})
    res_update_a = client.post("/api/chats", headers={"Authorization": f"Bearer {token_a}"}, json=chat_a_payload)
    assert res_update_a.status_code == 200
    updated_a = res_update_a.json()["chat"]
    assert updated_a["id"] == chat_a_id
    print(f"✓ Existing chat {chat_a_id} updated successfully in-place in MySQL")

    # 9. Verify unauthenticated users get empty chat list (no data leak)
    unauth_list = client.get("/api/chats").json()["chats"]
    assert len(unauth_list) == 0
    print("✓ Unauthenticated guest gets 0 chats (Privacy protection verified)")

    # 10. Test User Alpha logout
    logout_res = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token_a}"})
    assert logout_res.status_code == 200
    me_after_logout = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_a}"})
    assert me_after_logout.status_code == 401
    print("✓ Session invalidated on logout (Protected endpoint returns 401)")

    print("\n=======================================================")
    print(" ALL 10 AUTHENTICATION & MULTI-TENANCY TESTS PASSED! ")
    print("=======================================================\n")

if __name__ == "__main__":
    test_full_auth_and_chat_tenancy()
