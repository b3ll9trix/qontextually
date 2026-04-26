"""MCP server exposing Qontextually's context graph as tools.

Runs over stdio. Any MCP client (Claude Desktop, Claude Code, MCP Inspector,
custom agent) can connect and call the tools to read the graph.

This server is read-only. Humans do writes through the REST API + Lovable UI.
Agents that need to edit the graph should call the REST API directly \u2014 MCP
here is the retrieval surface, not the control plane.

Tools:
  search_context(query)          \u2014 FTS5 over sources + entity name match
  get_entity(name_or_id)         \u2014 full entity card with triples + aliases
  get_provenance(triple_id)      \u2014 sources backing a fact
  list_entities_by_type(type)    \u2014 directory-style browse
  get_source(source_id)          \u2014 raw chunk + what was extracted from it

Launch: python -m lib.mcp_server  (stdio server, meant to be spawned by a
client, not invoked directly by humans).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from db.db import get_connection

log = logging.getLogger("qontextually.mcp")

SOURCE_AUTHORITY = {
    "hr": 1.0, "crm": 0.8, "policy": 0.7, "ticket": 0.5,
    "email": 0.4, "chat": 0.3, "unknown": 0.5,
}


server: Server = Server("qontextually")


def _json_or_empty(s: str | None) -> dict:
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        return {}


def _tool_text(data: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, indent=2, ensure_ascii=False, default=str))]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_context",
            description=(
                "Search the context graph. Matches against source text (FTS5) "
                "and entity names. Returns ranked hits with entity/source context. "
                "Use this first for questions about named things."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language query or keyword"},
                    "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_entity",
            description=(
                "Fetch an entity's full record: properties, aliases, outgoing and "
                "incoming triples (each with a source_count). Accepts either the "
                "entity id (e_xxxxxxxxxxxx) or a name/alias (case-insensitive)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name_or_id": {"type": "string"},
                },
                "required": ["name_or_id"],
            },
        ),
        Tool(
            name="get_provenance",
            description=(
                "Given a triple id, return every source backing it \u2014 document path, "
                "source_type, authority, confidence, extracted_at, and the text "
                "snippet the fact was extracted from. Use this to cite a fact."
            ),
            inputSchema={
                "type": "object",
                "properties": {"triple_id": {"type": "integer"}},
                "required": ["triple_id"],
            },
        ),
        Tool(
            name="list_entities_by_type",
            description=(
                "Paginated directory-style listing. Useful when the question is "
                "about all things of a kind (all policies, all people in HR, etc.)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "Entity type (Person, Organization, Project, Ticket, Policy, Product, Meeting, Message, Event)"},
                    "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
                    "offset": {"type": "integer", "default": 0, "minimum": 0},
                },
                "required": ["type"],
            },
        ),
        Tool(
            name="get_source",
            description=(
                "Fetch a source record by id: raw_text, document_path, source_type, "
                "and everything extracted from it (entities + triples). Use when a "
                "fact's citation points at a source you want to examine directly."
            ),
            inputSchema={
                "type": "object",
                "properties": {"source_id": {"type": "integer"}},
                "required": ["source_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "search_context":
            return _tool_text(_search_context(arguments["query"], arguments.get("limit", 10)))
        if name == "get_entity":
            return _tool_text(_get_entity(arguments["name_or_id"]))
        if name == "get_provenance":
            return _tool_text(_get_provenance(int(arguments["triple_id"])))
        if name == "list_entities_by_type":
            return _tool_text(_list_entities_by_type(
                arguments["type"],
                arguments.get("limit", 20),
                arguments.get("offset", 0),
            ))
        if name == "get_source":
            return _tool_text(_get_source(int(arguments["source_id"])))
        return _tool_text({"error": f"unknown tool: {name}"})
    except Exception as exc:
        log.exception("tool %s failed", name)
        return _tool_text({"error": str(exc), "tool": name, "arguments": arguments})


def _search_context(query: str, limit: int = 10) -> dict:
    conn = get_connection()
    try:
        hits: list[dict] = []
        fts_query = " ".join(query.strip().split()) or "*"
        try:
            for r in conn.execute(
                """
                SELECT s.id, s.document_path, s.source_type, snippet(source_fts, 0, '[', ']', '\u2026', 16) AS snip
                FROM source_fts f JOIN sources s ON s.id = f.rowid
                WHERE source_fts MATCH ? ORDER BY rank LIMIT ?
                """,
                (fts_query, limit),
            ):
                hits.append(
                    {
                        "kind": "source",
                        "source_id": r["id"],
                        "document_path": r["document_path"],
                        "source_type": r["source_type"],
                        "authority": SOURCE_AUTHORITY.get(r["source_type"], 0.5),
                        "snippet": r["snip"],
                    }
                )
        except Exception:
            pass

        needle = f"%{query.lower()}%"
        for r in conn.execute(
            """
            SELECT e.id, e.type, e.name, e.properties_json
            FROM entities e
            WHERE e.status = 'active'
              AND (lower(e.name) LIKE ? OR e.id IN (
                    SELECT entity_id FROM entity_aliases WHERE lower(alias) LIKE ?
                  ))
            LIMIT ?
            """,
            (needle, needle, limit),
        ):
            hits.append(
                {
                    "kind": "entity",
                    "id": r["id"],
                    "type": r["type"],
                    "name": r["name"],
                    "properties": _json_or_empty(r["properties_json"]),
                }
            )

        return {"query": query, "total_hits": len(hits), "hits": hits}
    finally:
        conn.close()


def _resolve_entity_id(conn, name_or_id: str) -> str | None:
    if name_or_id.startswith("e_"):
        row = conn.execute("SELECT id FROM entities WHERE id = ?", (name_or_id,)).fetchone()
        return row["id"] if row else None
    row = conn.execute(
        "SELECT id FROM entities WHERE lower(name) = ? AND status='active' LIMIT 1",
        (name_or_id.strip().lower(),),
    ).fetchone()
    if row:
        return row["id"]
    row = conn.execute(
        """
        SELECT entity_id FROM entity_aliases
        WHERE lower(alias) = ?
        ORDER BY is_primary DESC LIMIT 1
        """,
        (name_or_id.strip().lower(),),
    ).fetchone()
    return row["entity_id"] if row else None


def _get_entity(name_or_id: str) -> dict:
    conn = get_connection()
    try:
        entity_id = _resolve_entity_id(conn, name_or_id)
        if entity_id is None:
            return {"error": f"no entity matches {name_or_id!r}"}

        row = conn.execute(
            "SELECT id, type, name, properties_json FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()

        aliases = [
            {"alias": a["alias"], "alias_type": a["alias_type"], "is_primary": bool(a["is_primary"])}
            for a in conn.execute(
                "SELECT alias, alias_type, is_primary FROM entity_aliases WHERE entity_id = ? ORDER BY is_primary DESC",
                (entity_id,),
            )
        ]

        outgoing = []
        for t in conn.execute(
            """
            SELECT t.id, t.predicate, t.object_is_entity, t.object_id, t.object_value, t.status,
                   oe.name AS obj_name,
                   (SELECT COUNT(*) FROM triple_sources ts WHERE ts.triple_id = t.id) AS src_count
            FROM triples t LEFT JOIN entities oe ON oe.id = t.object_id
            WHERE t.subject_id = ? AND t.status = 'active'
            ORDER BY src_count DESC LIMIT 50
            """,
            (entity_id,),
        ):
            outgoing.append(
                {
                    "triple_id": t["id"],
                    "predicate": t["predicate"],
                    "object": t["obj_name"] if t["object_is_entity"] else t["object_value"],
                    "object_is_entity": bool(t["object_is_entity"]),
                    "object_id": t["object_id"],
                    "source_count": t["src_count"],
                }
            )

        incoming = []
        for t in conn.execute(
            """
            SELECT t.id, t.subject_id, se.name AS subj_name, t.predicate
            FROM triples t JOIN entities se ON se.id = t.subject_id
            WHERE t.object_id = ? AND t.object_is_entity = 1 AND t.status = 'active'
            LIMIT 50
            """,
            (entity_id,),
        ):
            incoming.append(
                {
                    "triple_id": t["id"],
                    "subject": t["subj_name"],
                    "subject_id": t["subject_id"],
                    "predicate": t["predicate"],
                }
            )

        return {
            "id": row["id"],
            "type": row["type"],
            "name": row["name"],
            "properties": _json_or_empty(row["properties_json"]),
            "aliases": aliases,
            "outgoing_triples": outgoing,
            "incoming_triples": incoming,
        }
    finally:
        conn.close()


def _get_provenance(triple_id: int) -> dict:
    conn = get_connection()
    try:
        t = conn.execute(
            """
            SELECT t.id, t.predicate, t.object_value, t.object_is_entity,
                   se.id AS subj_id, se.name AS subj_name, se.type AS subj_type,
                   oe.id AS obj_id, oe.name AS obj_name, oe.type AS obj_type
            FROM triples t
            JOIN entities se ON se.id = t.subject_id
            LEFT JOIN entities oe ON oe.id = t.object_id
            WHERE t.id = ?
            """,
            (triple_id,),
        ).fetchone()
        if t is None:
            return {"error": f"triple {triple_id} not found"}

        sources = []
        for r in conn.execute(
            """
            SELECT s.id, s.document_path, s.source_type, s.extracted_at,
                   substr(s.raw_text, 1, 500) AS snippet, ts.confidence
            FROM triple_sources ts JOIN sources s ON s.id = ts.source_id
            WHERE ts.triple_id = ? ORDER BY ts.extracted_at ASC
            """,
            (triple_id,),
        ):
            sources.append(
                {
                    "source_id": r["id"],
                    "document_path": r["document_path"],
                    "source_type": r["source_type"],
                    "authority": SOURCE_AUTHORITY.get(r["source_type"], 0.5),
                    "confidence": r["confidence"],
                    "extracted_at": r["extracted_at"],
                    "snippet": r["snippet"],
                }
            )

        return {
            "triple_id": t["id"],
            "subject": {"id": t["subj_id"], "name": t["subj_name"], "type": t["subj_type"]},
            "predicate": t["predicate"],
            "object": (
                {"id": t["obj_id"], "name": t["obj_name"], "type": t["obj_type"]}
                if t["object_is_entity"]
                else None
            ),
            "object_value": t["object_value"],
            "object_is_entity": bool(t["object_is_entity"]),
            "sources": sources,
        }
    finally:
        conn.close()


def _list_entities_by_type(type_: str, limit: int = 20, offset: int = 0) -> dict:
    conn = get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE type = ? AND status='active'", (type_,)
        ).fetchone()[0]
        items = []
        for r in conn.execute(
            """
            SELECT id, name, properties_json FROM entities
            WHERE type = ? AND status='active'
            ORDER BY name LIMIT ? OFFSET ?
            """,
            (type_, limit, offset),
        ):
            items.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "properties": _json_or_empty(r["properties_json"]),
                }
            )
        return {"type": type_, "total": total, "items": items}
    finally:
        conn.close()


def _get_source(source_id: int) -> dict:
    conn = get_connection()
    try:
        r = conn.execute(
            "SELECT id, document_path, source_type, extracted_at, raw_text, properties_json FROM sources WHERE id = ?",
            (source_id,),
        ).fetchone()
        if r is None:
            return {"error": f"source {source_id} not found"}

        triples = []
        for t in conn.execute(
            """
            SELECT t.id, t.predicate, t.object_is_entity, t.object_value, se.name AS subj_name, oe.name AS obj_name
            FROM triple_sources ts JOIN triples t ON t.id = ts.triple_id
            JOIN entities se ON se.id = t.subject_id
            LEFT JOIN entities oe ON oe.id = t.object_id
            WHERE ts.source_id = ?
            """,
            (source_id,),
        ):
            triples.append(
                {
                    "triple_id": t["id"],
                    "subject": t["subj_name"],
                    "predicate": t["predicate"],
                    "object": t["obj_name"] if t["object_is_entity"] else t["object_value"],
                }
            )

        return {
            "source_id": r["id"],
            "document_path": r["document_path"],
            "source_type": r["source_type"],
            "authority": SOURCE_AUTHORITY.get(r["source_type"], 0.5),
            "extracted_at": r["extracted_at"],
            "properties": _json_or_empty(r["properties_json"]),
            "raw_text": r["raw_text"],
            "contributed_triples": triples,
        }
    finally:
        conn.close()


async def _amain() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(_amain())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
