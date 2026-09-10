import asyncio
import httpx
from aira.tools.web import web_search, fetch_page

async def main():
    print("Testing robust scraping on difficult URLs...")
    # Test 1: StackOverflow URL
    so_url = "https://stackoverflow.com/questions/44382138/python-recursionerror-maximum-recursion-depth-exceeded-while-calling-a-python-o"
    print("\n1. Fetching SO URL:", so_url)
    res1 = await fetch_page(so_url)
    print("   Result keys:", list(res1.keys()))
    print("   Title:", res1.get("title"))
    print("   Text length:", len(res1.get("text", "")))
    print("   Via:", res1.get("via"))

    # Test 2: Search for code bug
    print("\n2. Searching for: python recursionerror fix")
    s_res = await web_search("python recursionerror fix")
    results = s_res.get("results", [])
    print(f"   Results found: {len(results)}")
    for r in results[:3]:
        print("   -", r["title"], "->", r["url"])

if __name__ == "__main__":
    asyncio.run(main())
