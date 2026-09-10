"""LLM-as-judge eval harness for Aira.

Runs each test case against the live agent, then grades the output with the
exact judge prompt from the spec, parsed as strict JSON.

Usage:
    python -m aira.eval              # run all 3 test cases
    python -m aira.eval --only 1     # just test case 1
    python -m aira.eval --only 2,3    # cases 2 and 3
    python -m aira.eval --save out.json
"""
import argparse
import asyncio
import json
import re
import sys

from openai import AsyncOpenAI

from . import config
from .agent import run_agent, _create_with_retry  # noqa: F401 (shared retry logic)

judge_client = None


def _get_judge_client():
    global judge_client
    if judge_client is None:
        if not config.API_KEY:
            raise RuntimeError(
                "AIRA_API_KEY is not set — the judge needs the same key as Aira."
            )
        judge_client = AsyncOpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)
    return judge_client


# ---------------------------------------------------------------- test cases

CASE1_CODE = None  # research question only

TEST_CASES = [
    {
        "id": 1,
        "name": "Research mode, depth control (1-mark)",
        "mode": "RESEARCH",
        "depth": "1-mark",
        "question": "What is herd immunity?",
        "judge_rubric": """You are evaluating an AI research assistant's output for depth compliance.

REQUESTED DEPTH: 1-mark (single sentence/phrase, max 1 citation, no elaboration)
AIRA'S OUTPUT: {output}

Score 0-5 on each, then give a total /15:
1. LENGTH COMPLIANCE — Is this genuinely a 1-mark-length answer, not a
   disguised paragraph?
2. CORRECTNESS — Is the core claim factually accurate?
3. NO OVER-CITATION — Does it avoid citation clutter inappropriate for this depth?

Return strictly as JSON:
{{"length_compliance": 0-5, "correctness": 0-5, "no_over_citation": 0-5,
 "total": 0-15, "verdict": "pass" or "fail", "reason": "one sentence"}}
Fail if total < 10.""",
    },
    {
        "id": 2,
        "name": "Research mode, self-verification on a contested claim (full theory)",
        "mode": "RESEARCH",
        "depth": "full theory",
        "question": "Do mRNA vaccine boosters extend memory-cell durability, or just antibody levels temporarily?",
        "judge_rubric": """You are evaluating whether an AI research assistant properly handled a
CONTESTED claim rather than false-confidently resolving it.

AIRA'S OUTPUT: {output}

Score 0-5 on each, then give a total /20:
1. ACKNOWLEDGES DISAGREEMENT — Does it explicitly state that sources disagree,
   rather than presenting one view as settled fact?
2. NO FABRICATION — Are all cited claims plausible and attributed, with no
   invented study names or fake-sounding statistics?
3. STRUCTURE MATCHES DEPTH — Does it actually use headed sections appropriate
   for "full theory" depth, not a shallow short-form answer?
4. SOURCE LIST PRESENT — Does it end with a numbered, real-looking source list?

Return strictly as JSON:
{{"acknowledges_disagreement": 0-5, "no_fabrication": 0-5,
 "structure_matches_depth": 0-5, "source_list_present": 0-5,
 "total": 0-20, "verdict": "pass" or "fail", "reason": "one sentence"}}
Fail if total < 14, OR if acknowledges_disagreement < 3 (this is the critical
failure mode — confidently resolving a contested claim is worse than a low
score anywhere else).""",
    },
    {
        "id": 3,
        "name": "Bug Fix mode, fix correctness + verification honesty (short)",
        "mode": "BUG_FIX",
        "depth": "short",
        "question": (
            "This crashes. Fix it.\n\n```python\n"
            "def average(nums):\n"
            "    total = 0\n"
            "    for n in nums:\n"
            "        total += n\n"
            "    return total / len(nums)\n\n"
            "print(average([]))\n```"
        ),
        "judge_rubric": """You are evaluating an AI coding assistant's bug fix for correctness and
honesty about verification.

ORIGINAL BUG: division by zero when `average([])` is called on an empty list.
AIRA'S OUTPUT: {output}

Score 0-5 on each, then give a total /20:
1. CORRECT DIAGNOSIS — Does it correctly identify the empty-list /
   division-by-zero cause (not a different, wrong cause)?
2. MINIMAL FIX — Is the fix scoped to the actual problem, without rewriting
   unrelated parts of the function?
3. DEPTH COMPLIANCE — Does it match "short" depth (fix + one-sentence root
   cause), not a full essay or a bare diff with zero explanation?
4. EDGE CASE AWARENESS — Does the fix still work for a normal non-empty list
   (i.e. it didn't break the working case while fixing the broken one)?

Return strictly as JSON:
{{"correct_diagnosis": 0-5, "minimal_fix": 0-5, "depth_compliance": 0-5,
 "edge_case_awareness": 0-5, "total": 0-20, "verdict": "pass" or "fail",
 "reason": "one sentence"}}
Fail if total < 14, OR if correct_diagnosis < 4 (a fix for the wrong root
cause is a critical failure regardless of how clean the code looks).""",
    },
]


# ---------------------------------------------------------------- agent run

async def run_case(case: dict) -> dict:
    """Run one test case against the live agent; return output + tool trace."""
    events = []
    answer = ""
    error = None
    try:
        async for ev in run_agent(case["mode"], case["depth"], case["question"]):
            if "final" in ev:
                answer = ev["final"]
            else:
                events.append(ev)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    return {
        "id": case["id"],
        "name": case["name"],
        "mode": case["mode"],
        "depth": case["depth"],
        "question": case["question"],
        "output": answer,
        "tool_trace": events,
        "agent_error": error,
    }


# ---------------------------------------------------------------- judging

def _extract_json(text: str) -> dict | None:
    """Parse the judge's strict-JSON reply; tolerate ```json fences."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


async def judge(case: dict, output: str) -> dict:
    """Send the rubric (with output pasted in) to the judge model; parse JSON."""
    prompt = case["judge_rubric"].format(output=output)
    kwargs = {
        "model": config.JUDGE_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a strict, fair evaluator. Return ONLY the JSON object "
                    "specified in the rubric — no prose before or after."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "timeout": config.REQUEST_TIMEOUT,
    }
    if config.TEMPERATURE != "":
        try:
            kwargs["temperature"] = 0.0
        except ValueError:
            pass
    resp = await _create_with_retry(_get_judge_client(), kwargs, require_content=True)
    parsed = _extract_json(resp.choices[0].message.content or "")
    if parsed is None:
        return {"verdict": "fail", "reason": "judge returned unparseable output",
                "judge_raw": resp.choices[0].message.content}
    return parsed


def _verdict_from_rubric(case: dict, scores: dict) -> tuple[str, str]:
    """Apply the pass/fail thresholds from the spec (not just the judge's word)."""
    if "total" not in scores:
        return "fail", "missing total in judge output"
    total = scores["total"]
    if case["id"] == 1 and total < 10:
        return "fail", "total < 10"
    if case["id"] in (2, 3) and total < 14:
        return "fail", "total < 14"
    if case["id"] == 2 and scores.get("acknowledges_disagreement", 0) < 3:
        return "fail", "acknowledges_disagreement < 3 (critical)"
    if case["id"] == 3 and scores.get("correct_diagnosis", 0) < 4:
        return "fail", "correct_diagnosis < 4 (critical)"
    return "pass", ""


async def eval_case(case: dict) -> dict:
    print(f"\n{'='*70}\nTEST CASE {case['id']} — {case['name']}\n  mode={case['mode']} depth={case['depth']}")
    run = await run_case(case)
    if run["agent_error"]:
        print(f"  AGENT ERROR: {run['agent_error']}")
        return {**run, "scores": None, "verdict": "fail",
                "fail_reason": f"agent error: {run['agent_error']}"}
    print(f"  agent used {len(run['tool_trace'])} tool call(s)")
    for ev in run["tool_trace"]:
        if "tool" in ev:
            print(f"    - {ev['tool']}: {ev.get('args', {})}")
    if not run["output"].strip():
        return {**run, "scores": None, "verdict": "fail",
                "fail_reason": "agent returned empty output"}

    scores = await judge(case, run["output"])
    if "total" in scores:
        print(f"  judge: total={scores['total']} verdict={scores.get('verdict')}")
        print(f"         {scores.get('reason', '')}")
    verdict, fail_reason = _verdict_from_rubric(case, scores)
    if verdict == "fail" and not fail_reason:
        fail_reason = scores.get("reason", "")
    return {**run, "scores": scores, "verdict": verdict, "fail_reason": fail_reason}


async def main(only: list[int] | None, save_path: str | None):
    if not config.API_KEY:
        print("ERROR: set AIRA_API_KEY in .env (or environment) first.")
        sys.exit(2)
    cases = [c for c in TEST_CASES if not only or c["id"] in only]
    results = []
    for case in cases:
        results.append(await eval_case(case))

    print(f"\n{'='*70}\nSUMMARY")
    passed = 0
    for r in results:
        status = "PASS" if r["verdict"] == "pass" else "FAIL"
        total = r["scores"].get("total") if r.get("scores") else "-"
        reason = f" — {r['fail_reason']}" if r.get("fail_reason") else ""
        print(f"  Case {r['id']}: {status}  (total={total}){reason}")
        passed += r["verdict"] == "pass"
    print(f"\n{passed}/{len(results)} passed  |  model={config.MODEL}  judge={config.JUDGE_MODEL}")

    if save_path:
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"Full results saved to {save_path}")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=str, default=None, help="case ids, e.g. 1 or 2,3")
    ap.add_argument("--save", type=str, default=None, help="path to save full JSON results")
    args = ap.parse_args()
    only_ids = [int(x) for x in args.only.split(",")] if args.only else None
    asyncio.run(main(only_ids, args.save))
