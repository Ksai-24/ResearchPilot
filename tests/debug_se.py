import asyncio
import httpx

async def main():
    async with httpx.AsyncClient(headers={"User-Agent": "AiraBot/1.0"}) as client:
        r = await client.get(
            "https://api.stackexchange.com/2.3/questions/44382138",
            params={"site": "stackoverflow", "filter": "withbody"}
        )
        print("Status:", r.status_code)
        print("JSON:", r.json())

if __name__ == "__main__":
    asyncio.run(main())
