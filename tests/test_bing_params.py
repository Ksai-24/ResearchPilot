import asyncio
import httpx
from bs4 import BeautifulSoup
from aira.tools.web import _unwrap_search_url

async def test_bing_params():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cookie": "SRCHHPGUSR=ADLT=OFF&NRSLT=10&SRCHLANG=en;",
    }
    params = {
        "q": "python recursionerror fix",
        "mkt": "en-US",
        "setlang": "en",
        "cc": "US",
    }
    async with httpx.AsyncClient(headers=headers, timeout=5.0) as client:
        r = await client.get("https://www.bing.com/search", params=params)
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for li in soup.select("li.b_algo"):
            a = li.select_one("h2 a")
            if a and a.get("href"):
                url = _unwrap_search_url(a["href"])
                p = li.select_one(".b_caption p, p")
                results.append((a.get_text(strip=True), url, p.get_text(strip=True) if p else ""))
        print(f"Results with mkt=en-US: {len(results)}")
        for t, u, snip in results[:5]:
            print(f"  {t}\n    URL: {u}\n    SNIP: {snip[:80]}...")

if __name__ == "__main__":
    asyncio.run(test_bing_params())
