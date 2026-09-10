import asyncio
import base64
import logging
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("aira.tools.web")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

BROWSER_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


class BoundedTTLCache:
    """Bounded in-memory cache with TTL and automatic capacity pruning to prevent memory leaks."""

    def __init__(self, ttl: float, max_size: int = 1000):
        self.ttl = ttl
        self.max_size = max_size
        self._store: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        if key not in self._store:
            return None
        ts, val = self._store[key]
        if time.time() - ts > self.ttl:
            del self._store[key]
            return None
        return val

    def set(self, key: str, val: Any) -> None:
        now = time.time()
        if len(self._store) >= self.max_size:
            cutoff = now - self.ttl
            expired = [k for k, (ts, _) in self._store.items() if ts < cutoff]
            for k in expired:
                del self._store[k]
            if len(self._store) >= self.max_size:
                sorted_keys = sorted(self._store.keys(), key=lambda k: self._store[k][0])
                for k in sorted_keys[: max(1, self.max_size // 5)]:
                    del self._store[k]
        self._store[key] = (now, val)

    def __getitem__(self, key: str) -> Any:
        val = self.get(key)
        if val is None:
            raise KeyError(key)
        return val

    def __setitem__(self, key: str, val: Any) -> None:
        if isinstance(val, tuple) and len(val) == 2 and isinstance(val[0], (int, float)):
            self._store[key] = (float(val[0]), val[1])
        else:
            self.set(key, val)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def clear(self) -> None:
        self._store.clear()


# Bounded TTL caches to eliminate redundant network roundtrips safely
_SEARCH_CACHE = BoundedTTLCache(ttl=600.0, max_size=1000)   # 10 minutes
_FETCH_CACHE = BoundedTTLCache(ttl=1800.0, max_size=1000)   # 30 minutes

# Persistent shared HTTP client with connection pooling
_shared_client: Optional[httpx.AsyncClient] = None


def get_web_client() -> httpx.AsyncClient:
    """Return a shared persistent httpx client with connection pooling."""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        limits = httpx.Limits(
            max_connections=100,
            max_keepalive_connections=50,
            keepalive_expiry=60.0,
        )
        timeout = httpx.Timeout(connect=3.0, read=6.0, write=5.0, pool=5.0)
        _shared_client = httpx.AsyncClient(
            headers=BROWSER_HEADERS,
            limits=limits,
            timeout=timeout,
            follow_redirects=True,
        )
    return _shared_client


async def close_web_client():
    """Close the shared HTTP client during app shutdown."""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
        _shared_client = None


def _unwrap_search_url(href: str) -> str:
    """Unwrap real destination URLs from search engine tracking/redirect links.
    
    Resolves:
    - Bing tracking redirects (https://www.bing.com/ck/a?...&u=a1<base64>...)
    - DuckDuckGo redirect parameters (/l/?uddg=<url>)
    - Google redirect parameters (/url?q=<url>)
    """
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href

    # DuckDuckGo uddg parameter
    if "uddg=" in href:
        m = re.search(r"uddg=([^&]+)", href)
        if m:
            return urllib.parse.unquote(m.group(1))

    # Bing tracking redirect: https://www.bing.com/ck/a?...&u=a1...
    if "bing.com/ck/a" in href:
        m = re.search(r"[?&]u=([^&]+)", href)
        if m:
            val = m.group(1)
            # Bing prefixes with a1 or a0
            raw_b64 = val[2:] if val.startswith(("a1", "a0")) else val
            rem = len(raw_b64) % 4
            if rem:
                raw_b64 += "=" * (4 - rem)
            try:
                decoded = base64.urlsafe_b64decode(raw_b64).decode("utf-8", errors="ignore")
                if decoded.startswith("http"):
                    return decoded
            except Exception:
                pass

    # Google redirect: /url?q=
    if "/url?q=" in href:
        m = re.search(r"[?&]q=([^&]+)", href)
        if m:
            return urllib.parse.unquote(m.group(1))

    return href


async def _search_bing(client: httpx.AsyncClient, query: str, max_results: int) -> List[Dict[str, str]]:
    """Execute Bing search and decode all tracking links into direct destination URLs."""
    results = []
    try:
        r = await client.get(
            "https://www.bing.com/search",
            params={"q": query, "mkt": "en-US", "setlang": "en", "cc": "US"},
            headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Cookie": "SRCHHPGUSR=ADLT=OFF&NRSLT=10&SRCHLANG=en;",
            },
            timeout=4.0,
        )
        if r.status_code == 200:
            soup = BeautifulSoup(r.text[:200_000], "html.parser")
            seen = set()
            for li in soup.select("li.b_algo"):
                a = li.select_one("h2 a")
                if not a or not a.get("href"):
                    continue
                raw_url = a["href"]
                url = _unwrap_search_url(raw_url)
                if not url.startswith("http") or url in seen:
                    continue
                seen.add(url)
                p = li.select_one(".b_caption p, p")
                results.append(
                    {
                        "title": a.get_text(strip=True),
                        "url": url,
                        "snippet": p.get_text(strip=True) if p else "",
                    }
                )
                if len(results) >= max_results:
                    break
    except Exception as e:
        logger.debug("Bing search failed or timed out: %s", e)
    return results


async def _search_wikipedia(client: httpx.AsyncClient, query: str, max_results: int = 2) -> List[Dict[str, str]]:
    """Query Wikipedia OpenSearch API (instant 0.15s responses, zero rate-limiting)."""
    results = []
    try:
        r = await client.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "opensearch", "search": query, "limit": max_results, "format": "json"},
            headers={"User-Agent": "AiraResearchBot/1.0 (https://aira.local/)"},
            timeout=2.0,
        )
        if r.status_code == 200:
            data = r.json()
            if len(data) >= 4:
                titles = data[1]
                snippets = data[2] if len(data) > 2 else []
                urls = data[3]
                for i in range(min(len(titles), len(urls))):
                    snip = snippets[i] if i < len(snippets) else ""
                    results.append({
                        "title": f"Wikipedia: {titles[i]}",
                        "url": urls[i],
                        "snippet": snip or f"Encyclopedic overview of {titles[i]} on Wikipedia.",
                    })
    except Exception as e:
        logger.debug("Wikipedia opensearch failed: %s", e)
    return results


async def _search_stackexchange(client: httpx.AsyncClient, query: str, max_results: int = 3) -> List[Dict[str, str]]:
    """Query StackExchange API for code, bug, and syntax questions with 0 bot blocking."""
    results = []
    try:
        r = await client.get(
            "https://api.stackexchange.com/2.3/search/advanced",
            params={
                "order": "desc",
                "sort": "relevance",
                "q": query,
                "site": "stackoverflow",
                "pagesize": max_results,
            },
            headers={"User-Agent": "AiraBot/1.0"},
            timeout=3.0,
        )
        if r.status_code == 200:
            for it in r.json().get("items", []):
                link = it.get("link", "")
                title = it.get("title", "")
                if link and title:
                    results.append({
                        "title": f"Stack Overflow: {title}",
                        "url": link,
                        "snippet": f"Score: {it.get('score', 0)} | Tags: {', '.join(it.get('tags', []))}",
                    })
    except Exception as e:
        logger.debug("StackExchange search error: %s", e)
    return results


async def _search_ddg(client: httpx.AsyncClient, query: str, max_results: int) -> List[Dict[str, str]]:
    """Query DuckDuckGo with a strict 1.5s connect timeout."""
    results = []
    try:
        r = await client.post(
            "https://lite.duckduckgo.com/lite/",
            data={"q": query},
            timeout=1.5,
        )
        if r.status_code == 200:
            soup = BeautifulSoup(r.text[:150_000], "html.parser")
            seen = set()
            for a in soup.select("a.result-link"):
                url = _unwrap_search_url(a.get("href", ""))
                if not url.startswith("http") or url in seen:
                    continue
                seen.add(url)
                results.append({"title": a.get_text(strip=True), "url": url, "snippet": ""})
                if len(results) >= max_results:
                    break
    except Exception as e:
        logger.debug("DDG Lite search failed: %s", e)
    return results


async def web_search(query: str, max_results: int = 6) -> Dict[str, Any]:
    """Search the web with low latency using parallel multi-tier engines (Bing + StackOverflow + Wikipedia + DDG) with URL unwrapping and bounded TTL caching."""
    q_norm = query.strip().lower()

    # Bounded in-memory cache check
    cached = _SEARCH_CACHE.get(q_norm)
    if cached is not None:
        return cached

    client = get_web_client()
    results: List[Dict[str, str]] = []

    # Check if query is programming/code/error related
    is_code = any(k in q_norm for k in ("error", "exception", "python", "js", "javascript", "code", "bug", "traceback", "fix", "asyncio", "fastapi", "rust", "react", "def ", "class ", "import ", "syntax", "null", "undefined"))

    # Parallel search dispatch across primary engines for sub-second retrieval
    search_tasks = [
        _search_bing(client, query, max_results),
        _search_wikipedia(client, query, max_results=2),
    ]
    if is_code:
        search_tasks.append(_search_stackexchange(client, query, max_results=3))

    gathered = await asyncio.gather(*search_tasks, return_exceptions=True)
    seen_urls = set()

    # If code query, prioritize StackOverflow results first
    if is_code and len(gathered) > 2 and isinstance(gathered[2], list):
        for item in gathered[2]:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                results.append(item)

    # Add Bing search results
    if len(gathered) > 0 and isinstance(gathered[0], list):
        for item in gathered[0]:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                results.append(item)

    # Add Wikipedia encyclopedic overviews
    if len(gathered) > 1 and isinstance(gathered[1], list):
        for item in gathered[1]:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                results.append(item)

    # Fallback to DuckDuckGo if still empty
    if not results:
        try:
            results = await _search_ddg(client, query, max_results)
        except Exception:
            pass

    out = {"query": query, "results": results[:max_results]}
    if results:
        _SEARCH_CACHE.set(q_norm, out)
    return out


# Text lines the Wayback Machine injects that we must strip from extracts.
_WAYBACK_NOISE = (
    "wayback machine", "internet archive", "archive.org",
    "captures", "captured", "saved from", "skip to main content area",
)


def _strip_wayback_noise(text: str) -> str:
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            lines.append(line)
            continue
        low = s.lower()
        if any(low.startswith(n) or low == n for n in _WAYBACK_NOISE):
            continue
        if "web-static.archive.org" in s or "wombat" in low:
            continue
        lines.append(line)
    cleaned = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


async def _wayback_fetch(client: httpx.AsyncClient, url: str):
    """Fast check and retrieval of an archived snapshot (capped at 2.5s + 4.0s max)."""
    if "bing.com/ck/a" in url or "google.com/url" in url:
        return None
    try:
        r = await client.get(
            "https://archive.org/wayback/available",
            params={"url": url},
            timeout=2.5,
        )
        if r.status_code != 200:
            return None
        snap = (r.json().get("archived_snapshots") or {}).get("closest") or {}
        snap_url = snap.get("url")
        if not snap_url or snap.get("status") != "200":
            return None
        r2 = await client.get(snap_url, timeout=4.0)
        if r2.status_code != 200 or len(r2.text) < 400:
            return None
        return r2
    except Exception:
        return None


async def _fetch_stackoverflow(client: httpx.AsyncClient, url: str, max_chars: int) -> Optional[Dict[str, Any]]:
    """Fetch StackOverflow question and top answers via API to bypass Cloudflare anti-bot blocks."""
    m = re.search(r'/questions/(\d+)', url)
    if not m:
        return None
    qid = m.group(1)
    site = "stackoverflow" if "stackoverflow" in url else "stackexchange"
    try:
        r_q = await client.get(
            f"https://api.stackexchange.com/2.3/questions/{qid}",
            params={"site": site, "filter": "withbody"},
            headers={"User-Agent": "AiraBot/1.0"},
            timeout=4.0
        )
        if r_q.status_code != 200:
            return None
        items = r_q.json().get("items", [])
        if not items:
            return None
        q = items[0]
        title = q.get("title", "")
        q_soup = BeautifulSoup(q.get("body", ""), "html.parser")
        q_text = q_soup.get_text("\n", strip=True)

        r_a = await client.get(
            f"https://api.stackexchange.com/2.3/questions/{qid}/answers",
            params={"site": site, "filter": "withbody", "order": "desc", "sort": "votes", "pagesize": 2},
            headers={"User-Agent": "AiraBot/1.0"},
            timeout=4.0
        )
        ans_parts = []
        if r_a.status_code == 200:
            for ans in r_a.json().get("items", []):
                a_soup = BeautifulSoup(ans.get("body", ""), "html.parser")
                score = ans.get("score", 0)
                acc = ans.get("is_accepted", False)
                ans_parts.append(f"--- Top Answer (Score: {score}{', Accepted' if acc else ''}) ---\n" + a_soup.get_text("\n", strip=True))

        full = f"Stack Overflow: {title}\n\n[Question]\n{q_text}\n\n" + "\n\n".join(ans_parts)
        return {
            "url": url,
            "title": f"Stack Overflow: {title}",
            "text": full[:max_chars],
            "via": "stackoverflow api",
            "truncated": len(full) > max_chars,
        }
    except Exception as e:
        logger.debug("SO API fetch failed: %s", e)
        return None


async def _jina_fetch(client: httpx.AsyncClient, url: str, max_chars: int) -> Optional[Dict[str, Any]]:
    """Fast secondary reader (r.jina.ai) to bypass Cloudflare/JS blocking with clean markdown in under 1.5s."""
    try:
        jina_url = f"https://r.jina.ai/{url}"
        r = await client.get(
            jina_url,
            headers={"Accept": "text/plain", "User-Agent": "AiraBot/1.0"},
            timeout=3.5,
        )
        if r.status_code == 200 and len(r.text.strip()) > 80:
            title = url
            m = re.search(r"Title:\s*(.+)", r.text)
            if m:
                title = m.group(1).strip()
            text = re.sub(r"^Title:.*?\nURL Source:.*?\nMarkdown Content:\n", "", r.text, flags=re.DOTALL)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            return {
                "url": url,
                "title": title,
                "text": text[:max_chars],
                "truncated": len(text) > max_chars,
                "via": "jina fast reader",
            }
    except Exception as e:
        logger.debug("Jina reader fetch failed: %s", e)
    return None


async def fetch_page(url: str, max_chars: int = 4000) -> Dict[str, Any]:
    """Fetch a web page with low latency, connection pooling, fast anti-bot resilience, and bounded TTL caching."""
    url = _unwrap_search_url(url.strip())
    
    # Bounded cache check
    cached = _FETCH_CACHE.get(url)
    if cached is not None:
        return cached

    client = get_web_client()

    # Route StackOverflow / StackExchange URLs to API directly
    if "stackoverflow.com" in url or "stackexchange.com" in url:
        so_result = await _fetch_stackoverflow(client, url, max_chars)
        if so_result:
            _FETCH_CACHE.set(url, so_result)
            return so_result

    via = "direct"
    r = None
    last_err = ""

    try:
        r = await client.get(url, timeout=3.5)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        last_err = f"HTTP {code}"
        if code in (403, 429, 503):
            # Fast Jina Reader fallback for anti-bot / Cloudflare blocked sites
            jina_res = await _jina_fetch(client, url, max_chars)
            if jina_res:
                _FETCH_CACHE.set(url, jina_res)
                return jina_res
    except Exception as e:
        last_err = f"{type(e).__name__}"
        # If direct request timed out or connection failed, try Jina reader
        jina_res = await _jina_fetch(client, url, max_chars)
        if jina_res:
            _FETCH_CACHE.set(url, jina_res)
            return jina_res

    if r is None:
        # Fast Wayback Machine check as secondary fallback
        wb = await _wayback_fetch(client, url)
        if wb is not None:
            r, via = wb, "wayback archive"

    if r is None:
        fallback_res = {
            "url": url,
            "title": url,
            "text": f"Note: Direct page extraction from {url} was restricted by the host server ({last_err or 'unavailable'}). Rely on the search snippet or select an alternative source.",
            "via": "restricted",
            "truncated": False,
        }
        _FETCH_CACHE.set(url, fallback_res)
        return fallback_res

    ctype = r.headers.get("content-type", "")
    raw_text = r.text[:250_000]

    if "html" not in ctype and "xml" not in ctype and via == "direct":
        text = raw_text[:max_chars]
        res = {"url": url, "title": url, "text": text, "truncated": len(raw_text) > max_chars, "via": via}
        _FETCH_CACHE.set(url, res)
        return res

    soup = BeautifulSoup(raw_text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else url
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()
    main = soup.body or soup
    text = re.sub(r"\n{3,}", "\n\n", main.get_text("\n", strip=True))
    if via == "wayback archive":
        text = _strip_wayback_noise(text)

    if len(text.strip()) < 60:
        # Site content is JS-rendered or verification gated: try fast Jina Reader
        jina_res = await _jina_fetch(client, url, max_chars)
        if jina_res:
            _FETCH_CACHE.set(url, jina_res)
            return jina_res

        res = {
            "url": str(r.url),
            "title": title,
            "text": f"Note: Content on {url} is protected by client-side verification. Rely on the search snippet or explore other sources.",
            "via": "restricted",
            "truncated": False,
        }
    else:
        res = {
            "url": str(r.url),
            "title": title,
            "text": text[:max_chars],
            "truncated": len(text) > max_chars,
            "via": via,
        }

    _FETCH_CACHE.set(url, res)
    return res
