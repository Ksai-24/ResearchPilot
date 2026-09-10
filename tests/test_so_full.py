import asyncio
import re
import httpx
from bs4 import BeautifulSoup

async def fetch_stackoverflow(client: httpx.AsyncClient, url: str, max_chars: int = 4000):
    m = re.search(r'/questions/(\d+)', url)
    if not m:
        return None
    qid = m.group(1)
    site = "stackoverflow" if "stackoverflow" in url else "stackexchange"
    try:
        # 1. Fetch Question
        r_q = await client.get(
            f"https://api.stackexchange.com/2.3/questions/{qid}",
            params={"site": site, "filter": "withbody"},
            headers={"User-Agent": "AiraBot/1.0"},
            timeout=4.0
        )
        if r_q.status_code != 200:
            return None
        items = r_q.json().get("items", [])
        if not items:
            return None
        q = items[0]
        title = q.get("title", "")
        q_soup = BeautifulSoup(q.get("body", ""), "html.parser")
        q_text = q_soup.get_text("\n", strip=True)

        # 2. Fetch Top Answers
        r_a = await client.get(
            f"https://api.stackexchange.com/2.3/questions/{qid}/answers",
            params={"site": site, "filter": "withbody", "order": "desc", "sort": "votes", "pagesize": 3},
            headers={"User-Agent": "AiraBot/1.0"},
            timeout=4.0
        )
        ans_parts = []
        if r_a.status_code == 200:
            for ans in r_a.json().get("items", []):
                a_soup = BeautifulSoup(ans.get("body", ""), "html.parser")
                score = ans.get("score", 0)
                acc = ans.get("is_accepted", False)
                ans_parts.append(f"--- Answer (Score: {score}{', Accepted' if acc else ''}) ---\n" + a_soup.get_text("\n", strip=True))

        full_content = f"Stack Overflow: {title}\n\n[Question]\n{q_text}\n\n" + "\n\n".join(ans_parts)
        return {
            "url": url,
            "title": f"Stack Overflow: {title}",
            "text": full_content[:max_chars],
            "via": "stackoverflow api",
            "truncated": len(full_content) > max_chars,
        }
    except Exception as e:
        print("SO fetch error:", e)
        return None

async def main():
    async with httpx.AsyncClient() as client:
        res = await fetch_stackoverflow(client, "https://stackoverflow.com/questions/44382138/python-recursionerror-maximum-recursion-depth-exceeded-while-calling-a-python-o")
        print("Success:", res is not None)
        if res:
            print("Title:", res["title"])
            print("Length:", len(res["text"]))
            print("Preview:\n", res["text"][:300])

if __name__ == "__main__":
    asyncio.run(main())
