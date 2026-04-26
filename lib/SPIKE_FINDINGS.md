# LlamaIndex spike findings

Short log from running `SimpleDirectoryReader` against the real `sample_dataset` (EnterpriseBench from Hugging Face).

## Dataset shape after `git lfs pull`

| Domain | Files | Source type | Notes |
|---|---|---|---|
| `Business_and_Management/` | 2 JSON | unknown -> `crm` | clients + vendors |
| `Collaboration_tools/` | 1 JSON | `chat` | 2897 conversations |
| `Customer_Relation_Management/` | 273 PDF + 5 JSON | `crm`, `email`, `chat` | invoices, POs, shipping; customers/products/sentiment/chats |
| `Enterprise Social Platform/` | 1 JSON | `chat` | 971 internal posts |
| `Enterprise_mail_system/` | 1 JSON | `email` | **11,928 emails**, authority 0.40 |
| `Human_Resource_Management/` | 1013 PDF + 1 JSON + 1 CSV | `hr` | resumes (PDF), employees.json (canonical), resume metadata CSV — authority 1.00 |
| `IT_Service_Management/` | 1 JSON | `ticket` | 163 tickets |
| `Inazuma_Overflow/` | 1 JSON | `unknown` | 10,823 internal StackOverflow-style Q&A |
| `Policy_Documents/` | 26 PDF | `policy` | authority 0.70 |
| `Workspace/` | 1 JSON | `unknown` | 750 GitHub repo samples |

Total after per-record splitting: **67,030 documents** covering 1,322 unique files.

## Key backend findings

1. **37 files were git-lfs pointers** until `brew install git-lfs && git lfs pull`. Notably ALL 26 policy PDFs, `emails.json`, `overflow.json`, `product_sentiment.json`, `tasks.jsonl`. Easy to miss — pypdf rejects them as "invalid pdf header: b'versi'".
2. **JSON arrays load as one Document** by default. For files like `emails.json` (11,928 records in one file) this destroys provenance. Fixed via `JSONRecordReader` in `lib/readers.py`.
3. **CSV default reader concatenates fields with ', '** losing column semantics. Fixed via `CSVRecordReader`.
4. **`overflow.json` has CRLF line endings.** All other JSON uses LF. Normalized in the custom reader.
5. **84 PDF pages have <50 chars of text.** Concentrated in resumes page 3 — likely signature/photo pages. Skip during extraction if `len(text.strip()) < 50`.
6. **`tasks.jsonl` is the eval suite**, not extraction input. Excluded from loading.

## Use the custom reader, not SimpleDirectoryReader directly

```python
from lib.ingestor import qontext_reader

reader = qontext_reader("sample_dataset")
docs = reader.load_data()
# docs[i].metadata includes: file_path, file_name, file_type,
#   record_index / record_id (for JSON)
#   row_index / row_count / columns (for CSV)
#   page_label (for PDF)
```

## Source_type mapping (for when you populate `sources.source_type`)

```python
DOMAIN_TO_SOURCE_TYPE = {
    "Human_Resource_Management": "hr",       # authority 1.00
    "Customer_Relation_Management": "crm",   # authority 0.80, but chats/emails inside may override
    "Business_and_Management":     "crm",
    "Policy_Documents":            "policy", # 0.70
    "IT_Service_Management":       "ticket", # 0.50
    "Enterprise_mail_system":      "email",  # 0.40
    "Collaboration_tools":         "chat",   # 0.30
    "Enterprise Social Platform":  "chat",
    "Inazuma_Overflow":            "unknown",
    "Workspace":                   "unknown",
}
```

Tomorrow's extractor should derive `source_type` from the first path segment under `sample_dataset/`.

## Performance notes

- Full load: **30s** for 67k documents (single-threaded).
- **98M chars** / ~24M tokens of extraction surface area. Budget accordingly for LLM calls.
- Longest single record after splitting: `conversations.json` entries up to ~30KB. Well within LLM context limits.
