"""Verify UI mode toggles and default research phase in index.html."""
import asyncio
from pathlib import Path
import sys
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aira.server import app


async def test_ui_elements():
    try:
        async with httpx.AsyncClient(timeout=0.5) as client:
            r = await client.get("http://127.0.0.1:8000/")
    except Exception:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            r = await client.get("/")

    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    html = r.text

    # 1. Verify 3-mode segmented toggles
    assert 'data-kind="research"' in html, "Research mode button missing"
    assert 'data-kind="deep"' in html, "Deep Search mode button missing"
    assert 'data-kind="bugfix"' in html, "Code & Fix mode button missing"

    # 2. Verify platform indicators
    assert 'id="activeplatformpill"' in html, "Active platform pill indicator missing"
    assert 'id="dockmodebanner"' in html, "Dock mode banner missing"

    # 3. Verify inbuilt default to research phase
    assert "setKind('research')" in html, "Inbuilt default to research phase missing"

    print("[OK] UI 3-mode toggles, active indicator, and default research phase verified!")


if __name__ == "__main__":
    asyncio.run(test_ui_elements())
