import asyncio
from aira.agent import run_agent

async def test_mode(mode: str, question: str):
    print(f"\n==========================================")
    print(f"Testing Agent [{mode}]: {question}")
    print(f"==========================================")
    events = []
    final_answer = ""
    async for ev in run_agent(mode, "short", question):
        events.append(ev)
        if "status" in ev:
            print(f"  [Status] {ev['status']}")
        elif "tool" in ev:
            print(f"  [Tool Call] {ev['tool']} -> {ev.get('args')}")
        elif "tool_result" in ev:
            print(f"  [Tool Result] {ev['tool_result']} (ok={ev.get('ok')})")
        elif "final" in ev:
            final_answer = ev["final"]
            print(f"  [Final Answer Received!]")

    print(f"\nTotal events: {len(events)}")
    print(f"Answer length: {len(final_answer)}")
    print(f"Answer preview:\n{final_answer[:300]}...")
    assert len(final_answer) > 40, f"Answer too short: {final_answer}"
    print(f"[OK] Mode {mode} passed!")

async def main():
    # Test Research
    await test_mode("RESEARCH", "What is WebAssembly?")
    # Test Code & Fix
    await test_mode("BUG_FIX", "How do I fix Python RecursionError?")

if __name__ == "__main__":
    asyncio.run(main())
