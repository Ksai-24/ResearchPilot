"""Debug: inspect raw message fields from Groq gpt-oss-120b, with and without tools."""
import asyncio
import os
import sys

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aira import config
from openai import AsyncOpenAI


async def main():
    c = AsyncOpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)

    print("== plain completion ==")
    r = await c.chat.completions.create(
        model=config.MODEL,
        messages=[{"role": "user", "content": "Say hello in exactly 5 words."}],
    )
    m = r.choices[0].message
    print("  content:", repr((m.content or "")[:200]))
    print("  extra fields:", {k: str(v)[:80] for k, v in (m.model_extra or {}).items()})
    print("  reasoning_content attr:", getattr(m, "reasoning_content", None) is not None)

    print("\n== with tools (search then answer) ==")
    from aira.tools import TOOL_SCHEMAS
    msgs = [
        {"role": "system", "content": "Answer in one short sentence after searching."},
        {"role": "user", "content": "What is herd immunity?"},
    ]
    for round in range(4):
        r = await c.chat.completions.create(
            model=config.MODEL,
            messages=msgs,
            tools=[TOOL_SCHEMAS["web_search"]],
        )
        m = r.choices[0].message
        print(f"  round {round}: content={repr((m.content or '')[:60])} tool_calls={[tc.function.name for tc in (m.tool_calls or [])]}")
        print(f"           extra: { {k: str(v)[:60] for k, v in (m.model_extra or {}).items()} }")
        if not m.tool_calls:
            break
        msgs.append({"role": "assistant", "content": m.content or "",
                     "tool_calls": [tc.model_dump() for tc in m.tool_calls]})
        msgs.append({"role": "tool", "tool_call_id": m.tool_calls[0].id,
                     "content": '{"query":"herd immunity","results":[{"title":"Herd immunity - WHO","url":"https://who.int","snippet":"indirect protection"}]}'})


asyncio.run(main())
