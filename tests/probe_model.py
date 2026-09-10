"""Probe: hammer the free model 6x and inspect raw responses for failure modes."""
import asyncio
import sys

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aira import config
from aira.tools import TOOL_SCHEMAS
from openai import AsyncOpenAI

MODEL = config.MODEL


async def one(c, i):
    try:
        r = await c.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": f"Reply with the word OK only. ({i})"}],
            tools=[TOOL_SCHEMAS["web_search"]],
        )
        ch = r.choices
        if not ch:
            print(f"  #{i}: choices={ch!r}  raw={r.model_dump()!r:.300}")
            return "empty"
        m = ch[0].message
        print(f"  #{i}: ok content={repr((m.content or '')[:40])}")
        return "ok"
    except Exception as e:
        print(f"  #{i}: EXC {type(e).__name__}: {str(e)[:150]}")
        return "exc"


async def main():
    c = AsyncOpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)
    results = await asyncio.gather(*[one(c, i) for i in range(6)])
    print("summary:", {r: results.count(r) for r in set(results)})


asyncio.run(main())
