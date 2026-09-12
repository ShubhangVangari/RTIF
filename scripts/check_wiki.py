"""scripts/check_wiki.py — audits the compiled wiki and prints a report.

Run like this:

    python scripts/check_wiki.py
    python scripts/check_wiki.py --store gcs

Exits 1 if any error-severity issue was found, 0 otherwise, so this can be
wired into a build step later.
"""

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.check import check_wiki
from core.models import Issue, Page
from store.local import LocalStore

DOC_MAP_PATH = ROOT / "wiki_local" / "doc_map.json"
EXCLUDED_PATH = ROOT / "wiki_local" / "excluded.json"


# Step 1: read the command line, then open the store it points at.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit the compiled wiki.")
    parser.add_argument("--store", choices=["local", "gcs"], default="local")
    return parser.parse_args()


def open_store(kind: str):
    if kind == "gcs":
        from store.gcs import GCSStore

        return GCSStore()
    return LocalStore()


# Step 2: read every concept page out of the store, by slug.
def read_all_pages(store) -> dict[str, Page]:
    pages: dict[str, Page] = {}
    for slug in store.list_pages():
        page = store.read_page(slug)
        if page is not None:
            pages[slug] = page
    return pages


# Step 3: read doc_map.json and excluded.json off disk. These are corpus
# metadata, not wiki content, so they come from the repo directly rather
# than through the store.
def read_all_doc_ids() -> list[str]:
    doc_map = json.loads(DOC_MAP_PATH.read_text(encoding="utf-8"))
    return list(doc_map)


def read_excluded_doc_ids() -> list[str]:
    entries = json.loads(EXCLUDED_PATH.read_text(encoding="utf-8"))
    excluded = []
    for entry in entries:
        excluded.append(entry["doc_id"] if isinstance(entry, dict) else str(entry))
    return excluded


# Sorts the flat list of Issues from check_wiki() into one list per kind, so
# the report below can print one section per check.
def group_by_kind(issues: list[Issue]) -> dict[str, list[Issue]]:
    grouped: dict[str, list[Issue]] = {}
    for issue in issues:
        grouped.setdefault(issue.kind, []).append(issue)
    return grouped


# ---------------------------------------------------------------
# Step 5: print the report, one section per check
# ---------------------------------------------------------------


def print_broken_links(issues: list[Issue]) -> None:
    print(f"BROKEN LINKS ({len(issues)})")
    for issue in issues:
        print(f"  {issue.page} -> {issue.detail}")
    print()


def print_orphan_pages(issues: list[Issue]) -> None:
    print(f"ORPHAN PAGES ({len(issues)})")
    for issue in issues:
        print(f"  {issue.page}")
    print()


def print_uncited_sentences(issues: list[Issue]) -> None:
    by_page: dict[str, list[str]] = {}
    for issue in issues:
        by_page.setdefault(issue.page, []).append(issue.detail)

    print(f"UNCITED SENTENCES ({len(issues)} sentences across {len(by_page)} pages)")
    for page, sentences in by_page.items():
        print(f"  {page} ({len(sentences)})")
        for sentence in sentences[:3]:
            preview = sentence if len(sentence) <= 80 else sentence[:80] + "..."
            print(f'    "{preview}"')
    print()


def print_untraced_documents(issues: list[Issue]) -> None:
    print(f"DOCUMENTS THAT LEFT NO TRACE ({len(issues)})")
    doc_ids = [issue.detail for issue in issues]
    for start in range(0, len(doc_ids), 6):
        print("  " + "  ".join(doc_ids[start : start + 6]))
    print()


def print_index_mismatch(issues: list[Issue]) -> None:
    print(f"INDEX MISMATCH ({len(issues)})")
    for issue in issues:
        print(f"  {issue.page} — {issue.detail}")
    print()


def print_oversized_pages(issues: list[Issue]) -> None:
    print(f"OVERSIZED PAGES ({len(issues)})")
    for issue in issues:
        print(f"  {issue.page} — {issue.detail}")
    print()


# One printer per check kind, only called for a check that actually found
# something — an empty section would just be noise.
SECTION_PRINTERS = {
    "broken_link": print_broken_links,
    "orphan_page": print_orphan_pages,
    "uncited_sentence": print_uncited_sentences,
    "untraced_document": print_untraced_documents,
    "index_mismatch": print_index_mismatch,
    "oversized_page": print_oversized_pages,
}


def print_summary(issues: list[Issue]) -> None:
    errors = sum(1 for issue in issues if issue.severity == "error")
    warnings = sum(1 for issue in issues if issue.severity == "warning")
    reviews = sum(1 for issue in issues if issue.severity == "review")
    print("-" * 60)
    print(f"{errors} errors | {warnings} warnings | {reviews} to review")


def main() -> None:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    store = open_store(args.store)

    pages = read_all_pages(store)
    index = store.read_index()
    all_doc_ids = read_all_doc_ids()
    excluded_doc_ids = read_excluded_doc_ids()

    # Step 4: run the audit.
    issues = check_wiki(
        pages=pages,
        index=index,
        all_doc_ids=all_doc_ids,
        excluded_doc_ids=excluded_doc_ids,
    )

    grouped = group_by_kind(issues)
    for kind, printer in SECTION_PRINTERS.items():
        if kind in grouped:
            printer(grouped[kind])
    print_summary(issues)

    error_count = sum(1 for issue in issues if issue.severity == "error")
    sys.exit(1 if error_count else 0)


if __name__ == "__main__":
    main()
