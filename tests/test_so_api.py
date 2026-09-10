import asyncio
import httpx
from bs4 import BeautifulSoup

async def test_so_api():
    async with httpx.AsyncClient(headers={"User-Agent": "AiraBot/1.0"}, timeout=5.0) as client:
        r = await client.get(
            "https://api.stackexchange.com/2.3/questions/44382138",
            params={
                "order": "desc",
                "sort": "activity",
                "site": "stackoverflow",
                "filter": "!9_bDDxJY5"  # includes title, body_markdown, answers
            }
        )
        print("SO Question API status:", r.status_code)
        if r.status_code == 200:
            data = r.json()
            items = data.get("items", [])
            if items:
                q = items[0]
                print("Title:", q.get("title"))
                soup = BeautifulSoup(q.get("body", ""), "html.parser")
                print("Body text length:", len(soup.get_text()))
                print("Answers count:", len(q.get("answers", [])))

if __name__ == "__main__":
    asyncio.run(test_so_api())
