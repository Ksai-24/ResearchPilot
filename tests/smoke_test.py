"""Smoke test: verify tools + server wiring without needing an LLM key."""
import asyncio
import sys

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aira.tools import web_search, fetch_page, run_python, TOOL_SCHEMAS, dispatch


async def main():
    print("== 1. web_search ==")
    r = await web_search("herd immunity definition", max_results=3)
    for x in r["results"]:
        print("  -", x["title"][:60], "|", x["url"][:70])
    assert r["results"], "search returned nothing"

    print("\n== 2. fetch_page ==")
    url = r["results"][0]["url"]
    p = await fetch_page(url, max_chars=300)
    print("  title:", p["title"][:70])
    print("  text head:", p["text"][:120].replace("\n", " "))
    assert len(p["text"]) > 50, "page text too thin"

    print("\n== 3. run_python (bug reproduction: average([]) ==")
    out = await run_python(
        "def average(nums):\n"
        "    total = 0\n"
        "    for n in nums:\n"
        "        total += n\n"
        "    return total / len(nums)\n\n"
        "print(average([]))"
    )
    print("  ok:", out["ok"], "| rc:", out["returncode"])
    print("  output:", out["output"].strip().splitlines()[-1])
    assert not out["ok"] and "ZeroDivisionError" in out["output"], "expected ZeroDivisionError"

    print("\n== 4. dispatcher ==")
    d = await dispatch("web_search", {"query": "test", "extra_ignored": 1})
    assert d["query"] == "test"
    print("  dispatch OK; schemas:", list(TOOL_SCHEMAS))

    print("\n== 5. system prompt assembly ==")
    from aira.prompt import system_prompt
    sp = system_prompt("RESEARCH", "full theory")
    assert "RESEARCH mode" in sp and "full theory" in sp
    sp2 = system_prompt("BUG_FIX", "short")
    assert "BUG_FIX mode" in sp2 and "3-5 sentences" in sp2
    print("  RESEARCH full-theory:", len(sp), "chars | BUG_FIX short:", len(sp2), "chars")

    print("\nALL SMOKE TESTS PASSED")


asyncio.run(main())
