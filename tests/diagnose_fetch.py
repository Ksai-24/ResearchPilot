"""Diagnose fetch_page failures: which sites block us and why."""
import asyncio
import sys

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aira.tools.web import fetch_page

# Mix of: previously-failing journal sites, common reference sites, news
URLS = [
    "https://en.wikipedia.org/wiki/Herd_immunity",
    "https://www.nature.com/articles/s41586-022-04778-y",
    "https://www.jci.org/articles/view/167955",
    "https://www.science.org/doi/10.1126/science.abm0829",
    "https://rupress.org/jem/article/220/9/e20230668/214193/",
    "https://www.bbc.com/news/science-environment-67053962",
    "https://www.ibm.com/topics/generative-ai",
    "https://pubmed.ncbi.nlm.nih.gov/?term=mRNA+booster+memory+B+cells",
]


async def main():
    for url in URLS:
        try:
            r = await fetch_page(url, max_chars=200)
            n = r.get("text", "")
            print(f"  OK   {len(n):5d} ch | {url[:70]}")
        except Exception as e:
            msg = str(e).replace("\n", " ")[:80]
            print(f"  FAIL        | {url[:70]} -> {msg}")


asyncio.run(main())
