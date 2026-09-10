import asyncio
import httpx
from bs4 import BeautifulSoup

async def main():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with httpx.AsyncClient(headers=headers, timeout=6.0, follow_redirects=True) as client:
        # Test 1: lite.duckduckgo.com POST
        try:
            r = await client.post("https://lite.duckduckgo.com/lite/", data={"q": "python recursionerror fix"})
            print(f"Lite DDG status: {r.status_code}, length: {len(r.text)}")
            soup = BeautifulSoup(r.text, "html.parser")
            links = soup.select("a.result-link")
            print(f"Lite DDG links: {len(links)}")
            for l in links[:3]:
                print(f"  {l.get_text(strip=True)} -> {l.get('href')}")
        except Exception as e:
            print("Lite DDG error:", e)

        # Test 2: Brave search
        try:
            r = await client.get("https://search.brave.com/search", params={"q": "python recursionerror fix"})
            print(f"Brave status: {r.status_code}")
            soup = BeautifulSoup(r.text, "html.parser")
            snippets = soup.select(".snippet[data-type='web']")
            print(f"Brave snippets: {len(snippets)}")
            for s in snippets[:3]:
                a = s.select_one("a")
                print(f"  Brave: {a.get_text(strip=True) if a else ''} -> {a.get('href') if a else ''}")
        except Exception as e:
            print("Brave error:", e)

if __name__ == "__main__":
    asyncio.run(main())
