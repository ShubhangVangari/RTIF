"""Read-only report on an ingest run. Reads ingest_full.log; writes nothing.

The line that matters is the flag rate split by source type. Practitioner
documents are where the corpus's genuine disagreements live (§2.2), so Path C
should fire MORE often on them than on academic documents. A near-zero
practitioner flag rate means Path C detection is failing on exactly the material
the contested pages depend on.

    python scripts/watch_ingest.py [path/to/log]
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC_MAP_PATH = ROOT / "wiki_local" / "doc_map.json"
CONCEPTS_DIR = ROOT / "wiki_local" / "wiki" / "concepts"

DOC_RE = re.compile(r"^(DOC-\d+)\s+\((\d+)/(\d+)\)\s*$")
DECISION_RE = re.compile(r"^  ([ABC]) ([a-z0-9-]+) — ")
THIS_DOC_RE = re.compile(
    r"^  this doc: created (\d+) \| extended (\d+) \| flagged (\d+)"
    r" \| tokens ([\d,]+) in / ([\d,]+) out \| ([\d.]+)s"
)
RUNNING_RE = re.compile(
    r"^  running : created (\d+) \| extended (\d+) \| flagged (\d+)"
    r" \| tokens ([\d,]+) in / ([\d,]+) out \| \$([\d.]+)"
)
FAILED_RE = re.compile(r"^  FAILED after ([\d.]+)s: (.*)$")
SUMMARY_RE = re.compile(r"^documents (processed|failed|skipped)\s*: (\d+)")
LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


def source_type(doc_id: str, doc_map: dict) -> str:
    path = doc_map.get(doc_id, "")
    return "practitioner" if "/web-" in path else "academic"


def main() -> None:
    log_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "ingest_full.log"
    if not log_path.exists():
        print(f"no log at {log_path}")
        return

    doc_map = json.loads(DOC_MAP_PATH.read_text(encoding="utf-8"))
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()

    docs: list[dict] = []
    current: dict | None = None
    failures: list[tuple[str, str]] = []
    running: tuple | None = None
    summary: dict[str, int] = {}
    total_docs = 0

    for line in lines:
        match = DOC_RE.match(line)
        if match:
            current = {
                "doc_id": match.group(1),
                "decisions": [],
                "created": 0,
                "extended": 0,
                "flagged": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "seconds": 0.0,
                "complete": False,
            }
            total_docs = int(match.group(3))
            docs.append(current)
            continue

        if current is None:
            match = SUMMARY_RE.match(line)
            if match:
                summary[match.group(1)] = int(match.group(2))
            continue

        match = DECISION_RE.match(line)
        if match:
            current["decisions"].append((match.group(1), match.group(2)))
            continue

        match = THIS_DOC_RE.match(line)
        if match:
            current["created"] = int(match.group(1))
            current["extended"] = int(match.group(2))
            current["flagged"] = int(match.group(3))
            current["tokens_in"] = int(match.group(4).replace(",", ""))
            current["tokens_out"] = int(match.group(5).replace(",", ""))
            current["seconds"] = float(match.group(6))
            current["complete"] = True
            continue

        match = RUNNING_RE.match(line)
        if match:
            running = match.groups()
            continue

        match = FAILED_RE.match(line)
        if match:
            failures.append((current["doc_id"], match.group(2)))
            continue

        match = SUMMARY_RE.match(line)
        if match:
            summary[match.group(1)] = int(match.group(2))

    done = [d for d in docs if d["complete"]]

    print("=" * 68)
    print(f"log: {log_path}")
    finished = "processed" in summary
    print(f"state: {'FINISHED' if finished else 'IN PROGRESS'}")
    print(
        f"documents processed : {len(done)}"
        + (f" of {total_docs}" if total_docs else "")
    )
    print(f"documents failed    : {len(failures)}" + (
        f" (summary says {summary['failed']})" if "failed" in summary else ""))
    print(
        f"documents skipped   : "
        + (str(summary["skipped"]) if "skipped" in summary
           else "n/a until the run ends (skips print nothing)")
    )
    for doc_id, message in failures:
        print(f"    FAILED {doc_id}: {message[:100]}")

    if running:
        print(
            f"cumulative tokens   : {int(running[3].replace(',', '')):,} in"
            f" / {int(running[4].replace(',', '')):,} out"
        )
        print(f"cumulative cost     : ${running[5]}")
    if done:
        mean_secs = sum(d["seconds"] for d in done) / len(done)
        print(f"mean seconds/doc    : {mean_secs:.1f}")
        if total_docs and len(done) < total_docs:
            remaining = (total_docs - len(done)) * mean_secs
            print(
                f"projected remaining : {remaining/3600:.1f}h"
                f" ({total_docs - len(done)} documents)"
            )

    # --- path mix ---
    paths = {"A": 0, "B": 0, "C": 0}
    for doc in done:
        for path, _slug in doc["decisions"]:
            paths[path] += 1
    print("\n" + "-" * 68)
    print(
        f"PATH MIX            : A={paths['A']} (extend)  "
        f"B={paths['B']} (create)  C={paths['C']} (flag)"
    )

    # --- the line that matters ---
    print("\n" + "-" * 68)
    print("FLAG RATE BY SOURCE TYPE (Path C)")
    by_type: dict[str, dict[str, int]] = {
        "academic": {"docs": 0, "flags": 0},
        "practitioner": {"docs": 0, "flags": 0},
    }
    for doc in done:
        kind = source_type(doc["doc_id"], doc_map)
        by_type[kind]["docs"] += 1
        by_type[kind]["flags"] += sum(1 for p, _ in doc["decisions"] if p == "C")

    rates = {}
    for kind in ("academic", "practitioner"):
        count = by_type[kind]["docs"]
        flags = by_type[kind]["flags"]
        rate = flags / count if count else 0.0
        rates[kind] = rate
        print(f"  {kind:<13}: {flags:>3} flags / {count:>3} docs = {rate:.3f} per doc")

    if by_type["practitioner"]["docs"] == 0:
        print("  (no practitioner documents processed yet)")
    elif rates["practitioner"] <= rates["academic"]:
        print(
            "  WARNING: practitioner flag rate is not higher than academic.\n"
            "  Practitioner sources carry the disagreements the contested pages\n"
            "  depend on; Path C should fire more often on them, not less."
        )
    else:
        print("  OK: practitioner documents flag more often than academic ones.")

    # --- creates over time (§2.6 should decay toward zero) ---
    print("\n" + "-" * 68)
    print("PAGES CREATED PER DOCUMENT (§2.6 — should decay toward zero)")
    created = [d["created"] for d in done]
    print(f"  {created}")
    print(f"  total created: {sum(created)}")
    if len(created) >= 20:
        half = len(created) // 2
        print(
            f"  first half: {sum(created[:half])} | second half: {sum(created[half:])}"
        )

    # --- link density ---
    print("\n" + "-" * 68)
    print("OUTBOUND LINKS (§4.3 target 3-8)")
    counts = []
    unreadable = 0
    for path in sorted(CONCEPTS_DIR.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            unreadable += 1
            continue
        parts = text.split("\n---\n\n", 1)
        body = parts[1] if len(parts) == 2 else ""
        if not body.strip():
            continue
        seen, ordered = set(), []
        for slug in LINK_RE.findall(body):
            if slug not in seen:
                seen.add(slug)
                ordered.append(slug)
        counts.append((path.stem, len(ordered)))

    if counts:
        mean = sum(n for _, n in counts) / len(counts)
        in_range = sum(1 for _, n in counts if 3 <= n <= 8)
        print(f"  pages with a body: {len(counts)}")
        print(f"  mean outbound links: {mean:.2f}")
        print(f"  within 3-8: {in_range}/{len(counts)}")
        below = [s for s, n in counts if n < 3]
        if below:
            print(f"  below 3 ({len(below)}): {', '.join(below[:10])}"
                  + (" ..." if len(below) > 10 else ""))
    else:
        print("  no pages with a body yet")
    if unreadable:
        print(f"  ({unreadable} file(s) unreadable this pass — mid-write, harmless)")


if __name__ == "__main__":
    main()
