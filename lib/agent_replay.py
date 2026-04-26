"""Replay a recorded demo question against the live MCP servers.

Given a saved trace file (from `python -m lib.agent --save-trace`), re-executes
the same tool calls in sequence against the current MCP servers (internal
graph + Tavily web) and renders each one with the same rich.live UI. The
LLM's final answer is replayed from the trace; the tool responses are NOT
replayed \u2014 they run live so what judges see is genuinely the current state
of the graph and the web.

This lets jurors run the demo without an OpenRouter key: no LLM calls happen,
only MCP tool calls, which cost either zero (internal) or $0 for the first
1000 Tavily searches on the free tier. If Tavily key is missing, web-tool
steps fall back to an explanatory error row but the final answer still renders.

Invoke:
    python -m lib.agent_replay data/demo_qa/q1_leave_policy.json
    python -m lib.agent_replay --all data/demo_qa/
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.live import Live
from rich.text import Text

from lib.agent import (
    AgentTrace,
    _mount_internal,
    _mount_tavily,
    _preview_result,
    _render,
)


async def _areplay(trace_path: Path, delay_per_turn: float = 0.8) -> int:
    raw = json.loads(trace_path.read_text())
    question = raw["question"]
    recorded_turns = raw.get("turns", [])
    final_answer = raw.get("final_answer") or "(no final answer recorded)"
    model = raw.get("model", "recorded")

    trace = AgentTrace(question=question, model=f"replay \u00b7 {model}")
    console = Console()

    needs_web = any(
        (t.get("tool") or "").startswith("tavily") or t.get("source") == "web"
        for t in recorded_turns
    )

    async with AsyncExitStack() as stack:
        internal_session, internal_tools = await _mount_internal(stack)
        session_by_tool = {t.name: internal_session for t in internal_tools}

        if needs_web:
            mounted = await _mount_tavily(stack)
            if mounted is not None:
                tavily_session, tavily_tools = mounted
                for t in tavily_tools:
                    session_by_tool[t.name] = tavily_session

        start = time.time()
        with Live(_render(question, trace, "Replaying..."), console=console, refresh_per_second=8) as live:
            for turn in recorded_turns:
                tool = turn.get("tool")
                args = turn.get("arguments") or {}
                if not tool:
                    continue
                source = "web" if tool.startswith("tavily") else "graph"
                live.update(_render(question, trace, f"Calling {tool} ({source}, live)..."))
                session = session_by_tool.get(tool)
                if session is None:
                    result_text = json.dumps({
                        "error": f"tool {tool} unavailable (missing MCP server \u2014 Tavily key?)"
                    })
                else:
                    try:
                        result = await session.call_tool(tool, args)
                        result_text = result.content[0].text if result.content else "{}"
                    except Exception as exc:
                        result_text = json.dumps({"error": str(exc)})

                trace.turns.append({
                    "tool": tool,
                    "source": source,
                    "arguments": args,
                    "result_preview": _preview_result(result_text),
                    "result_full": result_text,
                })
                live.update(_render(question, trace, ""))
                await asyncio.sleep(delay_per_turn)

            trace.final_answer = final_answer
            trace.elapsed_s = time.time() - start
            trace.total_tokens = raw.get("total_tokens", 0)
            trace.total_cost = raw.get("total_cost", 0.0)
            live.update(_render(question, trace, ""))

    console.print(Text(f"\n[replayed from {trace_path}]", style="dim"))
    return 0


def main() -> int:
    load_dotenv(".env")
    parser = argparse.ArgumentParser(description="Replay a recorded Qontextually agent trace")
    parser.add_argument("trace", help="Path to saved trace JSON, or directory when --all")
    parser.add_argument("--all", action="store_true", help="Treat argument as a directory and replay every *.json in sequence")
    parser.add_argument("--delay", type=float, default=0.8, help="Seconds between tool calls (for readability)")
    parser.add_argument("--speak", action="store_true", help="Speak each replayed final answer via Gradium TTS")
    args = parser.parse_args()

    speak_fn = None
    if args.speak:
        from lib.voice import speak as _speak
        speak_fn = _speak

    async def _maybe_speak(trace_path: Path) -> None:
        if not speak_fn:
            return
        try:
            with open(trace_path) as f:
                answer = json.load(f).get("final_answer") or ""
            if answer.strip():
                await speak_fn(answer)
        except Exception as exc:
            print(f"[TTS failed on {trace_path.name}: {type(exc).__name__}: {exc}]")

    async def _replay_and_speak(p: Path) -> int:
        rc = await _areplay(p, delay_per_turn=args.delay)
        await _maybe_speak(p)
        return rc

    path = Path(args.trace)
    if args.all:
        files = sorted(path.glob("*.json"))
        if not files:
            print(f"no traces found in {path}", file=sys.stderr)
            return 1
        for f in files:
            asyncio.run(_replay_and_speak(f))
        return 0

    if not path.exists():
        print(f"trace not found: {path}", file=sys.stderr)
        return 1
    return asyncio.run(_replay_and_speak(path))


if __name__ == "__main__":
    sys.exit(main())
