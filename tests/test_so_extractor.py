import asyncio
import httpx
import re
from bs4 import BeautifulSoup

async def fetch_stackoverflow(url: str):
    m = re.search(r'/questions/(\d+)', url)
    if not m:
        return None
    qid = m.group(1)
    async with httpx.AsyncClient(timeout=6.0) as client:
        # Fetch question and answers in one go
        r = await client.get(
            f"https://api.stackexchange.com/2.3/questions/{qid}",
            params={
                "site": "stackoverflow",
                "filter": "!6Wfm_gWyJ*sP4",  # Includes title, body, answers, answer body
            }
        )
        if r.status_code != 200:
            return None
        items = r.json().get("items", [])
        if not items:
            return None
        q = items[0]
        title = q.get("title", "")
        q_soup = BeautifulSoup(q.get("body", ""), "html.parser")
        q_text = q_soup.get_text("\n", strip=True)
        
        answers_text = []
        for ans in q.get("answers", []):
            a_soup = BeautifulSoup(ans.get("body", ""), "html.parser")
            score = ans.get("score", 0)
            is_acc = ans.get("is_accepted", False)
            answers_text.append(f"--- Answer (Score: {score}{', Accepted' if is_acc else ''}) ---\n" + a_soup.get_text("\n", strip=True))
            
        full = f"Question: {title}\n\n{q_text}\n\n" + "\n\n".join(answers_text)
        return {
            "url": url,
            "title": f"Stack Overflow: {title}",
            "text": full[:4000],
            "via": "stackoverflow api",
            "truncated": len(full) > 4000
        }

async def main():
    res = await fetch_stackoverflow("https://stackoverflow.com/questions/44382138/python-recursionerror-maximum-recursion-depth-exceeded-while-calling-a-python-o")
    if res:
        print("Success!")
        print("Title:", res["title"])
        print("Text preview:\n", res["text"][:350])
    else:
        print("Failed!")

if __name__ == "__main__":
    asyncio.run(main())
