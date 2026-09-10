import asyncio
import httpx

async def test_headers():
    urls = [
        "https://rupress.org/jem/article/220/9/e20230668/214193/",
        "https://www.nature.com/articles/s41586-022-04778-y",
    ]
    bot_headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    async with httpx.AsyncClient(headers=bot_headers, timeout=5.0, follow_redirects=True) as client:
        for u in urls:
            try:
                r = await client.get(u)
                print(f"Bot UA on {u[:45]}: status={r.status_code}, len={len(r.text)}")
            except Exception as e:
                print(f"Bot UA error on {u[:45]}:", e)

if __name__ == "__main__":
    asyncio.run(test_headers())
