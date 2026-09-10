"""Live regression: all 3 kind-pills (research / deep / bugfix) on the new UI's backend."""
import asyncio
import json
import sys

import httpx

BASE = "http://127.0.0.1:8000"


async def ask(mode, depth, question, timeout=300):
    events, answer, err = [], "", None
    async with httpx.AsyncClient(timeout=timeout) as c:
        async with c.stream("POST", f"{BASE}/api/ask",
                            json={"mode": mode, "depth": depth, "question": question}) as r:
            r.raise_for_status()
            buf = ""
            async for chunk in r.aiter_text():
                buf += chunk
                while "\n\n" in buf:
                    line, buf = buf.split("\n\n", 1)
                    line = line.strip()
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    try:
                        ev = json.loads(line[6:])
                    except Exception:
                        continue
                    if ev.get("type") == "tool":
                        events.append(ev["tool"])
                    elif ev.get("type") == "final":
                        answer = ev["answer"]
                    elif ev.get("type") == "error":
                        err = ev["message"]
    return events, answer, err


async def main():
    ok = True

    print("== R1: RESEARCH short (Research pill) ==")
    tools, ans, err = await ask("RESEARCH", "short", "What is quantum computing?")
    print("  tools:", tools or "none", "| err:", err)
    print("  head:", (ans or "")[:130].replace("\n", " "))
    if err or not ans.strip() or "web_search" not in tools:
        ok = False; print("  FAIL")

    print("== R2: RESEARCH full theory (Deep Search pill) ==")
    tools, ans, err = await ask("RESEARCH", "full theory",
                                "Is nuclear fusion power realistically commercializable by 2040?")
    print("  tools:", len(tools), "calls | err:", err)
    print("  head:", (ans or "")[:130].replace("\n", " "))
    has_src = "sources" in (ans or "").lower()
    if err or not ans.strip() or not has_src:
        ok = False; print("  FAIL (no sources?)")

    print("== R3: BUG_FIX short (Code & Fix pill) ==")
    q3 = ("This crashes. Fix it.\n\n```python\ndef average(nums):\n    total = 0\n"
          "    for n in nums:\n        total += n\n    return total / len(nums)\n\nprint(average([]))\n```")
    tools, ans, err = await ask("BUG_FIX", "short", q3)
    print("  tools:", tools or "none", "| err:", err)
    print("  head:", (ans or "")[:130].replace("\n", " "))
    if err or not ans.strip() or "run_python" not in tools:
        ok = False; print("  FAIL")
    if "zero" not in (ans or "").lower():
        print("  WARN: root cause not named")

    print("\nRESULT:", "ALL PASS" if ok else "SOME FAILED")
    sys.exit(0 if ok else 1)


asyncio.run(main())
