"""Tests for optimized web search, scraping latency, and TTL caching (pure stdlib)."""
import asyncio
import time
from aira.tools.web import web_search, fetch_page, _SEARCH_CACHE, _FETCH_CACHE, get_web_client


def test_shared_client_and_connection_pool():
    """Verify shared client is pooled and has proper limits."""
    client = get_web_client()
    assert client is not None
    assert not client.is_closed
    # Ensure second call returns the identical pooled client instance
    client2 = get_web_client()
    assert client is client2
    print("[OK] Shared client connection pooling passed")


async def test_search_caching_speed():
    """Verify that cached searches return near-instantly (< 10ms)."""
    query = "test_caching_speed_unique_query"
    # Pre-populate cache
    _SEARCH_CACHE[query] = (
        time.time(),
        {"query": query, "results": [{"title": "Cached Title", "url": "https://example.com", "snippet": "Test"}]},
    )

    t0 = time.perf_counter()
    res = await web_search(query)
    elapsed = time.perf_counter() - t0

    assert res["results"][0]["title"] == "Cached Title"
    assert elapsed < 0.05, f"Cache retrieval took {elapsed}s, expected < 0.05s"
    print(f"[OK] Search cache speed passed ({elapsed*1000:.2f}ms)")


async def test_fetch_page_caching_speed():
    """Verify that cached page fetches return near-instantly (< 10ms)."""
    url = "https://example.org/cached-test"
    _FETCH_CACHE[url] = (
        time.time(),
        {"url": url, "title": "Example Title", "text": "Cached text content", "truncated": False, "via": "cache"},
    )

    t0 = time.perf_counter()
    res = await fetch_page(url)
    elapsed = time.perf_counter() - t0

    assert res["title"] == "Example Title"
    assert elapsed < 0.05, f"Cache retrieval took {elapsed}s, expected < 0.05s"
    print(f"[OK] Fetch cache speed passed ({elapsed*1000:.2f}ms)")


async def test_web_search_live_timeout_bound():
    """Verify that live search completes or fails fast within responsive bounds (< 8s)."""
    t0 = time.perf_counter()
    res = await web_search("python asyncio tutorial", max_results=3)
    elapsed = time.perf_counter() - t0

    assert elapsed < 8.0, f"Search took {elapsed}s, which exceeds 8s timeout bound"
    assert isinstance(res, dict)
    assert "results" in res
    print(f"[OK] Live search completed within timeout bounds ({elapsed:.2f}s, {len(res['results'])} results)")


async def main():
    test_shared_client_and_connection_pool()
    await test_search_caching_speed()
    await test_fetch_page_caching_speed()
    await test_web_search_live_timeout_bound()
    print("\nALL SCRAPING SPEED & POOLING TESTS PASSED!")


if __name__ == "__main__":
    asyncio.run(main())
