import asyncio
from aira.tools.web import web_search, fetch_page

queries = [
    "python asyncio tutorial",
    "fastapi documentation",
    "crispr cas9 mechanism",
    "rust memory safety",
    "how to fix python recursionerror",
]

async def main():
    for q in queries:
        print(f"\n--- Testing Query: {q} ---")
        try:
            s = await web_search(q)
            results = s.get("results", [])
            print(f"Results count: {len(results)}")
            for i, r in enumerate(results[:3]):
                print(f"  [{i+1}] {r['title']} -> {r['url']}")
            if results:
                test_url = results[0]["url"]
                print(f"Fetching: {test_url}")
                p = await fetch_page(test_url, max_chars=300)
                print(f"Fetch success: {len(p.get('text', ''))} chars, via {p.get('via')}")
        except Exception as e:
            print(f"Error on {q}: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
