"""Cloud Storage-backed Store. Same key structure as store/local.py. See SCHEMA.md §0."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from google.api_core.exceptions import NotFound
from google.cloud import storage

from core.models import (
    Page,
    SourcePage,
    markdown_to_page,
    markdown_to_source_page,
    page_to_markdown,
    source_page_to_markdown,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_MAP_PATH = REPO_ROOT / "wiki_local" / "doc_map.json"

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _page_fingerprint(page: Page) -> str:
    stable = replace(page, updated=_EPOCH)
    return hashlib.sha256(page_to_markdown(stable).encode("utf-8")).hexdigest()


def _source_page_fingerprint(page: SourcePage) -> str:
    stable = replace(page, ingested=_EPOCH)
    return hashlib.sha256(source_page_to_markdown(stable).encode("utf-8")).hexdigest()


def _download_text_or_empty(blob: storage.Blob) -> str:
    try:
        return blob.download_as_text()
    except NotFound:
        return ""


class GCSStore:
    def __init__(self, bucket_name: str | None = None) -> None:
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name or os.environ["BUCKET"])

    def _blob(self, key: str) -> storage.Blob:
        return self.bucket.blob(key)

    def read_page(self, slug: str) -> Page | None:
        blob = self._blob(f"wiki/concepts/{slug}.md")
        if not blob.exists():
            return None
        return markdown_to_page(blob.download_as_text())

    def write_page(self, page: Page) -> bool:
        blob = self._blob(f"wiki/concepts/{page.slug}.md")
        if blob.exists():
            existing = markdown_to_page(blob.download_as_text())
            if _page_fingerprint(existing) == _page_fingerprint(page):
                return False
        blob.upload_from_string(page_to_markdown(page))
        return True

    def list_pages(self) -> list[str]:
        prefix = "wiki/concepts/"
        blobs = self.client.list_blobs(self.bucket, prefix=prefix)
        return sorted(
            blob.name[len(prefix) : -len(".md")]
            for blob in blobs
            if blob.name.endswith(".md")
        )

    def read_index(self) -> str:
        return _download_text_or_empty(self._blob("wiki/index.md"))

    def write_index(self, content: str) -> None:
        self._blob("wiki/index.md").upload_from_string(content)

    def append_log(self, entry: str) -> None:
        # TODO: read-modify-write on an immutable GCS object is not
        # concurrency-safe — two concurrent appends can race and one can
        # clobber the other. No locking is implemented here.
        blob = self._blob("wiki/log.md")
        existing = _download_text_or_empty(blob)
        blob.upload_from_string(existing + entry + "\n")

    def read_raw(self, doc_id: str) -> str:
        doc_map = json.loads(DOC_MAP_PATH.read_text(encoding="utf-8"))
        rel_path = doc_map[doc_id]
        return (REPO_ROOT / rel_path).read_text(encoding="utf-8")

    def read_source_page(self, doc_id: str) -> SourcePage | None:
        blob = self._blob(f"wiki/sources/{doc_id}.md")
        if not blob.exists():
            return None
        return markdown_to_source_page(blob.download_as_text())

    def write_source_page(self, page: SourcePage) -> bool:
        blob = self._blob(f"wiki/sources/{page.doc_id}.md")
        if blob.exists():
            existing = markdown_to_source_page(blob.download_as_text())
            if _source_page_fingerprint(existing) == _source_page_fingerprint(page):
                return False
        blob.upload_from_string(source_page_to_markdown(page))
        return True
