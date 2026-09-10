import asyncio
import httpx
from bs4 import BeautifulSoup
from aira.tools.web import _unwrap_search_url

async def test_bing_queries():
    client = httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    queries = [
        'python "RecursionError"',
        'python RecursionError site:stackoverflow.com',
        'python maximum recursion depth exceeded',
    ]
    for q in queries:
        r = await client.get("https://www.bing.com/search", params={"q": q})
        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.select("li.b_algo")
        print(f"\nQuery: {q} -> {len(items)} results")
        for li in items[:3]:
            a = li.select_one("h2 a")
            if a:
                print(f"  {a.get_text(strip=True)} -> {_unwrap_search_url(a['href'])}")
    await client.aclose()

if __name__ == "__main__":
    asyncio.run(test_bing_queries())
