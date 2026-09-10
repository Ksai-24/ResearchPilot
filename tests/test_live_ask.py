import asyncio
import json
import httpx

async def test_live_ask():
    print("Testing live /api/ask SSE streaming...")
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream(
            "POST",
            "http://127.0.0.1:8000/api/ask",
            json={"mode": "RESEARCH", "depth": "short", "question": "What is Python GIL?"}
        ) as response:
            assert response.status_code == 200
            tools_used = []
            final_answer = ""
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                    if event.get("type") == "tool":
                        tools_used.append(event.get("tool") or event.get("status"))
                        print(f"  [Tool Event]: {event.get('status') or event.get('tool')}")
                    elif event.get("type") == "final":
                        final_answer = event.get("answer", "")
                except Exception:
                    pass
            print(f"\nCompleted live query. Tools used: {tools_used}")
            print(f"Final answer preview (first 250 chars):\n{final_answer[:250]}...")
            assert len(final_answer) > 50, "Answer was too short or empty!"
            print("Live SSE query passed successfully!")

if __name__ == "__main__":
    asyncio.run(test_live_ask())
