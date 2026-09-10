"""Live tests v2: upload + export + ask (with file context) on the running server."""
import asyncio
import io
import sys

import httpx

BASE = "http://127.0.0.1:8000"


async def ask(mode, depth, question, timeout=240):
    events, answer, err = [], "", None
    async with httpx.AsyncClient(timeout=timeout) as c:
        async with c.stream(
            "POST", f"{BASE}/api/ask",
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
                    except Exception:
                        continue
                    if ev.get("type") == "tool":
                        events.append(ev["tool"])
                    elif ev.get("type") == "final":
                        answer = ev["answer"]
                    elif ev.get("type") == "error":
                        err = ev["message"]
    return events, answer, err


import json


async def main():
    ok = True

    print("== T1: health ==")
    async with httpx.AsyncClient(timeout=30) as c:
        h = (await c.get(f"{BASE}/api/health")).json()
        print("  ", h)
        if not h.get("ok"):
            ok = False; print("  FAIL")

    print("== T2: upload txt ==")
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            f"{BASE}/api/upload",
            files={"file": ("notes.txt", b"Aira is our agent. The secret codeword is BANANA72.", "text/plain")},
        )
        j = r.json()
        print("  status:", r.status_code, "| chars:", j.get("chars"))
        if r.status_code != 200 or "BANANA72" not in j.get("text", ""):
            ok = False; print("  FAIL", j)

    print("== T3: upload docx ==")
    from docx import Document
    d = Document(); d.add_paragraph("Research notes: the project deadline is Friday.")
    bio = io.BytesIO(); d.save(bio)
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{BASE}/api/upload", files={"file": ("notes.docx", bio.getvalue(), "application/octet-stream")})
        j = r.json()
        print("  status:", r.status_code, "| chars:", j.get("chars"))
        if r.status_code != 200 or "deadline is Friday" not in j.get("text", ""):
            ok = False; print("  FAIL", j)

    print("== T4: upload rejects unsupported type ==")
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{BASE}/api/upload", files={"file": ("x.exe", b"MZ...", "application/octet-stream")})
        print("  status:", r.status_code, "(422 expected)")
        if r.status_code != 422:
            ok = False; print("  FAIL")

    print("== T5: export txt/docx/pdf ==")
    msgs = [
        {"role": "user", "text": "What is herd immunity?"},
        {"role": "assistant", "text": "Indirect protection when enough people are immune [1].",
         "sources": ["WHO — https://who.int"], "tools": ["Searching the web"]},
    ]
    for fmt in ("txt", "docx", "pdf"):
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{BASE}/api/export",
                json={"format": fmt, "mode": "BUG_FIX", "title": "avg bug", "messages": msgs},
            )
            cd = r.headers.get("content-disposition", "")
            print(f"  {fmt}: {r.status_code} {len(r.content)}B {cd}")
            if r.status_code != 200:
                ok = False; print("  FAIL")

    print("== T6: export rejects empty ==")
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{BASE}/api/export", json={"format": "pdf", "messages": []})
        print("  status:", r.status_code, "(400 expected)")
        if r.status_code != 400:
            ok = False; print("  FAIL")

    print("== T7: agent answers using uploaded file as reference ==")
    q = "I'm attaching notes. According to the attached file, what is the secret codeword and the deadline? Quote them exactly.\n\n--- ATTACHED FILES (user uploads, use as reference/context) ---\n\n[FILE: notes.txt]\nAira is our agent. The secret codeword is BANANA72. The project deadline is Friday.\n"
    tools, ans, err = await ask("RESEARCH", "short", q)
    print("  tools:", tools or "none", "| err:", err)
    print("  answer head:", (ans or "")[:180].replace("\n", " "))
    up = (ans or "").upper()
    if err or "BANANA72" not in up or "FRIDAY" not in up:
        ok = False; print("  FAIL: file content not used")

    print("\nRESULT:", "ALL PASS" if ok else "SOME FAILED")
    sys.exit(0 if ok else 1)


asyncio.run(main())
