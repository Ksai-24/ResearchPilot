import asyncio
import httpx

async def test_apis():
    async with httpx.AsyncClient(headers={"User-Agent": "AiraBot/1.0"}, timeout=5.0) as client:
        # 1. StackExchange API
        try:
            r = await client.get(
                "https://api.stackexchange.com/2.3/search/advanced",
                params={"order": "desc", "sort": "relevance", "q": "python RecursionError fix", "site": "stackoverflow"}
            )
            print("StackExchange API status:", r.status_code)
            if r.status_code == 200:
                items = r.json().get("items", [])
                print(f"StackOverflow results: {len(items)}")
                for it in items[:3]:
                    print(f"  SO: {it.get('title')} -> {it.get('link')}")
        except Exception as e:
            print("StackExchange error:", e)

        # 2. Wikipedia API
        try:
            r = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "opensearch", "search": "Recursion", "limit": 3, "format": "json"}
            )
            print("Wikipedia API status:", r.status_code)
            if r.status_code == 200:
                print("Wikipedia results:", r.json()[1])
        except Exception as e:
            print("Wikipedia error:", e)

if __name__ == "__main__":
    asyncio.run(test_apis())
