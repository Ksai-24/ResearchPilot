import asyncio
import httpx

async def test_jina():
    # Test a URL that normally 403 blocks scrapers: StackOverflow or Cloudflare protected
    url = "https://stackoverflow.com/questions/44382138/python-recursionerror-maximum-recursion-depth-exceeded-while-calling-a-python-o"
    jina_url = f"https://r.jina.ai/{url}"
    print("Testing Jina Reader on:", jina_url)
    async with httpx.AsyncClient(timeout=8.0) as client:
        try:
            r = await client.get(jina_url, headers={"User-Agent": "AiraBot/1.0"})
            print("Jina Reader status:", r.status_code, "length:", len(r.text))
            print("First 300 chars of extracted markdown:")
            print(r.text[:300])
        except Exception as e:
            print("Jina error:", e)

if __name__ == "__main__":
    asyncio.run(test_jina())
