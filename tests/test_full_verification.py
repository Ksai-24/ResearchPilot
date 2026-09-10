"""End-to-end automated verification script for scraping speed and local DB chat persistence."""
import asyncio
import json
import time
from pathlib import Path
import sys
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aira.server import app
from aira.tools.web import web_search, fetch_page


async def get_test_client() -> httpx.AsyncClient:
    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=1.0) as check_client:
            r = await check_client.get("/api/health")
            if r.status_code == 200:
                return httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=10.0)
    except Exception:
        pass
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_health():
    print("[1/5] Testing server health...")
    client = await get_test_client()
    async with client:
        r = await client.get("/api/health", timeout=5.0)
        assert r.status_code == 200, f"Health check failed: {r.status_code}"
        data = r.json()
        print(f"      Health OK: ok={data.get('ok')} | Key loaded: {data.get('key_loaded')} | DB: {data.get('database')}")


async def test_chat_persistence():
    print("[2/5] Testing local system database chat persistence...")
    client = await get_test_client()
    async with client:
        chat_id = f"test_chat_{int(time.time())}"
        test_chat_payload = {
            "id": chat_id,
            "title": "Quantum Computing Verification Chat",
            "kind": "research",
            "created": 1773180000000,
            "updated": 1773180050000,
            "messages": [
                {
                    "role": "user",
                    "text": "What is quantum supremacy?",
                    "files": ["whitepaper.pdf"]
                },
                {
                    "role": "assistant",
                    "text": "Quantum supremacy is the milestone where a programmable quantum device solves a problem that no classical supercomputer can solve in any feasible amount of time [1].\n\n### Sources:\n1. Nature: Quantum Supremacy Using a Programmable Superconducting Processor (https://www.nature.com/articles/s41586-019-1666-5)",
                    "sources": [
                        "Nature: Quantum Supremacy Using a Programmable Superconducting Processor (https://www.nature.com/articles/s41586-019-1666-5)"
                    ],
                    "tools": ["web_search", "fetch_page"],
                    "elapsed": "1.8s"
                }
            ]
        }

        # 1. Save Chat
        r_save = await client.post("/api/chats", json=test_chat_payload)
        assert r_save.status_code == 200, f"Save chat failed: {r_save.text}"
        assert r_save.json()["ok"] is True

        # 2. Get Chat by ID
        r_get = await client.get(f"/api/chats/{chat_id}")
        assert r_get.status_code == 200, f"Get chat failed: {r_get.text}"
        chat_data = r_get.json()["chat"]
        assert chat_data["id"] == chat_id
        assert len(chat_data["messages"]) == 2
        assert chat_data["messages"][1]["sources"][0].startswith("Nature")

        # 3. List Chats
        r_list = await client.get("/api/chats?kind=research")
        assert r_list.status_code == 200
        chats_list = r_list.json()["chats"]
        assert any(c["id"] == chat_id for c in chats_list)

        # 4. Clean up test chat
        r_del = await client.delete(f"/api/chats/{chat_id}")
        assert r_del.status_code == 200
        print("      Database chat persistence verified OK.")


async def test_scraping_performance():
    print("[3/5] Testing high-speed search and scraping performance...")
    t0 = time.time()
    search_res = await web_search("quantum computing superconductor", max_results=5)
    search_time = time.time() - t0
    print(f"      web_search finished in {search_time:.2f}s, found {len(search_res.get('results', []))} results.")
    assert len(search_res.get("results", [])) > 0, "Web search returned 0 results"

    test_url = search_res["results"][0]["url"]
    t1 = time.time()
    fetch_res = await fetch_page(test_url, max_chars=2000)
    fetch_time = time.time() - t1
    text_len = len(fetch_res.get("text", ""))
    print(f"      fetch_page finished in {fetch_time:.2f}s, extracted {text_len} chars (via {fetch_res.get('via')}).")
    assert text_len > 60, f"Extracted text too short: {text_len}"


async def test_scraping_in_all_modes():
    print("[4/5] Verifying scraping configuration in all 3 sections...")
    from aira.tools import RESEARCH_TOOLS, BUGFIX_TOOLS
    from aira.prompt import system_prompt

    # Mode 1: Research
    assert "web_search" in RESEARCH_TOOLS and "fetch_page" in RESEARCH_TOOLS
    p1 = system_prompt("RESEARCH", "short")
    assert "web_search and fetch_page" in p1

    # Mode 2: Deep Search
    assert "web_search" in RESEARCH_TOOLS and "fetch_page" in RESEARCH_TOOLS
    p2 = system_prompt("RESEARCH", "full theory")
    assert "web_search and fetch_page" in p2

    # Mode 3: Code & Fix
    assert "web_search" in BUGFIX_TOOLS and "fetch_page" in BUGFIX_TOOLS
    p3 = system_prompt("BUG_FIX", "short")
    assert "web_search and fetch_page" in p3
    print("      Scraping verified active across Research, Deep Search, and Code & Fix modes.")


async def test_frontend_db_sync():
    print("[5/5] Testing frontend UI database sync hooks...")
    client = await get_test_client()
    async with client:
        r = await client.get("/", timeout=5.0)
        assert r.status_code == 200
        html = r.text
        assert "loadChatsFromDatabase()" in html
        assert "/api/chats" in html
        assert "setKind('research')" in html
        print("      Frontend verified: database sync hook loaded and active.")


async def main():
    await test_health()
    await test_chat_persistence()
    await test_scraping_performance()
    await test_scraping_in_all_modes()
    await test_frontend_db_sync()
    print("\nALL 5 VERIFICATION CHECKS PASSED PERFECTLY!")


if __name__ == "__main__":
    asyncio.run(main())
