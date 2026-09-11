"""Tool registry: OpenAI function schemas + mode availability + dispatcher."""
import inspect

from .sandbox import run_python
from .web import fetch_page, fetch_pages, web_search
from ..files import search_documents_tool

_HANDLERS = {
    "web_search": web_search,
    "fetch_page": fetch_page,
    "fetch_pages": fetch_pages,
    "run_python": run_python,
    "search_documents": search_documents_tool,
}

TOOL_SCHEMAS = {
    "web_search": {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web. Returns up to 6 results with title, url and snippet. "
                "Use focused, specific queries for each factual sub-question."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"}
                },
                "required": ["query"],
            },
        },
    },
    "fetch_page": {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": (
                "Fetch a single web page by URL and return its readable text (truncated). "
                "Use when a snippet is not enough to verify a claim."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Absolute http(s) URL"}
                },
                "required": ["url"],
            },
        },
    },
    "fetch_pages": {
        "type": "function",
        "function": {
            "name": "fetch_pages",
            "description": (
                "Fetch multiple web pages simultaneously in parallel for ultra-fast multi-source research. "
                "Pass a list of up to 5 URLs from search results to scrape all of them at once in ~1 second."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of URLs to scrape in parallel (up to 5)",
                    }
                },
                "required": ["urls"],
            },
        },
    },
    "run_python": {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "Execute a complete Python script in an isolated subprocess (12s timeout, "
                "fresh temp dir). stdout+stderr and the exit code are returned. Use to "
                "reproduce bugs and verify fixes against the failing case and edge cases."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Complete Python script to run"}
                },
                "required": ["code"],
            },
        },
    },
    "search_documents": {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "Search through all uploaded PDFs and attached documents. Returns relevant passages "
                "with document title, page numbers, and exact text excerpts. Use when the user asks "
                "about, compares, or analyzes the uploaded documents/PDFs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Specific search keywords or terms to locate in the uploaded documents",
                    },
                    "doc_name": {
                        "type": "string",
                        "description": "Optional specific document filename to restrict search to",
                    },
                },
                "required": ["query"],
            },
        },
    },
}

RESEARCH_TOOLS = ["web_search", "fetch_pages", "fetch_page", "search_documents"]
BUGFIX_TOOLS = ["web_search", "fetch_pages", "fetch_page", "run_python", "search_documents"]


async def dispatch(name: str, args: dict):
    """Call a tool by name, ignoring unexpected extra arguments."""
    fn = _HANDLERS.get(name)
    if fn is None:
        raise ValueError(f"unknown tool: {name}")
    sig = inspect.signature(fn)
    clean = {k: v for k, v in (args or {}).items() if k in sig.parameters}
    return await fn(**clean)
