"""Terminal agent that answers questions across multiple MCP servers.

Connects to two MCP servers simultaneously and merges their tools:
  - Qontextually (stdio) \u2014 internal graph: search_context, get_entity, get_provenance, list_entities_by_type, get_source
  - Tavily (streamable HTTP) \u2014 live web: tavily_search, tavily_extract, tavily_crawl, tavily_map, tavily_research

OpenRouter drives the LLM (qwen/qwen3-next-80b-a3b-instruct by default).
rich.live renders tool calls, results, and citations in real time. Each
tool call is routed to the MCP server that owns it via a name\u2192session map.

Invoke as:
    python -m lib.agent "Who manages the Phoenix project?"
    python -m lib.agent --no-web "Internal-only question"

The agent loops: LLM picks a tool \u2192 we dispatch to the right MCP server \u2192
result goes back to LLM \u2192 repeat until LLM emits a final answer. Citations
in the final answer point to triple_ids + source_ids that exist in the DB
or (for web tools) to the returned URLs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Optional

import requests
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from rich.box import ROUNDED
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text


OPENROUTER_URL = os.environ.get(
    "QONTEXT_EXTRACT_URL", "https://openrouter.ai/api/v1/chat/completions"
)
API_KEY_ENV = "OPENROUTER_API_KEY"


def _agent_model() -> str:
    return os.environ.get("QONTEXT_AGENT_MODEL", "qwen/qwen3-next-80b-a3b-instruct")


def _agent_model_fallback() -> str:
    return os.environ.get("QONTEXT_AGENT_MODEL_FALLBACK", "openai/gpt-4o-mini")
MAX_TURNS = 8
TEMPERATURE = 0.1
MAX_TOKENS = 2048


SYSTEM_PROMPT = """You answer questions about a company called Inazuma.co using a knowledge graph plus live web search when needed.

You have TWO classes of tools:

INTERNAL GRAPH tools (start with search_context / get_entity / get_provenance / list_entities_by_type / get_source):
  - Use these FIRST for anything about Inazuma: people, projects, policies, emails, tickets, org structure.
  - Every fact here has a triple_id and source_id you can cite.

EXTERNAL WEB tools (start with tavily_): tavily_search, tavily_extract, tavily_crawl, tavily_map, tavily_research
  - Use ONLY for facts the internal graph cannot know: current laws, industry standards, external benchmarks, definitions of general terms, market prices.
  - Never use web tools for Inazuma-internal facts; the internal graph is authoritative there.

Decision procedure:
1. If the question mentions an Inazuma entity (a name, policy, project): start with search_context.
2. If search_context returns relevant hits, expand with get_entity and cite with get_provenance.
3. If the question needs external context (e.g. "does our policy match German law?"), call tavily_search AFTER you have gathered the internal side.
4. Stop as soon as you can answer. Do not call tools you do not need.

Final answer format (plain text):
- Direct, 1-3 sentences.
- A "Citations:" section listing each fact:
    - Internal: "- <fact> (triple #N, source #M)"
    - Web:      "- <fact> (web: <url>)"
- If both sources contributed, cite both.

If the graph has nothing and the question is about Inazuma, say "Inazuma's graph has no record of that" and stop. Do not invent. Do not fall back to the web for internal Inazuma questions.
"""


@dataclass
class AgentTrace:
    question: str
    model: str
    turns: list[dict] = field(default_factory=list)
    final_answer: Optional[str] = None
    elapsed_s: float = 0.0
    total_tokens: int = 0
    total_cost: float = 0.0

    def as_dict(self) -> dict:
        return {
            "question": self.question,
            "model": self.model,
            "turns": self.turns,
            "final_answer": self.final_answer,
            "elapsed_s": self.elapsed_s,
            "total_tokens": self.total_tokens,
            "total_cost": self.total_cost,
        }


def _mcp_tool_to_openai(tool: Any) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.inputSchema,
        },
    }


def _truncate(value: Any, limit: int = 300) -> str:
    s = str(value)
    return s if len(s) <= limit else s[:limit] + "\u2026"


def _call_openrouter(model: str, messages: list[dict], tools: list[dict]) -> dict:
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise RuntimeError(f"{API_KEY_ENV} not set")
    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/b3ll9trix/qontextually",
            "X-Title": "Qontextually Agent",
        },
        json={
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
        },
        timeout=90,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _render(question: str, trace: AgentTrace, status: str) -> Panel:
    grp: list[Any] = []
    grp.append(Panel(Text(question, style="bold cyan"), title="Question", border_style="cyan", box=ROUNDED))

    if trace.turns:
        tbl = Table(box=ROUNDED, expand=True, show_header=True, header_style="bold")
        tbl.add_column("#", width=3)
        tbl.add_column("Tool", style="magenta")
        tbl.add_column("Arguments", style="yellow")
        tbl.add_column("Result preview", style="green")
        for i, turn in enumerate(trace.turns, 1):
            args = turn.get("arguments") or {}
            args_s = ", ".join(f"{k}={_truncate(v, 40)}" for k, v in args.items())
            result_s = _truncate(turn.get("result_preview") or "", 90)
            tbl.add_row(str(i), turn.get("tool", "?"), args_s, result_s)
        grp.append(Panel(tbl, title="Tool calls", border_style="magenta", box=ROUNDED))

    if trace.final_answer:
        grp.append(
            Panel(
                Text(trace.final_answer, style="white"),
                title=f"Answer  \u00b7  {trace.model}  \u00b7  {trace.elapsed_s:.1f}s  \u00b7  ${trace.total_cost:.4f}",
                border_style="green",
                box=ROUNDED,
            )
        )
    else:
        grp.append(Panel(Spinner("dots", text=status), border_style="dim"))

    return Panel(Group(*grp), border_style="bright_black", box=ROUNDED)


async def _mount_internal(stack: AsyncExitStack) -> tuple[ClientSession, list[Any]]:
    """Spawn lib.mcp_server via stdio, initialize a session, return (session, tools)."""
    params = StdioServerParameters(
        command=".venv/bin/python",
        args=["-m", "lib.mcp_server"],
        cwd=os.getcwd(),
    )
    read, write = await stack.enter_async_context(stdio_client(params))
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    tools = (await session.list_tools()).tools
    return session, tools


async def _mount_tavily(stack: AsyncExitStack) -> Optional[tuple[ClientSession, list[Any]]]:
    """Connect to Tavily's remote MCP via streamable HTTP. Returns None if no key."""
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return None
    url = f"https://mcp.tavily.com/mcp/?tavilyApiKey={key}"
    try:
        streams = await stack.enter_async_context(streamablehttp_client(url))
        read, write = streams[0], streams[1]
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        tools = (await session.list_tools()).tools
        return session, tools
    except Exception as exc:
        return None


def _preview_result(result_text: str) -> str:
    try:
        obj = json.loads(result_text)
    except Exception:
        return _truncate(result_text, 90)
    if isinstance(obj, dict):
        if "error" in obj:
            return obj["error"]
        if "total_hits" in obj:
            return f"{obj['total_hits']} hits"
        if "results" in obj and isinstance(obj["results"], list):
            return f"{len(obj['results'])} web results"
        if "name" in obj:
            return f"{obj['name']} ({obj.get('type','?')})"
        if "total" in obj:
            return f"{obj['total']} items"
    return _truncate(obj, 90)


async def _arun(question: str, verbose: bool = False, use_web: bool = True) -> AgentTrace:
    trace = AgentTrace(question=question, model=_agent_model())
    console = Console()

    async with AsyncExitStack() as stack:
        internal_session, internal_tools = await _mount_internal(stack)

        session_by_tool: dict[str, ClientSession] = {t.name: internal_session for t in internal_tools}
        openai_tools = [_mcp_tool_to_openai(t) for t in internal_tools]

        tavily_ok = False
        if use_web:
            mounted = await _mount_tavily(stack)
            if mounted is not None:
                tavily_session, tavily_tools = mounted
                for t in tavily_tools:
                    session_by_tool[t.name] = tavily_session
                    openai_tools.append(_mcp_tool_to_openai(t))
                tavily_ok = True

        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        start = time.time()
        primary = _agent_model()
        fallback = _agent_model_fallback()
        model = primary

        boot_status = f"Consulting {model}" + (" (internal + web)" if tavily_ok else " (internal only)")

        with Live(_render(question, trace, boot_status + "\u2026"), console=console, refresh_per_second=8) as live:
            for turn_ix in range(MAX_TURNS):
                try:
                    resp = _call_openrouter(model, messages, openai_tools)
                except Exception as exc:
                    if model == primary:
                        model = fallback
                        trace.model = model
                        live.update(_render(question, trace, f"Primary failed, falling back to {model}\u2026"))
                        continue
                    trace.final_answer = f"Agent failed: {exc}"
                    live.update(_render(question, trace, ""))
                    break

                usage = resp.get("usage", {}) or {}
                trace.total_tokens += usage.get("total_tokens") or 0
                trace.total_cost += usage.get("cost") or 0.0

                msg = resp["choices"][0]["message"]
                tool_calls = msg.get("tool_calls") or []
                content = msg.get("content") or ""

                messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

                if not tool_calls:
                    trace.final_answer = content.strip() or "(empty answer)"
                    trace.elapsed_s = time.time() - start
                    live.update(_render(question, trace, ""))
                    break

                for tc in tool_calls:
                    fn = tc["function"]
                    tool_name = fn["name"]
                    try:
                        arguments = json.loads(fn.get("arguments") or "{}")
                    except Exception:
                        arguments = {}

                    source = "web" if tool_name.startswith("tavily") else "graph"
                    live.update(_render(question, trace, f"Calling {tool_name} ({source})\u2026"))

                    session = session_by_tool.get(tool_name)
                    if session is None:
                        result_text = json.dumps({"error": f"unknown tool {tool_name}"})
                    else:
                        try:
                            result = await session.call_tool(tool_name, arguments)
                            result_text = result.content[0].text if result.content else "{}"
                        except Exception as exc:
                            result_text = json.dumps({"error": str(exc)})

                    trace.turns.append({
                        "tool": tool_name,
                        "source": source,
                        "arguments": arguments,
                        "result_preview": _preview_result(result_text),
                        "result_full": result_text,
                    })
                    live.update(_render(question, trace, ""))

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_text[:8000],
                    })
            else:
                trace.final_answer = "(reached max turns without final answer)"
                trace.elapsed_s = time.time() - start

    if verbose:
        console.print(Panel(Text(json.dumps(trace.as_dict(), indent=2, default=str)[:4000], style="dim"), title="Trace", border_style="dim"))

    return trace


def main() -> int:
    load_dotenv(".env")
    parser = argparse.ArgumentParser(description="Qontextually Q&A agent")
    parser.add_argument("question", help="Question to ask the graph")
    parser.add_argument("--verbose", action="store_true", help="Print full trace JSON")
    parser.add_argument("--save-trace", help="Save full trace to a JSON file")
    parser.add_argument("--no-web", action="store_true", help="Skip Tavily MCP (internal graph only)")
    parser.add_argument("--speak", action="store_true", help="Speak the final answer aloud via Gradium TTS")
    args = parser.parse_args()

    trace = asyncio.run(_arun(args.question, verbose=args.verbose, use_web=not args.no_web))

    if args.save_trace:
        with open(args.save_trace, "w") as f:
            json.dump(trace.as_dict(), f, indent=2, default=str)
        print(f"\n[saved trace to {args.save_trace}]")

    if args.speak and trace.final_answer:
        from lib.voice import speak
        try:
            asyncio.run(speak(trace.final_answer))
        except Exception as exc:
            print(f"[TTS failed: {type(exc).__name__}: {exc}]")

    return 0 if trace.final_answer else 1


if __name__ == "__main__":
    sys.exit(main())
