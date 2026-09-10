import asyncio
import httpx

async def main():
    async with httpx.AsyncClient(headers={"User-Agent": "AiraBot/1.0"}) as client:
        r = await client.get(
            "https://api.stackexchange.com/2.3/questions/44382138/answers",
            params={"site": "stackoverflow", "filter": "withbody", "order": "desc", "sort": "votes"}
        )
        print("Answers status:", r.status_code)
        items = r.json().get("items", [])
        print("Answers count:", len(items))
        for a in items:
            print("Score:", a.get("score"), "Accepted:", a.get("is_accepted"))
            print("Answer body snippet:", a.get("body", "")[:150])

if __name__ == "__main__":
    asyncio.run(main())
