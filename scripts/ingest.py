"""Ingest documents into the wiki, one at a time. See SCHEMA.md §1.5, §2.6.

Wires the real Vertex call into core.compile.compile_document. One document per
model round trip: the index changes as pages are created, so it is re-read
before every document.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.compile import compile_document, document_sha256
from store.local import LocalStore

SCHEMA_PATH = ROOT / "SCHEMA.md"
DOC_MAP_PATH = ROOT / "wiki_local" / "doc_map.json"
EXCLUDED_PATH = ROOT / "wiki_local" / "excluded.json"
ORDER_PATH = ROOT / "wiki_local" / "ingest_order.txt"

MODEL = "gemini-2.5-pro"
ATTEMPTS = 3  # one call plus two retries

# Vertex list price for gemini-2.5-pro at <=200k context, USD per 1M tokens.
INPUT_PER_MILLION = 1.25
OUTPUT_PER_MILLION = 10.00


def make_call_model(client):
    def call_model(prompt: str, schema: dict) -> dict:
        last_error = None
        for attempt in range(ATTEMPTS):
            try:
                resp = client.models.generate_content(
                    model=MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0,
                        response_mime_type="application/json",
                        response_schema=schema,
                    ),
                )
                payload = json.loads(resp.text)
                if not isinstance(payload, dict):
                    raise ValueError(f"model returned {type(payload).__name__}, expected an object")
                usage = resp.usage_metadata
                payload["_usage"] = {
                    "tokens_in": getattr(usage, "prompt_token_count", 0) or 0,
                    "tokens_out": (getattr(usage, "candidates_token_count", 0) or 0)
                    + (getattr(usage, "thoughts_token_count", 0) or 0),
                }
                return payload
            except Exception as exc:
                last_error = exc
                if attempt < ATTEMPTS - 1:
                    time.sleep(2 * (attempt + 1))
        raise last_error

    return call_model


def ingest_order(doc_map: dict[str, str]) -> list[str]:
    """One practitioner document every six academic, as build_corpus_surface.py does.

    doc_map order is alphabetical, which puts every arxiv-* document before every
    web-* one. Ingesting in that order starves contested pages of the
    practitioner side of their dispute until the very end of the run. Source type
    comes from the raw filename prefix, not parsed frontmatter.
    """
    academic, practitioner = [], []
    for doc_id in sorted(doc_map):
        target = practitioner if "/web-" in doc_map[doc_id] else academic
        target.append(doc_id)

    order: list[str] = []
    next_practitioner = 0
    for i, doc_id in enumerate(academic):
        order.append(doc_id)
        if i % 6 == 5 and next_practitioner < len(practitioner):
            order.append(practitioner[next_practitioner])
            next_practitioner += 1
    order.extend(practitioner[next_practitioner:])
    return order


def load_excluded() -> set[str]:
    entries = json.loads(EXCLUDED_PATH.read_text(encoding="utf-8"))
    excluded = set()
    for entry in entries:
        excluded.add(entry["doc_id"] if isinstance(entry, dict) else str(entry))
    return excluded


def read_log(store) -> str:
    """The Store protocol has no read_log, so reach into the backing object."""
    log_path = getattr(store, "log_path", None)
    if log_path is not None:
        return log_path.read_text(encoding="utf-8") if log_path.exists() else ""

    from google.api_core.exceptions import NotFound

    try:
        return store.bucket.blob("wiki/log.md").download_as_text()
    except NotFound:
        return ""


def render_index(store) -> str:
    rows = []
    for slug in sorted(store.list_pages()):
        page = store.read_page(slug)
        if page is not None:
            rows.append(f"- [[{page.slug}]] — {page.summary}")
    return "# Index\n\n" + "\n".join(rows) + "\n"


def log_entry(doc_id: str, title: str, digest: str, decisions) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"## [{today}] ingest | {doc_id} | {title}", f"- sha256: {digest}"]
    for decision in decisions:
        lines.append(f"- {decision.path} {decision.page} — {decision.why}")
        if decision.classification_reasoning:
            lines.append(f"  {decision.classification_reasoning}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest documents into the wiki.")
    parser.add_argument("--docs", type=int, default=10, help="how many documents to ingest")
    parser.add_argument("--store", choices=["local", "gcs"], default="local")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")

    if args.store == "gcs":
        from store.gcs import GCSStore

        store = GCSStore()
    else:
        store = LocalStore()

    client = genai.Client(
        vertexai=True,
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location="us-central1",
    )
    call_model = make_call_model(client)

    doc_map = json.loads(DOC_MAP_PATH.read_text(encoding="utf-8"))
    excluded = load_excluded()
    order = [doc_id for doc_id in ingest_order(doc_map) if doc_id not in excluded]
    ORDER_PATH.write_text("\n".join(order) + "\n", encoding="utf-8")
    print(f"ingest order ({len(order)} documents) written to {ORDER_PATH}")

    processed = 0
    skipped = 0
    failures: list[tuple[str, str]] = []
    total_in = 0
    total_out = 0
    created = 0
    extended = 0
    flagged = 0

    for doc_id in order:
        if processed >= args.docs:
            break

        document = store.read_raw(doc_id)
        log = read_log(store)
        if document_sha256(document) in log:
            skipped += 1
            continue

        processed += 1
        started = time.perf_counter()
        print(f"\n{doc_id}  ({processed}/{args.docs})")

        try:
            result = compile_document(
                schema=SCHEMA_PATH.read_text(encoding="utf-8"),
                index=store.read_index(),
                doc_id=doc_id,
                document=document,
                load_page=store.read_page,
                call_model=call_model,
                log=log,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - started
            print(f"  FAILED after {elapsed:.1f}s: {type(exc).__name__}: {exc}")
            failures.append((doc_id, f"{type(exc).__name__}: {exc}"))
            continue

        summary_changed = False
        for page in result.pages:
            before = store.read_page(page.slug)
            if before is None or before.summary != page.summary:
                summary_changed = True
            store.write_page(page)

        store.write_source_page(result.source_page)
        store.append_log(
            log_entry(doc_id, result.source_page.title, result.source_page.sha256, result.decisions)
        )
        if summary_changed:
            store.write_index(render_index(store))

        doc_created = sum(1 for d in result.decisions if d.path == "B")
        doc_extended = sum(1 for d in result.decisions if d.path == "A")
        doc_flagged = sum(1 for d in result.decisions if d.path == "C")
        created += doc_created
        extended += doc_extended
        flagged += doc_flagged
        total_in += result.tokens_in
        total_out += result.tokens_out

        for decision in result.decisions:
            print(f"  {decision.path} {decision.page} — {decision.why}")
        elapsed = time.perf_counter() - started
        running_cost = (
            total_in / 1_000_000 * INPUT_PER_MILLION
            + total_out / 1_000_000 * OUTPUT_PER_MILLION
        )
        print(
            f"  this doc: created {doc_created} | extended {doc_extended}"
            f" | flagged {doc_flagged}"
            f" | tokens {result.tokens_in:,} in / {result.tokens_out:,} out"
            f" | {elapsed:.1f}s"
        )
        print(
            f"  running : created {created} | extended {extended} | flagged {flagged}"
            f" | tokens {total_in:,} in / {total_out:,} out"
            f" | ${running_cost:.2f}"
        )

    cost = total_in / 1_000_000 * INPUT_PER_MILLION + total_out / 1_000_000 * OUTPUT_PER_MILLION
    print("\n" + "=" * 60)
    print(f"documents processed : {processed}")
    print(f"documents failed    : {len(failures)}")
    print(f"documents skipped   : {skipped} (already ingested)")
    print(f"tokens              : {total_in:,} in / {total_out:,} out")
    print(f"estimated cost      : ${cost:.2f}")
    print(f"pages created       : {created}")
    print(f"pages extended      : {extended}")
    print(f"pages flagged       : {flagged}")
    for doc_id, message in failures:
        print(f"  FAILED {doc_id}: {message}")


if __name__ == "__main__":
    main()
