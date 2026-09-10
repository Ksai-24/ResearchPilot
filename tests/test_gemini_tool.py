import asyncio
from openai import AsyncOpenAI
from aira import config
from aira.tools import TOOL_SCHEMAS

async def main():
    client = AsyncOpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)
    model = "google/gemini-2.5-flash-lite"
    print(f"Testing model {model} with max_tokens=2500...")
    try:
        r = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": "Search the web for what is WebAssembly."}
            ],
            tools=[TOOL_SCHEMAS["web_search"]],
            max_tokens=2500,
        )
        msg = r.choices[0].message
        print("Content:", msg.content)
        print("Tool calls:", [tc.function.name for tc in (msg.tool_calls or [])])
    except Exception as e:
        print("Error on Gemini with max_tokens:", e)

if __name__ == "__main__":
    asyncio.run(main())
