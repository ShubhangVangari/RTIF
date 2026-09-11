"""Filesystem-backed Store, rooted at wiki_local/wiki/. See SCHEMA.md §0."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from core.models import (
    Page,
    SourcePage,
    markdown_to_page,
    markdown_to_source_page,
    page_to_markdown,
    source_page_to_markdown,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = REPO_ROOT / "wiki_local" / "wiki"
DOC_MAP_PATH = REPO_ROOT / "wiki_local" / "doc_map.json"

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _page_fingerprint(page: Page) -> str:
    stable = replace(page, updated=_EPOCH)
    return hashlib.sha256(page_to_markdown(stable).encode("utf-8")).hexdigest()


def _source_page_fingerprint(page: SourcePage) -> str:
    stable = replace(page, ingested=_EPOCH)
    return hashlib.sha256(source_page_to_markdown(stable).encode("utf-8")).hexdigest()


class LocalStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else DEFAULT_ROOT
        self.concepts_dir = self.root / "concepts"
        self.entities_dir = self.root / "entities"
        self.sources_dir = self.root / "sources"
        self.index_path = self.root / "index.md"
        self.log_path = self.root / "log.md"

        for directory in (self.concepts_dir, self.entities_dir, self.sources_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.index_path.touch(exist_ok=True)
        self.log_path.touch(exist_ok=True)

    def _page_path(self, slug: str) -> Path:
        return self.concepts_dir / f"{slug}.md"

    def _source_path(self, doc_id: str) -> Path:
        return self.sources_dir / f"{doc_id}.md"

    def read_page(self, slug: str) -> Page | None:
        path = self._page_path(slug)
        if not path.exists():
            return None
        return markdown_to_page(path.read_text(encoding="utf-8"))

    def write_page(self, page: Page) -> bool:
        path = self._page_path(page.slug)
        if path.exists():
            existing = markdown_to_page(path.read_text(encoding="utf-8"))
            if _page_fingerprint(existing) == _page_fingerprint(page):
                return False
        path.write_text(page_to_markdown(page), encoding="utf-8")
        return True

    def list_pages(self) -> list[str]:
        return sorted(p.stem for p in self.concepts_dir.glob("*.md"))

    def read_index(self) -> str:
        return self.index_path.read_text(encoding="utf-8")

    def write_index(self, content: str) -> None:
        self.index_path.write_text(content, encoding="utf-8")

    def append_log(self, entry: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(entry + "\n")

    def read_raw(self, doc_id: str) -> str:
        doc_map = json.loads(DOC_MAP_PATH.read_text(encoding="utf-8"))
        rel_path = doc_map[doc_id]
        return (REPO_ROOT / rel_path).read_text(encoding="utf-8")

    def read_source_page(self, doc_id: str) -> SourcePage | None:
        path = self._source_path(doc_id)
        if not path.exists():
            return None
        return markdown_to_source_page(path.read_text(encoding="utf-8"))

    def write_source_page(self, page: SourcePage) -> bool:
        path = self._source_path(page.doc_id)
        if path.exists():
            existing = markdown_to_source_page(path.read_text(encoding="utf-8"))
            if _source_page_fingerprint(existing) == _source_page_fingerprint(page):
                return False
        path.write_text(source_page_to_markdown(page), encoding="utf-8")
        return True
