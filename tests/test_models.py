from datetime import datetime, timezone

import pytest

from core.models import (
    Page,
    SourcePage,
    extract_citations,
    extract_links,
    markdown_to_page,
    markdown_to_source_page,
    page_to_markdown,
    source_page_to_markdown,
)


# ---------------------------------------------------------------------------
# Page round trip
# ---------------------------------------------------------------------------


def test_page_roundtrip_preserves_everything():
    p = Page(
        slug="benchmark-data-contamination",
        title="Benchmark Data Contamination",
        type="concept",
        summary="Test data leaking into training sets: what it does to "
                "leaderboard validity — including the disputed claim that "
                "leakage inflates scores without changing rankings.",
        body="# Title\n\nClaim [DOC-018][DOC-071]. See [[other-page]].\n\n"
             "---\n\nA horizontal rule above.\n",
        updated=datetime(2026, 9, 9, 18, 4, 11, tzinfo=timezone.utc),
        flags=["contested: whether contamination changes relative rankings"],
    )
    assert markdown_to_page(page_to_markdown(p)) == p


def test_empty_flags_omitted_from_output():
    p = Page(
        slug="x",
        title="X",
        type="concept",
        summary="A short summary for testing purposes and nothing else here.",
        body="# X\n\nSome claim [DOC-001].\n",
        updated=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    rendered = page_to_markdown(p)
    assert "flags" not in rendered
    assert markdown_to_page(rendered) == p


def test_summary_containing_colon_roundtrips():
    p = Page(
        slug="x",
        title="X",
        type="concept",
        summary="Scope: this page concerns X, not Y — see the sibling page "
                "for that instead of this one here.",
        body="# X\n\nClaim [DOC-001].\n",
        updated=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert markdown_to_page(page_to_markdown(p)) == p


def test_summary_spanning_multiple_lines_roundtrips():
    p = Page(
        slug="x",
        title="X",
        type="concept",
        summary="First line of the summary.\nSecond line of the summary.\n\n"
                "A second paragraph after a blank line.",
        body="# X\n\nClaim [DOC-001].\n",
        updated=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert markdown_to_page(page_to_markdown(p)) == p


def test_body_containing_horizontal_rule_roundtrips():
    p = Page(
        slug="x",
        title="X",
        type="concept",
        summary="A short summary for testing purposes and nothing else here.",
        body="# X\n\nClaim [DOC-001].\n\n---\n\nMore text after the rule.\n",
        updated=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert markdown_to_page(page_to_markdown(p)) == p


def test_markdown_to_page_raises_on_malformed_input():
    with pytest.raises(ValueError):
        markdown_to_page("not a page at all, no frontmatter here")


def test_markdown_to_page_raises_on_missing_field():
    text = "---\nslug: x\ntitle: X\n---\n\nbody\n"
    with pytest.raises(ValueError):
        markdown_to_page(text)


# ---------------------------------------------------------------------------
# SourcePage round trip
# ---------------------------------------------------------------------------


def test_source_page_roundtrip():
    sp = SourcePage(
        doc_id="DOC-040",
        title="100-LongBench: Are de facto Long-Context Benchmarks Literally Evaluating?",
        source="http://arxiv.org/abs/2505.19293v2",
        published="2025-05-25",
        authors=["Wang Yang", "Hongye Jin"],
        source_type="academic",
        sha256="deadbeef" * 8,
        ingested=datetime(2026, 9, 9, 18, 4, 11, tzinfo=timezone.utc),
        body="Compiled summary of claims.\n\n## Pages affected\n\n"
             "- [[benchmark-data-contamination]] — A (extend)\n",
    )
    assert markdown_to_source_page(source_page_to_markdown(sp)) == sp


def test_markdown_to_source_page_raises_on_malformed_input():
    with pytest.raises(ValueError):
        markdown_to_source_page("nope")


# ---------------------------------------------------------------------------
# extract_citations
# ---------------------------------------------------------------------------


def test_extract_citations_multiple_in_one_spot():
    assert extract_citations("Claim [DOC-041][DOC-112].") == ["DOC-041", "DOC-112"]


def test_extract_citations_deduplicates_preserving_order():
    body = "First [DOC-005]. Then [DOC-002]. Again [DOC-005]."
    assert extract_citations(body) == ["DOC-005", "DOC-002"]


def test_extract_citations_ignores_fenced_code_blocks():
    body = (
        "Real claim [DOC-001].\n\n"
        "```\nfake citation [DOC-999]\n```\n\n"
        "Another real claim [DOC-002].\n"
    )
    assert extract_citations(body) == ["DOC-001", "DOC-002"]


def test_extract_citations_none_present():
    assert extract_citations("No citations here.") == []


# ---------------------------------------------------------------------------
# extract_links
# ---------------------------------------------------------------------------


def test_extract_links_with_display_text_returns_slug_only():
    assert extract_links("See [[other-page|the other page]] for more.") == [
        "other-page"
    ]


def test_extract_links_plain_slug():
    assert extract_links("See [[other-page]].") == ["other-page"]


def test_extract_links_deduplicates_preserving_order():
    body = "See [[b-page]]. Also [[a-page]]. Again [[b-page|B]]."
    assert extract_links(body) == ["b-page", "a-page"]


def test_extract_links_ignores_fenced_code_blocks():
    body = (
        "Real link [[real-page]].\n\n"
        "```\nfake [[fake-page]]\n```\n\n"
        "Another real link [[other-real-page|display]].\n"
    )
    assert extract_links(body) == ["real-page", "other-real-page"]
