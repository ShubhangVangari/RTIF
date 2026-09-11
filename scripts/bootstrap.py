
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.models import Page
from store.local import LocalStore

SURVEY_PATH = ROOT / "wiki_local" / "survey.json"

ACRONYMS = {"llm", "rag", "sft"}
MINOR_WORDS = {
    "a", "an", "the",
    "and", "but", "or", "for", "nor", "so", "yet",
    "as", "at", "by", "in", "of", "on", "per", "to", "up", "via", "vs",
}


def slug_to_title(slug: str) -> str:
    words = slug.split("-")
    last = len(words) - 1
    parts = []
    for i, word in enumerate(words):
        lower = word.lower()
        if lower in ACRONYMS:
            parts.append(lower.upper())
        elif i not in (0, last) and lower in MINOR_WORDS:
            parts.append(lower)
        else:
            parts.append(lower.capitalize())
    return " ".join(parts)


def render_index(pages: list[Page]) -> str:
    lines = ["# Index", ""]
    for page in sorted(pages, key=lambda p: p.slug):
        lines.append(f"- [[{page.slug}]] — {page.summary}")
    return "\n".join(lines) + "\n"


clusters = json.loads(SURVEY_PATH.read_text(encoding="utf-8"))["clusters"]
store = LocalStore()
now = datetime.now(timezone.utc)

pages: list[Page] = []
written = 0
skipped = 0
contested = 0

for cluster in clusters:
    flags = []
    if cluster["is_contested"]:
        flags = ["contested: " + cluster["flag_payload"]]
        contested += 1

    page = Page(
        slug=cluster["slug"],
        title=slug_to_title(cluster["slug"]),
        type="concept",
        summary=cluster["summary"],
        body="",
        updated=now,
        flags=flags,
    )
    pages.append(page)
    if store.write_page(page):
        written += 1
    else:
        skipped += 1

index_text = render_index(pages)
store.write_index(index_text)

today = now.strftime("%Y-%m-%d")
existing_log = store.log_path.read_text(encoding="utf-8") if store.log_path.exists() else ""
today_marker = f"## [{today}] bootstrap |"

if today_marker not in existing_log:
    log_entry = (
        f"## [{today}] bootstrap | seed taxonomy from survey.json\n"
        f"- {written} concept pages created with empty bodies\n"
        f"- index.md rendered"
    )
    store.append_log(log_entry)
    log_note = "appended"
else:
    log_note = "skipped, already present for today"

print(f"pages written: {written}")
print(f"pages skipped (unchanged): {skipped}")
print(f"contested pages: {contested}")
print(f"index.md characters: {len(index_text)}")
print(f"log entry: {log_note}")
