"""Benchmark candidate agent models on a fixed set of questions.

Same MCP tools, same prompt, same graph; only the model changes. Reports
empty-answer rate, answer length, tool-call count, latency, and cost per
model. Used to pick a reliable replacement when Qwen3-32B hits too many
empty answers.

Run: .venv/bin/python -m scripts.bench_agent_models
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from lib.agent import SYSTEM_PROMPT, _call_openrouter, _mcp_tool_to_openai


QUESTIONS = [
    "What does the Leave Policy say about vacation entitlements?",
    "What policies does Inazuma have? I need their names.",
    "Who is the Engineering director with Raj in their name?",
    "What is an IT Asset Management Policy?",
    "What is Abigail Mitchell's title?",
]

CANDIDATES = [
    "anthropic/claude-haiku-4.5",
    "openai/gpt-4o-mini",
    "deepseek/deepseek-v3.2",
    "qwen/qwen3-next-80b-a3b-instruct",
]

MAX_TURNS = 6


async def _run_one(session, openai_tools, model: str, question: str) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    start = time.time()
    tool_calls_count = 0
    total_tokens = 0
    total_cost = 0.0
    final_answer = None
    http_errors = []

    for _ in range(MAX_TURNS):
        try:
            resp = _call_openrouter(model, messages, openai_tools)
        except Exception as exc:
            http_errors.append(str(exc)[:120])
            break

        usage = resp.get("usage", {}) or {}
        total_tokens += usage.get("total_tokens") or 0
        total_cost += usage.get("cost") or 0.0

        msg = resp["choices"][0]["message"]
        tool_calls = msg.get("tool_calls") or []
        content = msg.get("content") or ""

        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        if not tool_calls:
            final_answer = content.strip()
            break

        for tc in tool_calls:
            tool_calls_count += 1
            fn = tc["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            try:
                result = await session.call_tool(fn["name"], args)
                result_text = result.content[0].text if result.content else "{}"
            except Exception as exc:
                result_text = json.dumps({"error": str(exc)})
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result_text[:8000]})

    elapsed = time.time() - start
    return {
        "model": model,
        "question": question,
        "answer_len": len(final_answer or ""),
        "tool_calls": tool_calls_count,
        "elapsed_s": round(elapsed, 2),
        "tokens": total_tokens,
        "cost": total_cost,
        "answered": bool(final_answer),
        "answer_preview": (final_answer or "")[:180],
        "errors": http_errors,
    }


async def _abench() -> None:
    params = StdioServerParameters(
        command=".venv/bin/python",
        args=["-m", "lib.mcp_server"],
        cwd=os.getcwd(),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_resp = await session.list_tools()
            openai_tools = [_mcp_tool_to_openai(t) for t in tools_resp.tools]

            results: list[dict] = []
            for model in CANDIDATES:
                print(f"\n=== {model} ===")
                for q in QUESTIONS:
                    r = await _run_one(session, openai_tools, model, q)
                    results.append(r)
                    status = "\u2705" if r["answered"] else "\u274c"
                    err = f" [err: {r['errors'][0]}]" if r["errors"] else ""
                    print(f"  {status}  {q[:55]:55s}  ans={r['answer_len']:4d}ch  tools={r['tool_calls']}  {r['elapsed_s']:5.1f}s  ${r['cost']:.4f}{err}")

            print("\n=== Summary ===")
            by_model = defaultdict(list)
            for r in results:
                by_model[r["model"]].append(r)

            for model, rs in by_model.items():
                answered = sum(1 for r in rs if r["answered"])
                avg_len = sum(r["answer_len"] for r in rs) / len(rs) if rs else 0
                total_cost = sum(r["cost"] for r in rs)
                avg_time = sum(r["elapsed_s"] for r in rs) / len(rs) if rs else 0
                errs = sum(1 for r in rs if r["errors"])
                print(f"  {model:50s} answered={answered}/{len(rs)}  avg_answer={avg_len:.0f}ch  avg_time={avg_time:.1f}s  total=${total_cost:.4f}  errors={errs}")

            out = Path("data/bench_agent_models.json")
            out.write_text(json.dumps(results, indent=2, default=str))
            print(f"\nSaved full results to {out}")


def main() -> int:
    load_dotenv(".env")
    asyncio.run(_abench())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
