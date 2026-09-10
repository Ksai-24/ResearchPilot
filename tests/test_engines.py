import asyncio
import httpx
from bs4 import BeautifulSoup
import urllib.parse

async def test_engines():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
    query = "python recursionerror fix"
    async with httpx.AsyncClient(headers=headers, timeout=5.0) as client:
        # 1. Yahoo search
        try:
            r = await client.get("https://search.yahoo.com/search", params={"p": query})
            soup = BeautifulSoup(r.text, "html.parser")
            y_items = []
            for a in soup.select("h3 a"):
                href = a.get("href", "")
                if "yahoo.com" not in href and href.startswith("http"):
                    y_items.append((a.get_text(strip=True), href))
            print(f"Yahoo results: {len(y_items)}")
            for t, u in y_items[:3]:
                print(f"  Yahoo: {t} -> {u}")
        except Exception as e:
            print("Yahoo error:", e)

        # 2. DuckDuckGo JSON API
        try:
            r = await client.get("https://api.duckduckgo.com/", params={"q": query, "format": "json"})
            data = r.json()
            topics = data.get("RelatedTopics", [])
            print(f"DDG API topics: {len(topics)}")
        except Exception as e:
            print("DDG API error:", e)

        # 3. Google html
        try:
            r = await client.get("https://www.google.com/search", params={"q": query})
            soup = BeautifulSoup(r.text, "html.parser")
            g_items = []
            for a in soup.select("a"):
                href = a.get("href", "")
                if "/url?q=" in href:
                    clean = href.split("/url?q=")[1].split("&")[0]
                    clean = urllib.parse.unquote(clean)
                    if clean.startswith("http") and "google.com" not in clean:
                        g_items.append((a.get_text(strip=True), clean))
            print(f"Google HTML results: {len(g_items)}")
            for t, u in g_items[:3]:
                print(f"  Google: {t} -> {u}")
        except Exception as e:
            print("Google error:", e)

if __name__ == "__main__":
    asyncio.run(test_engines())
