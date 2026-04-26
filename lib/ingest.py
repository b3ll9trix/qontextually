from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

from llama_index.core import Document

from db.db import get_connection
from lib.extractor import ExtractionOutcome, OutOfCreditsError, extract_from_chunk
from lib.ingestor import qontext_reader
from lib.builder import WriteSummary, write_extraction

log = logging.getLogger(__name__)


DOMAIN_TO_SOURCE_TYPE: dict[str, str] = {
    "Human_Resource_Management": "hr",
    "Customer_Relation_Management": "crm",
    "Business_and_Management": "crm",
    "Policy_Documents": "policy",
    "IT_Service_Management": "ticket",
    "Enterprise_mail_system": "email",
    "Collaboration_tools": "chat",
    "Enterprise Social Platform": "chat",
    "Inazuma_Overflow": "unknown",
    "Workspace": "unknown",
}

TIER1_FILES: set[str] = {
    "employees.json",
    "resume_information.csv",
    "emails.json",
    "it_tickets.json",
    "conversations.json",
    "customer_support_chats.json",
    "customers.json",
    "clients.json",
    "vendors.json",
}

TIER1_DIRS: set[str] = {"Policy_Documents"}

TIER2_FILES: set[str] = {
    "products.json",
    "sales.json",
    "product_sentiment.json",
    "overflow.json",
    "GitHub.json",
    "posts.json",
}

MIN_PDF_CHARS = 50
MAX_CHUNK_CHARS = 60_000


@dataclass
class IngestStats:
    total_seen: int = 0
    skipped_trivial: int = 0
    skipped_oversize: int = 0
    skipped_already_done: int = 0
    extracted_ok: int = 0
    extraction_failed: int = 0
    write_failed: int = 0
    total_cost: float = 0.0
    total_tokens: int = 0
    entities_new: int = 0
    entities_matched: int = 0
    triples_new: int = 0
    triples_linked: int = 0
    models_used: dict[str, int] = field(default_factory=dict)

    def merge_outcome(self, outcome: ExtractionOutcome) -> None:
        self.total_cost += outcome.total_cost
        self.total_tokens += outcome.total_tokens
        if outcome.ok:
            self.extracted_ok += 1
            if outcome.model_used:
                self.models_used[outcome.model_used] = (
                    self.models_used.get(outcome.model_used, 0) + 1
                )
        else:
            self.extraction_failed += 1

    def merge_write(self, summary: WriteSummary) -> None:
        if not summary.ok:
            self.write_failed += 1
            return
        self.entities_new += summary.entities_inserted
        self.entities_matched += summary.entities_matched
        self.triples_new += summary.triples_inserted
        self.triples_linked += summary.triples_linked_to_existing

    def pretty(self) -> str:
        lines = [
            f"  seen={self.total_seen}",
            f"  extracted_ok={self.extracted_ok} extract_failed={self.extraction_failed} write_failed={self.write_failed}",
            f"  skipped_trivial={self.skipped_trivial} skipped_oversize={self.skipped_oversize} skipped_already_done={self.skipped_already_done}",
            f"  entities_new={self.entities_new} entities_matched={self.entities_matched}",
            f"  triples_new={self.triples_new} triples_linked={self.triples_linked}",
            f"  total_cost=${self.total_cost:.4f}  total_tokens={self.total_tokens}",
            f"  models_used={self.models_used}",
        ]
        return "\n".join(lines)


def _domain_from_path(file_path: str, dataset_root: str) -> str:
    try:
        rel = Path(file_path).resolve().relative_to(Path(dataset_root).resolve())
    except ValueError:
        return ""
    return rel.parts[0] if rel.parts else ""


def _source_type_for(domain: str) -> str:
    return DOMAIN_TO_SOURCE_TYPE.get(domain, "unknown")


def _is_tier1(file_name: str, domain: str) -> bool:
    if file_name in TIER1_FILES:
        return True
    if domain in TIER1_DIRS:
        return True
    if file_name.endswith(".pdf") and domain == "Human_Resource_Management":
        return True
    return False


def _is_tier2(file_name: str, domain: str) -> bool:
    if file_name in TIER2_FILES:
        return True
    if file_name.endswith(".pdf") and domain == "Customer_Relation_Management":
        return True
    return False


def _already_extracted(conn, chunk_meta: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM audit_log WHERE actor='extractor' AND action='extract_chunk' AND target_id=? LIMIT 1",
        (chunk_meta,),
    ).fetchone()
    return row is not None


def select_tier1_docs(
    dataset_root: str,
    *,
    reader_factory=qontext_reader,
) -> list[Document]:
    reader = reader_factory(dataset_root)
    all_docs = reader.load_data()
    selected = []
    for d in all_docs:
        name = d.metadata.get("file_name", "")
        file_path = d.metadata.get("file_path", "")
        domain = _domain_from_path(file_path, dataset_root)
        if _is_tier1(name, domain):
            selected.append(d)
    return selected


def select_tier2_docs(
    dataset_root: str,
    *,
    reader_factory=qontext_reader,
) -> list[Document]:
    reader = reader_factory(dataset_root)
    all_docs = reader.load_data()
    selected = []
    for d in all_docs:
        name = d.metadata.get("file_name", "")
        file_path = d.metadata.get("file_path", "")
        domain = _domain_from_path(file_path, dataset_root)
        if _is_tier2(name, domain):
            selected.append(d)
    return selected


_thread_conn = threading.local()


def _worker_conn():
    if not hasattr(_thread_conn, "conn"):
        _thread_conn.conn = get_connection()
    return _thread_conn.conn


def _build_chunk_meta_key(doc: Document, dataset_root: str) -> str:
    meta = doc.metadata
    file_path = meta.get("file_path", "")
    file_name = meta.get("file_name", "")
    domain = _domain_from_path(file_path, dataset_root)
    source_type = _source_type_for(domain)
    parts = [f"source_type={source_type}", f"file={file_name}"]
    if "record_id" in meta:
        parts.append(f"record_id={meta['record_id']}")
    if "record_index" in meta:
        parts.append(f"record_index={meta['record_index']}")
    if "row_index" in meta:
        parts.append(f"row={meta['row_index']}")
    if "page_label" in meta:
        parts.append(f"page={meta['page_label']}")
    return " ".join(parts)


def _process_one(doc: Document, dataset_root: str) -> tuple[str, ExtractionOutcome, Optional[WriteSummary]]:
    conn = _worker_conn()
    meta = doc.metadata
    file_path = meta.get("file_path", "")
    domain = _domain_from_path(file_path, dataset_root)
    source_type = _source_type_for(domain)

    chunk_meta = _build_chunk_meta_key(doc, dataset_root)

    outcome = extract_from_chunk(doc.text, chunk_meta=chunk_meta, conn=conn)

    write_summary: Optional[WriteSummary] = None
    if outcome.ok:
        properties = {k: v for k, v in meta.items() if k in {"record_id", "record_index", "row_index", "page_label", "file_type", "columns"}}
        write_summary = write_extraction(
            outcome.result,
            document_path=file_path,
            source_type=source_type,
            raw_text=doc.text,
            properties=properties,
            conn=conn,
        )

    return chunk_meta, outcome, write_summary


def run_ingest(
    docs: Iterable[Document],
    *,
    dataset_root: str,
    workers: int = 8,
    max_items: Optional[int] = None,
    progress_every: int = 25,
    skip_already_done: bool = True,
) -> IngestStats:
    stats = IngestStats()
    supervisor_conn = get_connection()

    t0 = time.time()
    filtered: list[Document] = []
    for doc in docs:
        stats.total_seen += 1
        text = doc.text or ""
        if len(text.strip()) < MIN_PDF_CHARS and doc.metadata.get("file_type") == "application/pdf":
            stats.skipped_trivial += 1
            continue
        if len(text) > MAX_CHUNK_CHARS:
            stats.skipped_oversize += 1
            continue
        if skip_already_done and _already_extracted(supervisor_conn, _build_chunk_meta_key(doc, dataset_root)):
            stats.skipped_already_done += 1
            continue
        filtered.append(doc)
        if max_items and len(filtered) >= max_items:
            break

    log.info("ingest: %d selected after filtering (seen=%d)", len(filtered), stats.total_seen)
    print(f"[ingest] selected {len(filtered)} chunks for extraction "
          f"(seen={stats.total_seen} trivial={stats.skipped_trivial} oversize={stats.skipped_oversize} done={stats.skipped_already_done})",
          flush=True)

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process_one, d, dataset_root): d for d in filtered}
        try:
            for fut in as_completed(futures):
                try:
                    chunk_meta, outcome, write_summary = fut.result()
                except OutOfCreditsError:
                    print("[ingest] FATAL: out of credits \u2014 aborting", flush=True)
                    for f in futures:
                        f.cancel()
                    break
                except Exception as exc:
                    stats.extraction_failed += 1
                    log.exception("worker failed: %s", exc)
                    continue

                stats.merge_outcome(outcome)
                if write_summary is not None:
                    stats.merge_write(write_summary)

                done += 1
                if done % progress_every == 0:
                    elapsed = time.time() - t0
                    rate = done / elapsed if elapsed > 0 else 0
                    eta = (len(filtered) - done) / rate if rate > 0 else float("inf")
                    print(
                        f"[ingest] {done}/{len(filtered)} "
                        f"ok={stats.extracted_ok} fail={stats.extraction_failed} "
                        f"cost=${stats.total_cost:.3f} "
                        f"entities={stats.entities_new}+{stats.entities_matched} "
                        f"triples={stats.triples_new}+{stats.triples_linked} "
                        f"rate={rate:.1f}/s eta={eta/60:.1f}min",
                        flush=True,
                    )
        except KeyboardInterrupt:
            print("[ingest] interrupted \u2014 letting in-flight workers finish...", flush=True)

    elapsed = time.time() - t0
    print(f"\n[ingest] done in {elapsed/60:.1f} min", flush=True)
    print(stats.pretty(), flush=True)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the ingestion pipeline")
    parser.add_argument("--dataset", default="sample_dataset", help="Root dataset dir")
    parser.add_argument("--tier", choices=["1", "2", "all"], default="1", help="Tier to ingest")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max", type=int, default=None, help="Max chunks to process")
    parser.add_argument("--no-skip", action="store_true", help="Re-process already-done chunks")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if args.tier in ("1", "all"):
        print(f"=== Tier 1 ingest ===", flush=True)
        docs = select_tier1_docs(args.dataset)
        run_ingest(
            docs,
            dataset_root=args.dataset,
            workers=args.workers,
            max_items=args.max,
            skip_already_done=not args.no_skip,
        )

    if args.tier in ("2", "all"):
        print(f"\n=== Tier 2 ingest ===", flush=True)
        docs = select_tier2_docs(args.dataset)
        run_ingest(
            docs,
            dataset_root=args.dataset,
            workers=args.workers,
            max_items=args.max,
            skip_already_done=not args.no_skip,
        )

    return 0


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(".env")
    sys.exit(main())
