# Qontextually — design notes

Deeper notes that would bloat the main README.

## Why cosine 0.90 (and not 0.85 or 0.95)

Embedding similarity is excellent at surface-form paraphrases — `works_at` ↔ `works_for` ↔ `employed_by` reliably cluster at cosine 0.90+, so those collapse automatically. It's measurably worse at **polarity and negation**. In our own run, `seeks_advice` and `offers_advice` — relations that point in opposite directions between the same two entity types — scored **cosine 0.932**, well above what a naive 0.85 threshold would auto-merge.

This isn't a quirk of our pipeline. Cao (2025) — "Semantic Adapter for Universal Text Embeddings: Diagnosing and Mitigating Negation Blindness to Enhance Universality," ECAI 2025 ([arXiv:2504.00584](https://arxiv.org/abs/2504.00584)) — evaluates the current generation of universal text embeddings (OpenAI's `text-embedding-3-*`, voyage-3, qwen3-embedding, BGE) and finds *"a significant lack of negation awareness in these models, often interpreting negated text pairs as semantically similar."*

We pick **cosine ≥ 0.90** as a deliberate trade-off. 0.85 is too permissive — it sweeps in a wide band of polarity flips and other near-duplicates. 0.95 is so conservative that only a handful of obvious lexical variants ever auto-merge, and most of the predicate sprawl stays unresolved. 0.90 catches a much wider class of true paraphrases (`mentors` ≈ `provides_mentorship`, `is_assigned_to` ≈ `assigned_to`) at the price of accepting some polarity-adjacent risk near the boundary — including `seeks_advice ~ offers_advice` at 0.932, which will now auto-merge unless explicitly seeded apart.

The audit surface is the merge log itself: every auto-merge writes (`from`, `into`, cosine, l2) to `predicate_merges`, ordered by cosine in the resolver's pretty-print. A reviewer can spot-check the high-cosine entries, and the system supports reverse-merges via the same builder code path. The 0.75–0.90 band still queues for human review through the `vocabulary_discovered` view — these are the cases where automation *and* the embedding model are both unreliable. A future pass could plug in an NLI-model check for predicates near the boundary; for this build the answer is operational, not architectural.

## Why MCP (and why dual-server)

The agent needs two kinds of context: our graph (authoritative for internal facts) and the open web (authoritative for everything else). MCP gives a uniform tool interface. `lib/agent.py` mounts both servers inside one `AsyncExitStack`, keeps a `session_by_tool` registry, and tags each trace entry with `source: "graph"` or `source: "web"`. The LLM sees a flat tool surface; the audit trail preserves which source answered what. Claude Desktop users get the same dual-server arrangement via their MCP config — no code changes.

## Why replay

Jurors without an OpenRouter key still need to see the agent work. `lib.agent_replay` reads a saved trace and re-executes each tool call live against the current MCP servers, then renders the recorded final answer. Tool outputs are real; the LLM call is replayed at $0. If a judge wants to verify a citation, they can click through — the `triple_id` / `source_id` / `web:` URL all resolve against live data.

## Why a 3-tier cascading extractor

Mistral-Nemo is cheap, fast, and reliable on ~95% of chunks. Qwen3 catches another ~4% when Nemo's JSON mode flakes. Claude Haiku 4.5 in strict mode catches the last 1% with schema-patched requests (Anthropic's strict JSON schema doesn't accept `minimum`/`maximum`, so we strip those before sending). Every attempt lands in `audit_log` with model, cost, tokens, and error — so the demo can show exactly which model resolved which chunk.
