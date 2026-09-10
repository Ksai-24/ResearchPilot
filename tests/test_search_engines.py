import asyncio
import httpx
from bs4 import BeautifulSoup
from aira.tools.web import get_web_client, _unwrap_search_url

async def test_ddg_html():
    client = get_web_client()
    query = "how to fix python recursionerror"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    r = await client.post("https://html.duckduckgo.com/html/", data={"q": query}, headers=headers, timeout=5.0)
    print(f"DDG HTML status: {r.status_code}")
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.select(".result__title a.result__url, a.result__url, a.result__snippet"):
        pass
    results = []
    for el in soup.select(".result__body"):
        a = el.select_one("a.result__snippet, .result__title a")
        title_el = el.select_one(".result__title")
        snip_el = el.select_one(".result__snippet")
        if a and a.get("href"):
            results.append({
                "title": title_el.get_text(strip=True) if title_el else "",
                "url": _unwrap_search_url(a.get("href")),
                "snippet": snip_el.get_text(strip=True) if snip_el else ""
            })
    print(f"DDG HTML results: {len(results)}")
    for res in results[:5]:
        print(f"  {res['title']} -> {res['url']}")

if __name__ == "__main__":
    asyncio.run(test_ddg_html())
