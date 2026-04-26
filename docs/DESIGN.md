# Qontextually — design notes

Deeper notes that would bloat the main README.

## Why cosine 0.95 (and not 0.85)

Embedding similarity is excellent at surface-form paraphrases — `works_at` ↔ `works_for` ↔ `employed_by` reliably cluster at cosine 0.95+, so those collapse automatically. It's measurably worse at **polarity and negation**. In our own run, `seeks_advice` and `offers_advice` — relations that point in opposite directions between the same two entity types — scored **cosine 0.932**, well above what a naive 0.85 threshold would auto-merge.

This isn't a quirk of our pipeline. Cao (2025) — "Semantic Adapter for Universal Text Embeddings: Diagnosing and Mitigating Negation Blindness to Enhance Universality," ECAI 2025 ([arXiv:2504.00584](https://arxiv.org/abs/2504.00584)) — evaluates the current generation of universal text embeddings (OpenAI's `text-embedding-3-*`, voyage-3, qwen3-embedding, BGE) and finds *"a significant lack of negation awareness in these models, often interpreting negated text pairs as semantically similar."* The mitigation we use: auto-merge at **cosine ≥ 0.95**, queue the 0.75–0.95 ambiguous band for human review via the `vocabulary_discovered` view.

Result on the tier-1 run: 4 predicates auto-merged with zero false positives, 461 ambiguous predicates (including `seeks_advice ~ offers_advice` at 0.932) surfaced to the reviewer with their cosine score attached.

## Why MCP (and why dual-server)

The agent needs two kinds of context: our graph (authoritative for internal facts) and the open web (authoritative for everything else). MCP gives a uniform tool interface. `lib/agent.py` mounts both servers inside one `AsyncExitStack`, keeps a `session_by_tool` registry, and tags each trace entry with `source: "graph"` or `source: "web"`. The LLM sees a flat tool surface; the audit trail preserves which source answered what. Claude Desktop users get the same dual-server arrangement via their MCP config — no code changes.

## Why replay

Jurors without an OpenRouter key still need to see the agent work. `lib.agent_replay` reads a saved trace and re-executes each tool call live against the current MCP servers, then renders the recorded final answer. Tool outputs are real; the LLM call is replayed at $0. If a judge wants to verify a citation, they can click through — the `triple_id` / `source_id` / `web:` URL all resolve against live data.

## Why a 3-tier cascading extractor

Mistral-Nemo is cheap, fast, and reliable on ~95% of chunks. Qwen3 catches another ~4% when Nemo's JSON mode flakes. Claude Haiku 4.5 in strict mode catches the last 1% with schema-patched requests (Anthropic's strict JSON schema doesn't accept `minimum`/`maximum`, so we strip those before sending). Every attempt lands in `audit_log` with model, cost, tokens, and error — so the demo can show exactly which model resolved which chunk.
