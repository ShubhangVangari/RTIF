from datetime import datetime, timezone

from core.check import (
    broken_links,
    check_wiki,
    index_mismatch,
    orphan_pages,
    oversized_pages,
    uncited_sentences,
    untraced_documents,
)
from core.models import Page


# A page with sensible defaults, so each test only has to say what matters.
def make_page(slug: str, body: str = "# Title\n\nA cited claim about this topic [DOC-001].\n") -> Page:
    return Page(
        slug=slug,
        title=slug.replace("-", " ").title(),
        type="concept",
        summary=f"Summary of {slug}.",
        body=body,
        updated=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------
# Check 1: broken links
# ---------------------------------------------------------------


def test_broken_links_reports_a_dangling_link():
    pages = {"page-a": make_page("page-a", body="# Page A\n\nSee [[page-b]] for more [DOC-001].\n")}

    issues = broken_links(pages)

    assert len(issues) == 1
    assert issues[0].kind == "broken_link"
    assert issues[0].page == "page-a"
    assert issues[0].detail == "page-b"
    assert issues[0].severity == "error"


def test_broken_links_reports_nothing_when_every_link_resolves():
    pages = {
        "page-a": make_page("page-a", body="# Page A\n\nSee [[page-b]] for more [DOC-001].\n"),
        "page-b": make_page("page-b"),
    }

    assert broken_links(pages) == []


# ---------------------------------------------------------------
# Check 2: orphan pages
# ---------------------------------------------------------------


def test_orphan_pages_reports_a_page_nothing_links_to():
    pages = {
        "page-a": make_page("page-a", body="# Page A\n\nSee [[page-b]] for more [DOC-001].\n"),
        "page-b": make_page("page-b", body="# Page B\n\nSee [[page-a]] for more [DOC-001].\n"),
        "page-c": make_page("page-c"),  # nothing on any page links here
    }

    issues = orphan_pages(pages)

    assert len(issues) == 1
    assert issues[0].kind == "orphan_page"
    assert issues[0].page == "page-c"
    assert issues[0].severity == "warning"


def test_orphan_pages_reports_nothing_when_every_page_is_linked():
    pages = {
        "page-a": make_page("page-a", body="# Page A\n\nSee [[page-b]] for more [DOC-001].\n"),
        "page-b": make_page("page-b", body="# Page B\n\nSee [[page-a]] for more [DOC-001].\n"),
    }

    assert orphan_pages(pages) == []


# ---------------------------------------------------------------
# Check 3: sentences with no source behind them
# ---------------------------------------------------------------


def test_uncited_sentences_reports_a_sentence_with_no_citation():
    body = "# Title\n\nThis sentence makes a real claim about the topic but has no citation at all.\n"
    pages = {"page-a": make_page("page-a", body=body)}

    issues = uncited_sentences(pages)

    assert len(issues) == 1
    assert issues[0].kind == "uncited_sentence"
    assert issues[0].page == "page-a"
    assert issues[0].severity == "warning"
    assert "no citation at all" in issues[0].detail


def test_uncited_sentences_reports_nothing_when_every_claim_is_cited():
    body = "# Title\n\nThis sentence makes a real claim about the topic and cites its source [DOC-001].\n"
    pages = {"page-a": make_page("page-a", body=body)}

    assert uncited_sentences(pages) == []


def test_uncited_sentences_skips_headings():
    body = (
        "# This Is A Heading That Is Long Enough To Matter For The Length Rule\n\n"
        "A real claim about the topic, properly cited right here [DOC-001].\n"
    )
    pages = {"page-a": make_page("page-a", body=body)}

    assert uncited_sentences(pages) == []


def test_uncited_sentences_skips_link_only_sentences():
    body = (
        "# Title\n\n"
        "A real claim about the topic, properly cited right here [DOC-001]. "
        "See [[some-other-page-with-a-reasonably-long-slug]].\n"
    )
    pages = {"page-a": make_page("page-a", body=body)}

    assert uncited_sentences(pages) == []


def test_uncited_sentences_skips_disagreement_bold_labels():
    body = (
        "# Title\n\n"
        "## Disagreement\n\n"
        "**At issue:** whether this specific claim holds under all conditions tested.\n\n"
        "**Position — the claim holds.** It holds under every condition tested so far [DOC-001].\n\n"
        "**Not in dispute:** that the underlying phenomenon is real and reproducible.\n"
    )
    pages = {"page-a": make_page("page-a", body=body)}

    assert uncited_sentences(pages) == []


def test_uncited_sentences_skips_short_signposts_built_around_a_link():
    body = (
        "# Title\n\n"
        "A real claim about the topic, properly cited right here [DOC-001]. "
        "For methods to find contamination, see [[contamination-detection-methods]].\n"
    )
    pages = {"page-a": make_page("page-a", body=body)}

    assert uncited_sentences(pages) == []


def test_uncited_sentences_treats_a_whole_bullet_as_one_unit():
    # The citation sits at the end of the bullet. Splitting on the internal
    # period would flag the first clause as uncited even though the bullet,
    # taken as a whole, is sourced.
    body = (
        "# Title\n\n"
        "* This bullet makes a claim in its first clause. It backs that claim up "
        "with a source right at the end of the same bullet point [DOC-001].\n"
    )
    pages = {"page-a": make_page("page-a", body=body)}

    assert uncited_sentences(pages) == []


# ---------------------------------------------------------------
# Check 4: documents that left no trace
# ---------------------------------------------------------------


def test_untraced_documents_reports_a_doc_id_cited_nowhere():
    pages = {"page-a": make_page("page-a", body="# A\n\nA claim, cited [DOC-001].\n")}

    issues = untraced_documents(pages, all_doc_ids=["DOC-001", "DOC-002"], excluded_doc_ids=[])

    assert len(issues) == 1
    assert issues[0].kind == "untraced_document"
    assert issues[0].detail == "DOC-002"
    assert issues[0].severity == "error"


def test_untraced_documents_skips_excluded_doc_ids():
    pages = {"page-a": make_page("page-a", body="# A\n\nA claim, cited [DOC-001].\n")}

    issues = untraced_documents(
        pages, all_doc_ids=["DOC-001", "DOC-002"], excluded_doc_ids=["DOC-002"]
    )

    assert issues == []


def test_untraced_documents_reports_nothing_when_every_doc_is_cited():
    pages = {"page-a": make_page("page-a", body="# A\n\nA claim, cited [DOC-001].\n")}

    issues = untraced_documents(pages, all_doc_ids=["DOC-001"], excluded_doc_ids=[])

    assert issues == []


# ---------------------------------------------------------------
# Check 5: index mismatch
# ---------------------------------------------------------------


def test_index_mismatch_reports_a_page_missing_from_the_index():
    pages = {"page-a": make_page("page-a"), "page-b": make_page("page-b")}
    index = "# Index\n\n- [[page-a]] — About page a.\n"

    issues = index_mismatch(pages, index)

    assert len(issues) == 1
    assert issues[0].kind == "index_mismatch"
    assert issues[0].page == "page-b"
    assert issues[0].severity == "error"


def test_index_mismatch_reports_an_index_row_with_no_page_file():
    pages = {"page-a": make_page("page-a")}
    index = "# Index\n\n- [[page-a]] — About page a.\n- [[page-b]] — About page b.\n"

    issues = index_mismatch(pages, index)

    assert len(issues) == 1
    assert issues[0].page == "page-b"


def test_index_mismatch_reports_nothing_when_pages_and_index_agree():
    pages = {"page-a": make_page("page-a")}
    index = "# Index\n\n- [[page-a]] — About page a.\n"

    assert index_mismatch(pages, index) == []


# ---------------------------------------------------------------
# Check 6: oversized pages
# ---------------------------------------------------------------


def test_oversized_pages_reports_a_page_over_the_word_limit():
    body = "# Title\n\n" + ("word " * 50)
    pages = {"page-a": make_page("page-a", body=body)}

    issues = oversized_pages(pages, max_words=40)

    assert len(issues) == 1
    assert issues[0].kind == "oversized_page"
    assert issues[0].page == "page-a"
    assert issues[0].severity == "review"


def test_oversized_pages_reports_nothing_under_the_limit():
    body = "# Title\n\n" + ("word " * 10)
    pages = {"page-a": make_page("page-a", body=body)}

    assert oversized_pages(pages, max_words=40) == []


# ---------------------------------------------------------------
# check_wiki: all six checks run together
# ---------------------------------------------------------------


def test_check_wiki_reports_nothing_for_a_fully_clean_wiki():
    # Both pages must link to each other — otherwise whichever one has no
    # inbound link would itself show up as an orphan.
    pages = {
        "page-a": make_page("page-a", body="# Page A\n\nA cited claim right here [DOC-001][[page-b]].\n"),
        "page-b": make_page("page-b", body="# Page B\n\nA cited claim right here [DOC-002][[page-a]].\n"),
    }
    index = "# Index\n\n- [[page-a]] — About a.\n- [[page-b]] — About b.\n"

    issues = check_wiki(
        pages=pages,
        index=index,
        all_doc_ids=["DOC-001", "DOC-002"],
        excluded_doc_ids=[],
    )

    assert issues == []


def test_check_wiki_collects_issues_from_every_check():
    # page-a has a broken link, an uncited sentence, and is not in the index.
    # page-b is an orphan. DOC-002 is cited nowhere.
    pages = {
        "page-a": make_page(
            "page-a",
            body=(
                "# Page A\n\n"
                "A cited claim right here [DOC-001]. "
                "See [[does-not-exist]].\n\n"
                "This sentence makes a claim with no citation attached to it.\n"
            ),
        ),
        "page-b": make_page("page-b"),
    }
    index = "# Index\n\n- [[page-b]] — About b.\n"

    issues = check_wiki(
        pages=pages,
        index=index,
        all_doc_ids=["DOC-001", "DOC-002"],
        excluded_doc_ids=[],
    )

    kinds_found = {issue.kind for issue in issues}
    assert kinds_found == {
        "broken_link",
        "orphan_page",
        "uncited_sentence",
        "untraced_document",
        "index_mismatch",
    }
