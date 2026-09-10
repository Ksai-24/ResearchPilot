import asyncio
import httpx
import re
from bs4 import BeautifulSoup
from aira.tools.web import _unwrap_search_url

async def test_ddg_vqd():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    }
    query = "python RecursionError fix"
    async with httpx.AsyncClient(headers=headers, timeout=5.0, follow_redirects=True) as client:
        # Step 1: Get vqd
        r1 = await client.get(f"https://duckduckgo.com/?q={query}")
        print("DDG initial status:", r1.status_code)
        m = re.search(r'vqd=([0-9-]+)', r1.text) or re.search(r'vqd="([^"]+)"', r1.text)
        if m:
            vqd = m.group(1)
            print("Found VQD:", vqd)
            # Step 2: Query links
            r2 = await client.get(f"https://links.duckduckgo.com/d.js?q={query}&vqd={vqd}")
            print("d.js status:", r2.status_code, len(r2.text))
            # d.js returns JavaScript callbacks or json
            print("Preview:", r2.text[:300])

if __name__ == "__main__":
    asyncio.run(test_ddg_vqd())
