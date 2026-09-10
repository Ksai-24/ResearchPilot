import asyncio
import httpx
from bs4 import BeautifulSoup

async def test_bing_variations():
    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}) as client:
        queries = [
            "how to fix python recursionerror",
            "python recursionerror fix",
            "python RecursionError maximum recursion depth exceeded",
        ]
        for q in queries:
            print(f"\n--- Bing: {q} ---")
            r = await client.get("https://www.bing.com/search", params={"q": q, "count": 10})
            soup = BeautifulSoup(r.text, "html.parser")
            items = soup.select("li.b_algo h2 a")
            print(f"Found {len(items)} items:")
            for a in items[:4]:
                print(f"  {a.get_text(strip=True)} -> {a.get('href')}")

if __name__ == "__main__":
    asyncio.run(test_bing_variations())
