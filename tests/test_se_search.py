import asyncio
import httpx

async def search_stackexchange(client: httpx.AsyncClient, query: str, max_results: int = 4) -> list[dict]:
    results = []
    try:
        r = await client.get(
            "https://api.stackexchange.com/2.3/search/advanced",
            params={
                "order": "desc",
                "sort": "relevance",
                "q": query,
                "site": "stackoverflow",
                "pagesize": max_results,
                "filter": "default",
            },
            headers={"User-Agent": "AiraBot/1.0"},
            timeout=3.0,
        )
        if r.status_code == 200:
            items = r.json().get("items", [])
            for it in items:
                link = it.get("link", "")
                title = it.get("title", "")
                if link and title:
                    results.append({
                        "title": f"Stack Overflow: {title}",
                        "url": link,
                        "snippet": f"Tags: {', '.join(it.get('tags', []))} | Score: {it.get('score', 0)} | Answered: {it.get('is_answered', False)}",
                    })
    except Exception as e:
        print("SE search error:", e)
    return results

async def main():
    async with httpx.AsyncClient() as client:
        res = await search_stackexchange(client, "python recursionerror fix")
        print(f"StackExchange returned {len(res)} results:")
        for r in res:
            print(" ", r["title"], "->", r["url"])

if __name__ == "__main__":
    asyncio.run(main())
