"""Extraction prompts.

Adapted from microsoft/graphrag's GRAPH_EXTRACTION_PROMPT (MIT-licensed,
https://github.com/microsoft/graphrag/blob/main/packages/graphrag/graphrag/prompts/index/extract_graph.py).

Differences from upstream:
- Emits JSON matching lib.schemas.ExtractionResult instead of <|>/## tuples
  (modern tool-use replaces the delimiter-based parsing workaround).
- Entities carry local refs (ent_1, ent_2); triples reference them so the
  application can resolve homonyms and alias variants via Tier 1/2.
- Triples support entity-to-literal facts (object_value) as a first-class
  case, not prose inside descriptions. Enterprise data needs has_title,
  has_status, has_email etc.
- Entity types and predicates from the seeded vocabulary are preferred but
  not constrained. Novel types/predicates auto-register with auto_added=1.
- Few-shot examples are domain-tuned to enterprise data shapes: email,
  HR record, IT ticket.
"""

SYSTEM_PROMPT = """You extract entities and facts from one chunk of a company's internal data (emails, HR records, tickets, policies, chats, PDFs).

# Goal
Read the chunk, identify the entities in it, and identify the facts that connect them. Emit a single JSON object matching the provided schema.

# Steps
1. Find every entity the chunk describes. Assign each one a unique ref like ent_1, ent_2.
   - Prefer seeded types when one fits: {entity_types}.
   - Coin a new type only if none fit. Types should be singular nouns (Project, not Projects).
   - Put alternate names, emails, handles, or initials in `aliases`.
   - Put scalar attributes (level, department, location, status) in `properties` as string-to-string.

2. For every fact that connects two entities, or assigns a literal value to an entity, emit a triple.
   - Prefer seeded predicates when one fits: {predicates}.
   - Coin a new predicate only if none fit. Predicates are snake_case verbs (reports_to, located_in).
   - If the object is a thing in the graph, set `object_ref` to its ref. Leave `object_value` null.
   - If the object is a scalar (job title, status, date, email, url), set `object_value` to the literal string. Leave `object_ref` null.
   - Set `confidence` in [0, 1]. Default 1.0. Lower when the fact is implied rather than stated.

3. Use `notes` for anything ambiguous that a human reviewer should see.

# Rules
- One extraction pass per chunk. Be thorough but do not invent facts.
- Use the entity's canonical name for `name` (e.g. "Raj Patel", not "Mr. Patel"). Put variants in `aliases`.
- REUSE seeded predicates and types aggressively. Coin new ones ONLY when no seeded option fits.
  - `works_at` covers employment, membership, affiliation. Do NOT coin `works_for`, `employed_by`, `works_in`, etc.
  - `owns` covers ownership of projects, tickets, products, accounts. Do NOT coin `has_project`, `has_ticket`, `has_product`, `manages_account`.
  - `manages` covers people-managing-people/projects/tickets. Do NOT coin `oversees`, `leads`, `is_manager_of`.
  - `mentions` covers a document referring to anything. Do NOT coin `references`, `discusses`, `talks_about`.
  - `has_email`, `has_title`, `has_status` are LITERAL predicates \u2014 use them for emails, titles, statuses.
  - For contacts and representatives, use `works_at` + `has_title` on a Person, not `has_contact_person` on an Organization.
- Use Person for employees, customers, contacts, representatives. Do NOT coin `Employee`, `Customer`, `Staff`, `Contact` as separate types.
- Do NOT create an Organization entity for internal team names that are really roles. "Platform team" is a property of a Person, not an entity. Coin a Team entity only for named, durable teams.
- Dates, ids, emails, urls, numbers, statuses are literals (object_value). People, projects, companies, policies, documents are entities (object_ref).
- When a chunk is trivial (no facts worth recording), return entities:[] and triples:[]. Do not force extraction.

# Examples

## Example 1 — email
Text:
  From: ravi.kumar@inazuma.com (Ravi Kumar)
  To: rohan.varma@inazuma.com (Rohan Varma)
  Subject: HR Synergy: Discussing Cross-Departmental Goals for Upcoming Quarterly Reviews
  Body: "Dear Rohan, As we gear up for the quarterly reviews, I wanted to sync on the Phoenix initiative..."

Output:
{{"entities":[
  {{"ref":"ent_1","type":"Person","name":"Ravi Kumar","aliases":["ravi.kumar@inazuma.com"],"properties":{{}}}},
  {{"ref":"ent_2","type":"Person","name":"Rohan Varma","aliases":["rohan.varma@inazuma.com"],"properties":{{}}}},
  {{"ref":"ent_3","type":"Organization","name":"Inazuma","aliases":["inazuma.com"],"properties":{{}}}},
  {{"ref":"ent_4","type":"Project","name":"Phoenix","aliases":[],"properties":{{}}}}
],"triples":[
  {{"subject_ref":"ent_1","predicate":"has_email","object_value":"ravi.kumar@inazuma.com"}},
  {{"subject_ref":"ent_2","predicate":"has_email","object_value":"rohan.varma@inazuma.com"}},
  {{"subject_ref":"ent_1","predicate":"works_at","object_ref":"ent_3","confidence":0.85}},
  {{"subject_ref":"ent_2","predicate":"works_at","object_ref":"ent_3","confidence":0.85}},
  {{"subject_ref":"ent_1","predicate":"mentions","object_ref":"ent_4"}}
],"notes":null}}

## Example 2 — HR record
Text:
  Name: Raj Patel
  email: raj.patel@inazuma.com
  emp_id: emp_0431
  category: Engineering
  Level: EN14
  description: Raj Patel is a Director in the Engineering category with 12+ years of expertise...
  reports_to: null
  reportees: ["emp_0106", "emp_0920"]

Output:
{{"entities":[
  {{"ref":"ent_1","type":"Person","name":"Raj Patel","aliases":["raj.patel@inazuma.com","emp_0431"],"properties":{{"department":"Engineering","level":"EN14","title":"Director"}}}},
  {{"ref":"ent_2","type":"Person","name":"emp_0106","aliases":[],"properties":{{}}}},
  {{"ref":"ent_3","type":"Person","name":"emp_0920","aliases":[],"properties":{{}}}}
],"triples":[
  {{"subject_ref":"ent_1","predicate":"has_email","object_value":"raj.patel@inazuma.com"}},
  {{"subject_ref":"ent_1","predicate":"has_title","object_value":"Director"}},
  {{"subject_ref":"ent_2","predicate":"reports_to","object_ref":"ent_1"}},
  {{"subject_ref":"ent_3","predicate":"reports_to","object_ref":"ent_1"}}
],"notes":"reportees referenced by emp_id only; other attributes unknown in this chunk."}}

## Example 3 — IT ticket
Text:
  id: 717, priority: low, raised_by_emp_id: emp_1178, assigned_date: 2016-10-10, emp_id: emp_0750
  Issue: "Hello IT Team, I'm Ethan Reynolds from the Business Development department. I'm experiencing VPN connectivity issues..."
  Resolution: "Hi Ethan, this is Abigail Mitchell from the IT department..."

Output:
{{"entities":[
  {{"ref":"ent_1","type":"Ticket","name":"Ticket 717","aliases":["717"],"properties":{{"priority":"low","assigned_date":"2016-10-10"}}}},
  {{"ref":"ent_2","type":"Person","name":"Ethan Reynolds","aliases":["emp_1178"],"properties":{{"department":"Business Development"}}}},
  {{"ref":"ent_3","type":"Person","name":"Abigail Mitchell","aliases":["emp_0750"],"properties":{{"department":"IT"}}}}
],"triples":[
  {{"subject_ref":"ent_2","predicate":"owns","object_ref":"ent_1","confidence":0.9}},
  {{"subject_ref":"ent_1","predicate":"has_status","object_value":"resolved"}},
  {{"subject_ref":"ent_3","predicate":"manages","object_ref":"ent_1","confidence":0.8}}
],"notes":null}}

# Your turn

Now extract from the chunk below. Emit JSON only, matching the schema.

Chunk metadata: {chunk_meta}
Chunk text:
{input_text}
"""


CONTINUE_PROMPT = """Some entities or facts were likely missed in the previous extraction. Add any you find. Return the same JSON shape. Entity refs must not collide with refs already used above; start from ent_{next_ref_index}. If nothing is missing, return entities:[] and triples:[].
"""


LOOP_PROMPT = """Are there still entities or facts that should be added? Answer with a single letter: Y or N.
"""


DEFAULT_ENTITY_TYPES = [
    "Person",
    "Organization",
    "Project",
    "Ticket",
    "Policy",
    "Document",
    "Product",
    "Meeting",
    "Message",
    "Event",
]

DEFAULT_PREDICATES = [
    "works_at",
    "reports_to",
    "manages",
    "owns",
    "part_of",
    "mentions",
    "authored",
    "attended",
    "references",
    "supersedes",
    "located_in",
    "has_title",
    "has_email",
    "has_status",
]


def render_system_prompt(
    input_text: str,
    *,
    chunk_meta: str = "",
    entity_types: list[str] | None = None,
    predicates: list[str] | None = None,
) -> str:
    """Fill the SYSTEM_PROMPT template with concrete values."""
    types = entity_types if entity_types is not None else DEFAULT_ENTITY_TYPES
    preds = predicates if predicates is not None else DEFAULT_PREDICATES
    return SYSTEM_PROMPT.format(
        entity_types=", ".join(types),
        predicates=", ".join(preds),
        input_text=input_text,
        chunk_meta=chunk_meta or "(none)",
    )


def render_continue_prompt(next_ref_index: int) -> str:
    return CONTINUE_PROMPT.format(next_ref_index=next_ref_index)
