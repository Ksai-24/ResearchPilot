"""End-to-end live tests against the running server: both modes + SSE parsing."""
import asyncio
import json
import sys

import httpx

BASE = "http://127.0.0.1:8000"


async def ask(mode, depth, question, timeout=240):
    events, answer, err = [], "", None
    async with httpx.AsyncClient(timeout=timeout) as c:
        async with c.stream(
            "POST",
            f"{BASE}/api/ask",
            json={"mode": mode, "depth": depth, "question": question},
        ) as r:
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
                    except json.JSONDecodeError:
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

    print("== T1: RESEARCH short — herd immunity ==")
    tools, ans, err = await ask("RESEARCH", "short", "What is herd immunity?")
    print("  tools:", tools or "none", "| answer len:", len(ans))
    print("  head:", (ans or "")[:160].replace("\n", " "))
    if err:
        print("  ERROR:", err)
    if err or not ans.strip() or "web_search" not in tools:
        ok = False
        print("  FAIL")

    print("\n== T2: BUG_FIX short — average([]) ==")
    q3 = (
        "This crashes. Fix it.\n\n```python\n"
        "def average(nums):\n    total = 0\n    for n in nums:\n        total += n\n"
        "    return total / len(nums)\n\nprint(average([]))\n```"
    )
    tools, ans, err = await ask("BUG_FIX", "short", q3)
    print("  tools:", tools or "none", "| answer len:", len(ans))
    print("  head:", (ans or "")[:200].replace("\n", " "))
    if err:
        print("  ERROR:", err)
    if err or not ans.strip() or "run_python" not in tools:
        ok = False
        print("  FAIL")
    if "ZeroDivisionError" not in ans and "division by zero" not in ans.lower():
        print("  WARN: answer doesn't name the root cause")

    print("\n== T3: unknown mode falls back to RESEARCH ==")
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            f"{BASE}/api/ask",
            json={"mode": "hacker", "depth": "short", "question": "hi"},
        )
        print("  status:", r.status_code, "(200 + SSE stream expected)")
        if r.status_code != 200:
            ok = False
            print("  FAIL")

    print("\n== T4: empty question rejected ==")
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{BASE}/api/ask", json={"mode": "RESEARCH", "depth": "short", "question": "  "})
        print("  status:", r.status_code, "(400 expected)")
        if r.status_code != 400:
            ok = False
            print("  FAIL")

    print("\nRESULT:", "ALL PASS" if ok else "SOME FAILED")
    sys.exit(0 if ok else 1)


asyncio.run(main())
