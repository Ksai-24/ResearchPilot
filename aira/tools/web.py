"""High-speed internet scraping and web search subsystem.

Features:
- Multiplexed HTTP/2 connection pooling with aggressive keep-alive and DNS reuse
- Sub-second parallel search dispatch across Bing, DuckDuckGo, Wikipedia, and StackExchange
- Zero-latency dedicated extractors for Wikipedia (REST API), arXiv (OpenAPI), StackExchange, and GitHub
- Fast Dual-Race Anti-Bot Scraper (Direct HTTP/2 + Jina Fast Reader fallback) for bypassing Cloudflare & paywalls
- Multi-URL parallel batch scraper (`fetch_pages`) for scraping up to 5 web pages simultaneously in ~1 second
- Bounded memory TTL caching to eliminate redundant internet roundtrips
"""
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
    """Bounded in-memory cache with TTL and automatic capacity pruning."""

    def __init__(self, ttl: float, max_size: int = 1500):
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


# Bounded TTL caches
_SEARCH_CACHE = BoundedTTLCache(ttl=900.0, max_size=1500)   # 15 minutes
_FETCH_CACHE = BoundedTTLCache(ttl=3600.0, max_size=2000)   # 60 minutes

# Persistent shared HTTP/2 client
_shared_client: Optional[httpx.AsyncClient] = None


def get_web_client() -> httpx.AsyncClient:
    """Return a shared persistent httpx client with HTTP/2 and high-capacity connection pooling."""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        limits = httpx.Limits(
            max_connections=150,
            max_keepalive_connections=80,
            keepalive_expiry=120.0,
        )
        timeout = httpx.Timeout(connect=2.5, read=5.0, write=4.0, pool=4.0)
        _shared_client = httpx.AsyncClient(
            headers=BROWSER_HEADERS,
            limits=limits,
            timeout=timeout,
            http2=True,
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
    """Unwrap real destination URLs from search engine tracking/redirect links."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href

    # DuckDuckGo uddg parameter
    if "uddg=" in href:
        m = re.search(r"uddg=([^&]+)", href)
        if m:
            return urllib.parse.unquote(m.group(1))

    # Bing tracking redirect
    if "bing.com/ck/a" in href:
        m = re.search(r"[?&]u=([^&]+)", href)
        if m:
            val = m.group(1)
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

    # Google redirect
    if "/url?q=" in href:
        m = re.search(r"[?&]q=([^&]+)", href)
        if m:
            return urllib.parse.unquote(m.group(1))

    return href


# =====================================================================
# Dedicated Fast API Scrapers for High-Traffic Knowledge Hubs
# =====================================================================

async def _fetch_wikipedia_direct(client: httpx.AsyncClient, url: str, max_chars: int) -> Optional[Dict[str, Any]]:
    """Fetch Wikipedia articles directly via REST API in ~80ms without web page bloat."""
    m = re.search(r"wikipedia\.org/wiki/([^#?]+)", url)
    if not m:
        return None
    raw_title = m.group(1)
    title = urllib.parse.unquote(raw_title).replace("_", " ")
    try:
        # Fast REST summary endpoint
        api_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(raw_title)}"
        r = await client.get(api_url, timeout=2.0)
        if r.status_code == 200:
            data = r.json()
            extract_text = data.get("extract", "")
            page_title = data.get("title", title)
            description = data.get("description", "")
            
            # If summary is too short, query intro section
            if len(extract_text) < 300:
                q_url = "https://en.wikipedia.org/w/api.php"
                r2 = await client.get(
                    q_url,
                    params={
                        "action": "query",
                        "prop": "extracts",
                        "exintro": "1",
                        "explaintext": "1",
                        "titles": page_title,
                        "format": "json",
                    },
                    timeout=2.0,
                )
                if r2.status_code == 200:
                    pages = r2.json().get("query", {}).get("pages", {})
                    for p in pages.values():
                        if "extract" in p:
                            extract_text = p["extract"]
                            break

            content = f"Wikipedia: {page_title}\n"
            if description:
                content += f"Summary: {description}\n\n"
            content += extract_text

            return {
                "url": url,
                "title": f"Wikipedia: {page_title}",
                "text": content[:max_chars],
                "via": "wikipedia rest api",
                "truncated": len(content) > max_chars,
            }
    except Exception as e:
        logger.debug("Wikipedia fast fetch error: %s", e)
    return None


async def _fetch_arxiv_direct(client: httpx.AsyncClient, url: str, max_chars: int) -> Optional[Dict[str, Any]]:
    """Fetch ArXiv scientific papers directly via ArXiv Export API in ~120ms."""
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]+\.[0-9]+(?:v[0-9]+)?)", url)
    if not m:
        return None
    arxiv_id = m.group(1)
    try:
        api_url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"
        r = await client.get(api_url, timeout=2.5)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "xml" if "xml" in r.text else "html.parser")
            entry = soup.find("entry")
            if entry:
                title = entry.find("title").get_text(strip=True) if entry.find("title") else f"ArXiv {arxiv_id}"
                summary = entry.find("summary").get_text("\n", strip=True) if entry.find("summary") else ""
                authors = [a.get_text(strip=True) for a in entry.find_all("name")]
                published = entry.find("published").get_text(strip=True) if entry.find("published") else ""
                
                content = (
                    f"ArXiv Paper: {title}\n"
                    f"ArXiv ID: {arxiv_id} | Published: {published[:10]}\n"
                    f"Authors: {', '.join(authors[:5])}\n\n"
                    f"[Abstract]\n{summary}"
                )
                return {
                    "url": url,
                    "title": f"ArXiv: {title}",
                    "text": content[:max_chars],
                    "via": "arxiv export api",
                    "truncated": len(content) > max_chars,
                }
    except Exception as e:
        logger.debug("ArXiv fast fetch error: %s", e)
    return None


async def _fetch_stackoverflow(client: httpx.AsyncClient, url: str, max_chars: int) -> Optional[Dict[str, Any]]:
    """Fetch StackOverflow question and top answers via API to bypass Cloudflare anti-bot blocks."""
    m = re.search(r"/questions/(\d+)", url)
    if not m:
        return None
    qid = m.group(1)
    site = "stackoverflow" if "stackoverflow" in url else "stackexchange"
    try:
        r_q = await client.get(
            f"https://api.stackexchange.com/2.3/questions/{qid}",
            params={"site": site, "filter": "withbody"},
            headers={"User-Agent": "AiraBot/1.0"},
            timeout=3.0,
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
            timeout=3.0,
        )
        ans_parts = []
        if r_a.status_code == 200:
            for ans in r_a.json().get("items", []):
                a_soup = BeautifulSoup(ans.get("body", ""), "html.parser")
                score = ans.get("score", 0)
                acc = ans.get("is_accepted", False)
                ans_parts.append(
                    f"--- Top Answer (Score: {score}{', Accepted' if acc else ''}) ---\n"
                    + a_soup.get_text("\n", strip=True)
                )

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
            timeout=3.0,
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


# =====================================================================
# Search Dispatcher (Parallel Multi-Engine)
# =====================================================================

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
            timeout=3.0,
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
        logger.debug("Bing search failed: %s", e)
    return results


async def _search_wikipedia(client: httpx.AsyncClient, query: str, max_results: int = 2) -> List[Dict[str, str]]:
    """Query Wikipedia OpenSearch API (instant ~100ms responses)."""
    results = []
    try:
        r = await client.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "opensearch", "search": query, "limit": max_results, "format": "json"},
            headers={"User-Agent": "AiraResearchBot/1.0"},
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
    """Query StackExchange API for code and syntax questions."""
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
            timeout=2.5,
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
    """Query DuckDuckGo Lite as instant backup."""
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
    """Search the web with sub-second latency using parallel multi-engine dispatch and bounded TTL caching."""
    q_norm = query.strip().lower()

    # Cache hit check (<1ms)
    cached = _SEARCH_CACHE.get(q_norm)
    if cached is not None:
        return cached

    client = get_web_client()
    results: List[Dict[str, str]] = []

    # Check if query is programming/code related
    is_code = any(
        k in q_norm
        for k in (
            "error", "exception", "python", "js", "javascript", "code", "bug",
            "traceback", "fix", "asyncio", "fastapi", "rust", "react", "def ",
            "class ", "import ", "syntax", "null", "undefined"
        )
    )

    # Parallel search dispatch across primary engines
    search_tasks = [
        _search_bing(client, query, max_results),
        _search_wikipedia(client, query, max_results=2),
    ]
    if is_code:
        search_tasks.append(_search_stackexchange(client, query, max_results=3))

    gathered = await asyncio.gather(*search_tasks, return_exceptions=True)
    seen_urls = set()

    # 1. Prioritize StackOverflow for code queries
    if is_code and len(gathered) > 2 and isinstance(gathered[2], list):
        for item in gathered[2]:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                results.append(item)

    # 2. Add Bing results
    if len(gathered) > 0 and isinstance(gathered[0], list):
        for item in gathered[0]:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                results.append(item)

    # 3. Add Wikipedia encyclopedic overviews
    if len(gathered) > 1 and isinstance(gathered[1], list):
        for item in gathered[1]:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                results.append(item)

    # 4. Fallback to DDG if empty
    if not results:
        try:
            results = await _search_ddg(client, query, max_results)
        except Exception:
            pass

    out = {"query": query, "results": results[:max_results]}
    if results:
        _SEARCH_CACHE.set(q_norm, out)
    return out


# =====================================================================
# High-Speed Page Scrapers & Fast Batch Fetching
# =====================================================================

async def fetch_page(url: str, max_chars: int = 4000) -> Dict[str, Any]:
    """Fetch and scrape a web page with sub-second speed, HTTP/2 pooling, anti-bot bypass, and bounded TTL caching."""
    url = _unwrap_search_url(url.strip())
    
    # Cache hit check
    cached = _FETCH_CACHE.get(url)
    if cached is not None:
        return cached

    client = get_web_client()

    # 1. Fast dedicated scrapers for high-volume domains (80-150ms)
    if "wikipedia.org/wiki/" in url:
        wiki_res = await _fetch_wikipedia_direct(client, url, max_chars)
        if wiki_res:
            _FETCH_CACHE.set(url, wiki_res)
            return wiki_res

    if "arxiv.org/" in url:
        arxiv_res = await _fetch_arxiv_direct(client, url, max_chars)
        if arxiv_res:
            _FETCH_CACHE.set(url, arxiv_res)
            return arxiv_res

    if "stackoverflow.com" in url or "stackexchange.com" in url:
        so_result = await _fetch_stackoverflow(client, url, max_chars)
        if so_result:
            _FETCH_CACHE.set(url, so_result)
            return so_result

    # 2. Fast Direct Scraper with tight connect timeout and early Jina fallback
    via = "direct"
    r = None
    last_err = ""

    try:
        r = await client.get(url, timeout=2.8)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        last_err = f"HTTP {code}"
        if code in (403, 429, 503):
            # Cloudflare / anti-bot challenge: instant Jina reader bypass
            jina_res = await _jina_fetch(client, url, max_chars)
            if jina_res:
                _FETCH_CACHE.set(url, jina_res)
                return jina_res
    except Exception as e:
        last_err = f"{type(e).__name__}"
        # Direct connection timeout or error: instant Jina reader fallback
        jina_res = await _jina_fetch(client, url, max_chars)
        if jina_res:
            _FETCH_CACHE.set(url, jina_res)
            return jina_res

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

    # Non-HTML direct pass
    if "html" not in ctype and "xml" not in ctype:
        text = raw_text[:max_chars]
        res = {"url": url, "title": url, "text": text, "truncated": len(raw_text) > max_chars, "via": via}
        _FETCH_CACHE.set(url, res)
        return res

    # Fast HTML DOM cleaning & extraction
    soup = BeautifulSoup(raw_text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else url
    
    # Strip non-content elements
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "iframe"]):
        tag.decompose()

    # Prioritize main article container if available for cleaner text
    main = soup.find("article") or soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup
    text = re.sub(r"\n{3,}", "\n\n", main.get_text("\n", strip=True))

    if len(text.strip()) < 60:
        # Client-side JS rendered: bypass with Jina Reader
        jina_res = await _jina_fetch(client, url, max_chars)
        if jina_res:
            _FETCH_CACHE.set(url, jina_res)
            return jina_res

        res = {
            "url": str(r.url),
            "title": title,
            "text": f"Note: Content on {url} is protected by client-side verification. Rely on search snippets or alternative sources.",
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


async def fetch_pages(urls: List[str], max_chars_per_page: int = 3500) -> Dict[str, Any]:
    """Fetch and scrape multiple web pages simultaneously in parallel in ~1 second."""
    if not urls:
        return {"count": 0, "pages": []}

    target_urls = urls[:5]
    tasks = [fetch_page(u, max_chars=max_chars_per_page) for u in target_urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    pages = []
    for u, res in zip(target_urls, results):
        if isinstance(res, Exception):
            pages.append({"url": u, "error": str(res), "text": "", "via": "error"})
        else:
            pages.append(res)

    return {"count": len(pages), "pages": pages}
