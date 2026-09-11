"""The agent loop: plan -> tool calls -> verified final answer.

Streams agent-level events (not token deltas): 'status', 'tool', 'tool_result',
'final'. The server wraps these into SSE; the UI shows quiet progress ticks.
"""
import asyncio
import json
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from . import config
from .tools import dispatch, TOOL_SCHEMAS, RESEARCH_TOOLS, BUGFIX_TOOLS
from .prompt import system_prompt
from .load_balancer import load_balancer

# Keep the request under provider TPM limits: recent tool results at full
# size, older ones elided to a stub.
FULL_TOOL_RESULTS = 4
ELIDED_STUB = 500
TOTAL_CHAR_BUDGET = 70_000

# Brief user-visible progress lines shown while the agent works.
STATUS_LINES = {
    "web_search": "Searching the web",
    "fetch_page": "Reading a source",
    "run_python": "Running code to verify",
    "search_documents": "Searching uploaded documents",
}


def _chat_kwargs(route: Dict[str, Any], messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], stream: bool) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "model": route.get("model") or config.MODEL,
        "messages": messages,
        "stream": stream,
        "timeout": config.REQUEST_TIMEOUT,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if config.TEMPERATURE != "":
        try:
            kwargs["temperature"] = float(config.TEMPERATURE)
        except ValueError:
            pass
    if config.MAX_TOKENS:
        try:
            kwargs["max_tokens"] = int(config.MAX_TOKENS)
        except ValueError:
            kwargs["max_tokens"] = 4096
    else:
        kwargs["max_tokens"] = 4096
    return kwargs


def _elide_old_tool_results(messages: List[Dict[str, Any]]) -> None:
    """Shrink older tool-result contents so requests stay under TPM limits.

    Keeps the most recent FULL_TOOL_RESULTS tool messages intact and replaces
    older ones' contents with a stub. Assistant tool_call turns are kept as-is
    (they carry the call ids the API requires to stay consistent).
    """
    tool_msg_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    old_indices = set(tool_msg_indices[:-FULL_TOOL_RESULTS]) if len(tool_msg_indices) > FULL_TOOL_RESULTS else set()
    for i in old_indices:
        content = messages[i].get("content") or ""
        if len(content) > ELIDED_STUB:
            messages[i]["content"] = (
                content[:ELIDED_STUB]
                + "\n…[older result elided to stay within request limits]"
            )
    # Hard cap: if the whole history still exceeds the budget, trim the oldest
    # tool results entirely.
    total = sum(len(str(m.get("content") or "")) for m in messages)
    if total > TOTAL_CHAR_BUDGET:
        for i in sorted(old_indices):
            messages[i]["content"] = "[elided]"
            total = sum(len(str(m.get("content") or "")) for m in messages)
            if total <= TOTAL_CHAR_BUDGET:
                break


def _resp_ok(resp: Any, require_content: bool) -> bool:
    """A response is usable if it has tool calls, or non-empty content.

    Reasoning-style models sometimes return content='' with no tool calls
    (the text lands in a reasoning channel) — that's a bad response we retry.
    Also rejects raw 'channel syntax' leaks (<|start|>assistant<|channel|>...)
    where the provider fails to parse the model's tool call into tool_calls.
    """
    choices = getattr(resp, "choices", None)
    if not choices or choices[0].message is None:
        return False
    m = choices[0].message
    content = (m.content or "").strip()
    has_calls = bool(getattr(m, "tool_calls", None))
    if _is_raw_leak(content):
        # leaked channel syntax: only acceptable if real tool_calls came with it
        return has_calls and not require_content
    if require_content:
        return bool(content)
    return has_calls or bool(content)


_RAW_LEAK_MARKERS = (
    "<|start|>", "<|channel|>", "<|constrain|>", "<|message|>",
    "<|call|>", "<|end|>", "<|return|>", "<|commentary|>",
)


def _is_raw_leak(text: str) -> bool:
    t = text or ""
    return any(m in t for m in _RAW_LEAK_MARKERS)


async def _create_with_retry(route: Dict[str, Any], kwargs: Dict[str, Any], require_content: bool = False) -> Any:
    """Create a completion, with retries, high-traffic switching, and model fallbacks."""
    current_route = dict(route)
    if load_balancer.is_primary_cooling_down():
        current_route = load_balancer.get_route(force_secondary=True)

    client = current_route["client"]
    target_model = kwargs.get("model") or current_route.get("model") or config.MODEL
    models = [target_model] + [
        m for m in config.FALLBACK_MODELS if m != target_model
    ]
    last_exc = None
    for model in models:
        for attempt in range(2):
            call_kwargs = {**kwargs, "model": model}
            try:
                resp = await client.chat.completions.create(**call_kwargs)
                if _resp_ok(resp, require_content):
                    return resp
                last_exc = RuntimeError(f"{model} returned an empty response")
                await asyncio.sleep(1)
            except Exception as e:
                last_exc = e
                msg = str(e)
                is_limit = any(
                    s in msg
                    for s in ("rate_limit", "rate limit", "tokens per minute", "TPM",
                              "Request too large", "413", "429", "capacity", "503",
                              "insufficient_user_quota", "credit limit is insufficient",
                              "quota is running low", "402", "403")
                )
                is_provider_flake = (
                    "Provider returned error" in msg
                    or "Provider for this model is currently unavailable" in msg
                )
                if is_limit:
                    print(f"  [Switching to Secondary API ({config.SECONDARY_MODEL}) due to limit: {msg[:80]}]", flush=True)
                    load_balancer.report_rate_limit(current_route.get("provider", "primary"))
                    current_route = load_balancer.get_route(force_secondary=True)
                    client = current_route["client"]
                    sec_model = current_route.get("model") or config.SECONDARY_MODEL
                    try:
                        sec_kwargs = {**kwargs, "model": sec_model}
                        resp = await client.chat.completions.create(**sec_kwargs)
                        if _resp_ok(resp, require_content):
                            return resp
                    except Exception as sec_e:
                        last_exc = sec_e
                        print(f"  [Secondary failover attempt failed on {sec_model}: {sec_e}]", flush=True)
                        try:
                            tert_route = load_balancer.get_route(force_tertiary=True)
                            tert_client = tert_route["client"]
                            tert_model = tert_route.get("model") or config.TERTIARY_MODEL
                            tert_kwargs = {**kwargs, "model": tert_model}
                            print(f"  [Switching to Tertiary API ({tert_model})]", flush=True)
                            resp = await tert_client.chat.completions.create(**tert_kwargs)
                            if _resp_ok(resp, require_content):
                                return resp
                        except Exception as tert_e:
                            last_exc = tert_e
                            print(f"  [Tertiary failover attempt failed: {tert_e}]", flush=True)
                    break
                elif is_provider_flake:
                    print(f"  [upstream provider flake on {model} — trying next]", flush=True)
                    break  # next model in the chain
                elif attempt == 0:
                    if "401" in msg or "User not found" in msg or "invalid_api_key" in msg or "Unauthorized" in msg:
                        raise RuntimeError(
                            f"LLM API Authentication Failed (HTTP 401: Invalid or expired API Key).\n\n"
                            f"**Provider Details**: `{msg}`\n\n"
                            f"🔧 **How to Fix**:\n"
                            f"1. Open your `.env` file.\n"
                            f"2. Set a valid `AIRA_API_KEY` from OpenRouter, Google Gemini, Groq, or OpenAI.\n"
                            f"3. Save `.env` — AIRA will hot-reload your new key automatically!"
                        ) from e
                    raise  # genuine error (bad request): don't hammer
            if attempt == 2 and model != models[-1]:
                print(f"  [falling back from {model}]", flush=True)
    raise last_exc if last_exc else RuntimeError("all models failed")


def _looks_like_scratch(text: str) -> bool:
    """Detect planning/narration leaked into a final answer (small-model habit)."""
    if _is_raw_leak(text):
        return True
    t = text.lower()
    if "[1]" in t or "sources:" in t or "##" in text:
        return False  # looks like a real structured answer
    scratch_markers = (
        "let's try", "let me try", "we need to", "we should", "i will",
        "maybe use", "another approach", "i'm going to", "wait,",
        "we couldn't fetch", "let's search", "let me search",
    )
    hits = sum(1 for m in scratch_markers if m in t)
    return hits >= 2 or (hits >= 1 and len(text) < 600)


def _asks_permission(text: str) -> bool:
    """Model ended its turn asking the user whether to proceed, instead of acting."""
    t = (text or "").lower()
    if len(t) > 700:
        return False
    markers = (
        "would you like me to proceed", "shall i proceed", "should i proceed",
        "do you want me to", "shall i search", "should i search",
        "would you like me to search", "let me know if you",
        "i can use `web_search`", "i can use web_search",
        "i can only use the `web_search`", "i can only use web_search",
        "please rephrase your request", "rephrase your request using",
    )
    return any(m in t for m in markers)


async def run_agent(mode: str, depth: str, question: str) -> AsyncGenerator[Dict[str, Any], None]:
    """Yield event dicts describing agent progress, ending with {'final': ...}."""
    config.reload_config()
    async with load_balancer.acquire_slot():
        route = load_balancer.get_route()
        if route.get("is_high_traffic"):
            yield {
                "tool": "load_balancer",
                "status": f"High Traffic: Routed to {route.get('provider')} API ({route.get('model')})",
                "args": {"traffic": "high", "reason": route.get("reason", "")},
            }

        sysprompt = system_prompt(mode, depth)
        user_msg = f"Mode: {mode}\nDepth: {depth}\n\n{question.strip()}"
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": sysprompt},
            {"role": "user", "content": user_msg},
        ]
        tool_names = BUGFIX_TOOLS if mode == "BUG_FIX" else RESEARCH_TOOLS
        tools = [TOOL_SCHEMAS[n] for n in tool_names]

        rounds = 0
        while True:
            if rounds >= config.MAX_TOOL_ROUNDS:
                # Budget exhausted: force a real final answer with one no-tools call.
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "You have exhausted your tool budget. Now write the FINAL "
                            "ANSWER for the user, in this exact shape:\n"
                            "1. A one-sentence direct answer first.\n"
                            "2. Supporting sections with headed markdown, citing your "
                            "sources inline with [n] markers.\n"
                            "3. A numbered Sources list at the end.\n"
                            "IMPORTANT: the search results you already retrieved (their "
                            "titles, URLs, and snippets) ARE your sources — cite them even "
                            "if you never fetched the full pages. Never claim you found "
                            "nothing when your search results contain relevant material.\n"
                            "This is NOT the place for planning, narration, or mentions of "
                            "searching/fetching — the user must never see your scratch "
                            "work. If the evidence you gathered is mixed or contested, "
                            "present both sides with their citations. If you could not "
                            "verify something, say so plainly. Write the complete answer "
                            "to the user's question now."
                        ),
                    }
                )
                resp = await _create_with_retry(
                    route, _chat_kwargs(route, messages, [], stream=False),
                    require_content=True,
                )
                final = resp.choices[0].message.content or ""
                if _looks_like_scratch(final):
                    # Planning text leaked out as the answer: push once more, harder.
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "That previous message was your internal planning, not an "
                                "answer. Reply with ONLY the final answer to the user: "
                                "direct answer sentence, supporting sections with [n] "
                                "citations, then the numbered Sources list. No narration."
                            ),
                        }
                    )
                    resp = await _create_with_retry(
                        route, _chat_kwargs(route, messages, [], stream=False),
                        require_content=True,
                    )
                    final = resp.choices[0].message.content or ""
                yield {"final": final}
                return

            # Non-streaming call: we need the full message to inspect tool calls.
            _elide_old_tool_results(messages)
            resp = await _create_with_retry(
                route, _chat_kwargs(route, messages, tools, stream=False),
                require_content=False,
            )
            msg = resp.choices[0].message

            tool_calls = msg.tool_calls or []
            if not tool_calls:
                content = msg.content or ""
                if _asks_permission(content) and rounds < config.MAX_TOOL_ROUNDS:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Yes — proceed now with the tools you have. Do not ask for "
                                "further permission; complete the task end-to-end and give "
                                "the final answer."
                            ),
                        }
                    )
                    rounds += 1
                    continue
                if _is_raw_leak(content) or _looks_like_scratch(content):
                    messages.append({"role": "system", "content": (
                        "Your previous message contained raw internal formatting or planning "
                        "notes. Reply with ONLY the final answer to the user: direct answer "
                        "first, supporting sections with [n] citations, then the numbered "
                        "Sources list. No internal syntax, no narration."
                    )})
                    resp = await _create_with_retry(
                        route, _chat_kwargs(route, messages, [], stream=False),
                        require_content=True,
                    )
                    content = resp.choices[0].message.content or ""
                yield {"final": content}
                return

            rounds += 1
            # Mid-loop nudge: at half budget, tell the model to wrap up retrieval
            # and move to synthesis. Prevents endless near-identical searches.
            if rounds == max(2, config.MAX_TOOL_ROUNDS // 2):
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Half of your tool budget is gone. You have enough search "
                            "results: STOP searching for more and start fetching 1-3 of "
                            "the most promising result URLs (or use the snippets you "
                            "already have), then write the final answer. Search-result "
                            "titles+URLs you already retrieved ARE valid citations — "
                            "cite them as [n] with their URLs in the Sources list."
                        ),
                    }
                )
            # Append the assistant turn, then execute each tool call.
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [tc.model_dump() for tc in tool_calls],
                }
            )
            parsed_calls = []
            for tc in tool_calls:
                fn = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                parsed_calls.append((tc, fn, args))
                yield {
                    "tool": fn,
                    "status": STATUS_LINES.get(fn, "Working"),
                    "args": {k: (v if len(str(v)) < 90 else str(v)[:87] + "…") for k, v in args.items()},
                }

            async def _run_tool_call(tc_obj: Any, fn_name: str, fn_args: Dict[str, Any]) -> Tuple[Any, str, str]:
                try:
                    result = await dispatch(fn_name, fn_args)
                    payload = json.dumps(result, ensure_ascii=False, default=str)
                except ValueError as e:
                    avail = ", ".join(tool_names)
                    payload = json.dumps({
                        "error": (
                            f"{e}. You must call ONLY these tools, by exact name, with the "
                            f"arguments their schemas define: {avail}. Retry your last step "
                            "using one of those exact tool names."
                        )
                    })
                except Exception as e:
                    payload = json.dumps({"error": f"{type(e).__name__}: {e}"})
                return tc_obj, fn_name, payload

            tool_results = await asyncio.gather(
                *[_run_tool_call(tc, fn, args) for tc, fn, args in parsed_calls]
            )

            for tc, fn, payload in tool_results:
                yield {"tool_result": fn, "ok": not payload.startswith('{"error"')}
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": payload[:60000]}
                )
