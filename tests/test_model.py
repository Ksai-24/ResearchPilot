"""Quick check: does this OpenRouter model do tool calls + final answer properly?"""
import asyncio
import sys

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aira import config
from aira.tools import TOOL_SCHEMAS
from openai import AsyncOpenAI

MODELS = sys.argv[1:] or ["nvidia/nemotron-3-super-120b-a12b:free"]


async def test(c, model):
    print(f"== {model} ==")
    msgs = [
        {"role": "system", "content": "Answer in one short sentence after searching."},
        {"role": "user", "content": "What is herd immunity?"},
    ]
    for round in range(4):
        r = await c.chat.completions.create(
            model=model, messages=msgs, tools=[TOOL_SCHEMAS["web_search"]],
        )
        m = r.choices[0].message
        print(f"  round {round}: content={repr((m.content or '')[:70])} calls={[tc.function.name for tc in (m.tool_calls or [])]}")
        if not m.tool_calls:
            return bool((m.content or "").strip())
        msgs.append({"role": "assistant", "content": m.content or "",
                     "tool_calls": [tc.model_dump() for tc in m.tool_calls]})
        msgs.append({"role": "tool", "tool_call_id": m.tool_calls[0].id,
                     "content": '{"query":"herd immunity","results":[{"title":"WHO","url":"https://who.int","snippet":"indirect protection"}]}'})
    return False


async def main():
    c = AsyncOpenAI(api_key=config.API_KEY, base_url="https://openrouter.ai/api/v1")
    for model in MODELS:
        try:
            ok = await test(c, model)
            print("  RESULT:", "GOOD" if ok else "BAD (no final content)")
        except Exception as e:
            print("  ERROR:", str(e)[:200])


if __name__ == "__main__":
    asyncio.run(main())
