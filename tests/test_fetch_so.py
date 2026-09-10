import asyncio
from aira.tools.web import fetch_page

async def main():
    url = "https://stackoverflow.com/questions/44382138/python-recursionerror-maximum-recursion-depth-exceeded-while-calling-a-python-o"
    p = await fetch_page(url, max_chars=1000)
    print("Fetch OK:", len(p.get("text", "")), "chars")
    print(p.get("text", "")[:400])

if __name__ == "__main__":
    asyncio.run(main())
