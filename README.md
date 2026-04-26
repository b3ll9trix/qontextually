# Qontextually

**A context base that turns messy enterprise data into an inspectable, provenance-backed graph AI agents can actually use.**

Big Berlin Hack — [Qontext track](https://qontext.ai).

---

## The problem

Most enterprise AI systems reconstruct company reality *at runtime*: they ingest mail, CRM, policies, tickets, docs, chats — and hope the prompt is good enough. That doesn't scale, doesn't preserve provenance, and silently absorbs contradictions.

The Qontext track reframes the work: **start from the data, not the agent.** Build a company context base — a virtual file system plus a graph — that is legible to both machines and humans, self-updating, and conflict-aware.

## The approach

Qontextually ingests a simulated enterprise dataset (1,322 files across 10 domains — HR, email, CRM, policy PDFs, IT tickets, internal chat, and more) and turns every single source record into **entities, triples, and sources** stored in a single SQLite database.

Five things make the output useful to an agent, not just pretty to look at:

1. **Every triple is backed by one or more source rows** — delete the source, the fact either loses a backer or becomes orphaned. Provenance is a first-class column, not a hope.
2. **Vocabulary is a registry, not a constraint** — the extractor can coin new entity types and predicates when the seed vocabulary doesn't fit, and those land with `auto_added=1` for human review via a built-in `vocabulary_discovered` view.
3. **Entity resolution is layered** — Tier 1 exact-alias lookup handles the common "same person mentioned 200 times" case for free; Tier 2 embedding similarity (sqlite-vec, 1536-dim) handles name variants; ambiguous matches queue as conflicts for a human reviewer.
4. **Predicate resolution mirrors entity resolution** — auto-generated vocabulary doesn't stay a human bottleneck. Tier 1 normalizes case and whitespace (`has_POC_status` and `has_poc_status` collapse for free); Tier 2 embeds each predicate with usage context and KNN-merges at cosine ≥ 0.95; only the 0.75–0.95 ambiguous band reaches the human queue. See [the negation caveat](#why-cosine-095-and-not-085) for why the threshold is conservative.
5. **Conflict-aware by design** — functional predicates (e.g. `has_title`, `works_at`) trigger conflict detection on insert; resolution uses authority × confidence × recency scoring with a pending-human fallback.

## The result

After a single tier-1 extraction run on the sample dataset (extraction in progress at time of this draft; numbers are from the current mid-run snapshot):

- **7,697 entities** across Person, Organization, Project, Ticket, Policy, Product, and auto-discovered types
- **31,803 triples** averaging **1.58 sources per triple** — multi-source provenance working naturally; the most-backed fact has 702 sources
- **7,486 sources** with character-level `raw_text` preserved and FTS5 indexed
- **1,516 predicates** — 14 seeded, 4 auto-merged, the rest pending tier-2 resolution or human review
- **~$2.14 total LLM cost** at Mistral-Nemo's $0.01/1M input token pricing (23.8M tokens processed)
- **JSON / CSV / PDF** all handled via llama-index — new formats work out of the box

*(Final numbers update when the tier-1 run completes. The snapshot above reflects 7,489 of ~21,645 chunks processed.)*

---

## Architecture

<!-- ARCHITECTURE DIAGRAM GOES HERE -->

*(Architecture diagram coming from Reshma)*

### Components

**`lib/readers.py` — Format-extensible source loader.** Wraps llama-index's `SimpleDirectoryReader` with two custom extractors: `JSONRecordReader` splits each JSON array into per-record documents (so `emails.json` becomes 11,928 individually-provenanced sources instead of one 17MB blob), and `CSVRecordReader` preserves column semantics instead of string-concatenating fields. All other formats (PDF, docx, xlsx, HTML, images, audio) flow through llama-index's 19 built-in readers unchanged.

**`lib/prompts.py` — Extraction prompt.** GraphRAG-derived (MIT-attributed) with three enterprise-tuned few-shot examples: email, HR record, IT ticket. Emits JSON matching a Pydantic schema instead of GraphRAG's legacy tuple-delimiter format. ~1,500 tokens; $0.0002 per chunk on average.

**`lib/schemas.py` — LLM contract.** `Entity`, `Triple`, `ExtractionResult` in Pydantic. Entities use local refs (`ent_1`, `ent_2`) that the writer resolves to durable DB ids — handles homonyms and alias variants that plain name-matching can't. Triples use a discriminated union (`object_ref` XOR `object_value`) that maps to the DB's `object_is_entity` flag. Ten validators catch malformed LLM output before any DB write.

**`lib/extractor.py` — Three-tier cascading extractor.** Primary: `mistralai/mistral-nemo` (cheap, fast, reliable JSON). Fallback on parse failure: `qwen/qwen3-30b-a3b-instruct-2507`. Strict fallback: `anthropic/claude-haiku-4.5` (with provider-specific schema patching — `minimum`/`maximum` stripped because Anthropic's strict JSON schema doesn't support them). Every attempt is logged to `audit_log` with model, cost, tokens, and error, so the demo can show exactly which model resolved which chunk.

**`lib/writer.py` — Atomic graph write with Tier 1 resolution.** For each `ExtractionResult`: inserts one `sources` row, resolves each entity via `entity_aliases` (case-insensitive, type-scoped, multi-field), inserts new entities with DB-generated UUIDs or merges properties/aliases into the matched one, upserts triples with dedup that adds a `triple_sources` link when the same fact reappears. Normalizes predicate case at write-time (prevents `has_POC_status` and `has_poc_status` splitting) and coerces LLM-coined synonyms (`Employee`, `Staff`, `Customer` → `Person`; `Company`, `Team` → `Organization`).

**`lib/ingest.py` — Orchestrator.** Walks the dataset, classifies each file into tier 1 (HR, emails, tickets, policies, chats, client/vendor records) or tier 2 (product SKUs, internal Stack Overflow, GitHub samples), derives `source_type` from the folder name, runs extraction concurrently across configurable workers, skips chunks already in `audit_log` so interrupted runs resume cleanly.

**`lib/embeddings.py` — OpenRouter embeddings client.** Writes 1536-dim vectors to both `entity_embeddings` (SQL source-of-truth) and `entity_embeddings_vec` (sqlite-vec KNN index). Graceful fallback if sqlite-vec isn't loadable — the regular table stays valid, only KNN disables.

**`db/db.py` — Connection chokepoint.** `get_connection()` loads sqlite-vec at runtime and creates the vec0 virtual table idempotently. Every module goes through here, so enabling similarity search is one env var away.

**`lib/predicate_resolver.py` — Tier-2 vocabulary resolution.** Standalone post-ingest script. Embeds each auto-discovered predicate together with its description and a sample of real usage (`Raj Patel (Person) works_at Inazuma (Organization) | …`), writes to `predicate_embeddings` and the sqlite-vec mirror, then KNN-searches each candidate against the canonical set. Auto-merges into seeded predicates preferentially at cosine ≥ 0.95, falls back to auto-canonical targets, never flips (candidate only merges into predicates with equal-or-higher occurrence count). Rewrites all affected triples and records the decision in `predicate_merges` with its cosine confidence. Idempotent — safe to re-run as new predicates accumulate.

**`migrations/` — 11 versioned SQL files.** Built incrementally through the design process. Highlights: registries without foreign keys (`predicates`, `entity_types`, `source_types` are advisory), `triple_sources` for many-to-many evidence, `entity_aliases` with partial unique index on `is_primary`, `entity_merges` with `ON DELETE SET NULL` (merge history survives entity hard-deletes), FTS5 external-content index with porter+unicode61 tokenizer, a `vocabulary_discovered` view that filters already-merged predicates out of the human queue, and `predicate_merges` / `predicate_embeddings` for the tiered predicate-resolution audit trail.

---

## Why cosine 0.95 (and not 0.85)

A deliberate engineering choice with a caveat worth telling judges about.

Embedding similarity is excellent at surface-form paraphrases — `works_at` ↔ `works_for` ↔ `employed_by` reliably cluster at cosine 0.95+, so those collapse automatically. It's measurably worse at **polarity and negation**. In our own run, `seeks_advice` and `offers_advice` — relations that point in opposite directions between the same two entity types — scored **cosine 0.932**, well above what a naive 0.85 threshold would auto-merge.

This isn't a quirk of our pipeline. Cao (2025) — "Semantic Adapter for Universal Text Embeddings: Diagnosing and Mitigating Negation Blindness to Enhance Universality," ECAI 2025 ([arXiv:2504.00584](https://arxiv.org/abs/2504.00584)) — evaluates the current generation of universal text embeddings (OpenAI's `text-embedding-3-*`, voyage-3, qwen3-embedding, BGE) and finds *"a significant lack of negation awareness in these models, often interpreting negated text pairs as semantically similar."* Our own observation of `seeks_advice`/`offers_advice` at 0.932 cosine is the same class of failure: opposite *semantic direction* between predicates that co-occur in identical linguistic contexts ("mentorship", "help", "career development"), so the model has no training signal telling it they're not interchangeable.

The mitigation is simple and cheap: set the auto-merge threshold at **cosine ≥ 0.95**, which catches lexical variants reliably but excludes the 0.85–0.95 band where polarity-flipped pairs live. Anything in 0.75–0.95 queues for human review via `vocabulary_discovered`. The result: in our tier-1 run, 4 predicates auto-merged with zero false positives, and the 461 ambiguous predicates (including `seeks_advice ~ offers_advice` at 0.932) all surfaced to the reviewer with their cosine score attached. A future pass could adopt Cao's embedding-reweighting method, or add an NLI-model check for predicates that land in the ambiguous band — but that's out of scope for this weekend.

The broader point: **we're honest about where automation stops working and where the human starts.** Embeddings generalize; they don't understand negation. The schema and UI reflect that.

---

## Run it

### Prerequisites

- Python 3.12–3.14 (tested on 3.14)
- [`uv`](https://github.com/astral-sh/uv) (auto-installed by the Makefile if missing)
- `git-lfs` for the sample dataset (see below)
- Optional: an [OpenRouter](https://openrouter.ai) API key if you want to re-run extraction. The repo ships with a populated graph; you don't need a key to demo.

### Instant demo (no API key required)

```bash
git clone <repo-url>
cd qontextually
make setup     # installs uv + deps into .venv
make migrate   # applies 10 migrations into db/qontextually.db
make ui        # opens sqlite-web at localhost:8080 — browse the graph
```

The committed `db/qontextually.db` contains the full extracted graph from the sample dataset. Nothing else to install; no keys required.

### Rebuild from scratch (optional, ~$5, ~2 hours)

```bash
cp .env.example .env
# Edit .env, add your OPENROUTER_API_KEY
git clone https://huggingface.co/datasets/AST-FRI/EnterpriseBench sample_dataset
cd sample_dataset && git lfs pull && cd ..
.venv/bin/python -m lib.ingest --tier 1 --workers 8
.venv/bin/python -m lib.predicate_resolver  # merge synonyms, queue ambiguous for review
```

This re-extracts everything from source. Tier 1 (~21k chunks) covers HR, emails, tickets, policies, chats, clients, vendors. Tier 2 (`--tier 2`) adds product catalog, internal forum, and GitHub samples — optional and much larger. Run `predicate_resolver` any time after ingest to collapse vocabulary synonyms; it's idempotent.

### Live-drop demo (jury mode)

For the "drop a new file, watch the graph grow" demo without requiring an API key:

```bash
export QONTEXT_REPLAY_DIR=data/demo_drops
.venv/bin/python -m lib.ingest --tier 1 --max 3 --no-skip
```

When `QONTEXT_REPLAY_DIR` is set, the extractor loads pre-recorded `ExtractionResult` fixtures instead of calling OpenRouter. Demo files and their fixtures live in `data/demo_drops/`.

---

## What's in the dataset

| Domain | Files | Count (post-split) | Source type | Authority |
|---|---|---|---|---|
| `Human_Resource_Management/` | 1013 PDFs + 1 JSON + 1 CSV | 2,327 documents | `hr` | 1.00 |
| `Customer_Relation_Management/` | 273 PDFs + 5 JSONs | 4,475 documents | `crm` | 0.80 |
| `Policy_Documents/` | 26 PDFs | 169 pages | `policy` | 0.70 |
| `IT_Service_Management/` | 1 JSON | 163 tickets | `ticket` | 0.50 |
| `Enterprise_mail_system/` | 1 JSON | 11,928 emails | `email` | 0.40 |
| `Collaboration_tools/` | 1 JSON | 2,897 conversations | `chat` | 0.30 |
| `Enterprise Social Platform/` | 1 JSON | 971 posts | `chat` | 0.30 |
| `Business_and_Management/` | 2 JSONs | 800 records | `crm` | 0.80 |
| `Inazuma_Overflow/` | 1 JSON | 10,823 Q&A (tier 2) | `unknown` | 0.50 |
| `Workspace/` | 1 JSON | 750 samples (tier 2) | `unknown` | 0.50 |

Authority weights drive conflict resolution: when two sources disagree on `John's title`, HR (1.00) beats email (0.40) automatically. Ambiguous cases queue for a human.

---

## The 24-hour build log

Abridged: [schema-first `migrations/` (11 files)] → [llama-index spike + custom JSON/CSV readers] → [Pydantic extraction schema with validators] → [GraphRAG prompt adapted, MIT-attributed] → [OpenRouter key + model reliability tests: Qwen3 failed non-deterministically, Mistral-Nemo passed 9/9] → [3-tier cascading extractor with Anthropic schema-patching] → [writer with Tier 1 resolution + case-dedup + entity-type coercion] → [orchestrator with concurrency and resumability] → [tier-1 extraction run hit spending cap at ~4k chunks; cap lifted, resumed successfully] → [observed vocabulary sprawl: 1,000+ LLM-coined predicates; built tiered predicate resolver with embedding-based auto-merge at cosine ≥ 0.95] → [replay mode for jury demo without API keys].

Every design choice and its failures is captured in the git log.

---

## License

MIT. The extraction prompt in `lib/prompts.py` is adapted from [microsoft/graphrag](https://github.com/microsoft/graphrag) (also MIT, attributed in the module docstring).

## Author

Reshma Suresh — [github.com/b3ll9trix](https://github.com/b3ll9trix)
