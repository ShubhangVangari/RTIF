import os
import uuid
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from core.models import Page
from store.local import LocalStore


def _local_store(tmp_path):
    return LocalStore(tmp_path / "wiki")


def _gcs_store(tmp_path):
    from store.gcs import GCSStore

    return GCSStore()


_FACTORIES = [_local_store]
if os.environ.get("BUCKET"):
    _FACTORIES.append(_gcs_store)


@pytest.fixture(params=_FACTORIES, ids=lambda f: f.__name__)
def store(request, tmp_path):
    return request.param(tmp_path)


def _read_log(store) -> str:
    if isinstance(store, LocalStore):
        return store.log_path.read_text(encoding="utf-8")
    from google.api_core.exceptions import NotFound

    try:
        return store.bucket.blob("wiki/log.md").download_as_text()
    except NotFound:
        return ""


def _unique_slug() -> str:
    return f"test-{uuid.uuid4().hex[:12]}"


def _make_page(slug: str, body: str = "# T\n\nBody [DOC-001].\n") -> Page:
    return Page(
        slug=slug,
        title="Title",
        type="concept",
        summary="A short summary for testing purposes and nothing else here.",
        body=body,
        updated=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_write_then_read_returns_equal_page(store):
    page = _make_page(_unique_slug())
    store.write_page(page)
    assert store.read_page(page.slug) == page


def test_read_page_missing_slug_returns_none(store):
    assert store.read_page(_unique_slug()) is None


def test_list_pages_returns_written_slugs(store):
    page = _make_page(_unique_slug())
    store.write_page(page)
    assert page.slug in store.list_pages()


def test_write_page_returns_false_when_called_twice_unchanged(store):
    page = _make_page(_unique_slug())
    assert store.write_page(page) is True
    assert store.write_page(page) is False


def test_write_page_returns_true_when_body_changed(store):
    page = _make_page(_unique_slug())
    store.write_page(page)
    changed = replace(page, body=page.body + "\nMore claims [DOC-002].\n")
    assert store.write_page(changed) is True


def test_append_log_appends_without_rewriting(store):
    marker = uuid.uuid4().hex[:8]
    before = _read_log(store)
    store.append_log(f"entry-one-{marker}")
    store.append_log(f"entry-two-{marker}")
    after = _read_log(store)
    assert after == before + f"entry-one-{marker}\n" + f"entry-two-{marker}\n"
