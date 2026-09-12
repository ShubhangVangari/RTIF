from datetime import datetime, timezone

import pytest

from core.compile import CompileError, compile_document, document_sha256
from core.models import Page

RAW_DOC = """---
title: Contamination Inflates Scores But Rarely Reorders Models
source: http://arxiv.org/abs/2506.00001v1
published: 2026-01-15
authors: A Researcher, B Researcher
type: arxiv
---

# Contamination Inflates Scores But Rarely Reorders Models

Leakage raises absolute scores but leaves relative rankings largely intact.
"""


def make_index(slugs: list[str]) -> str:
    rows = "\n".join(f"- [[{slug}]] — A summary for {slug}." for slug in slugs)
    return f"# Index\n\n{rows}\n"


def make_page(slug: str, body: str = "", flags: list[str] | None = None) -> Page:
    return Page(
        slug=slug,
        title=slug.replace("-", " ").title(),
        type="concept",
        summary=f"A seeded summary for {slug} that discriminates it from siblings.",
        body=body,
        updated=datetime(2026, 1, 1, tzinfo=timezone.utc),
        flags=list(flags or []),
    )


def make_load_page(pages: dict[str, Page]):
    def load_page(slug: str) -> Page | None:
        return pages.get(slug)

    return load_page


def make_call_model(stage1: dict, stage2: dict, prompts: list | None = None):
    """Fake model. Stage 1 and 2 are told apart by their response schema."""

    def call_model(prompt: str, schema: dict) -> dict:
        if prompts is not None:
            prompts.append(prompt)
        if "slugs" in schema.get("properties", {}):
            return dict(stage1)
        return dict(stage2)

    return call_model


def test_path_a_extend_updates_body_and_returns_one_decision():
    page = make_page(
        "benchmark-data-contamination",
        body="# Benchmark Data Contamination\n\nLeakage occurs [DOC-018].\n",
    )
    new_body = (
        "# Benchmark Data Contamination\n\nLeakage occurs [DOC-018]. Absolute "
        "scores rise while rankings hold [DOC-041]. See "
        "[[contamination-detection-methods]].\n"
    )
    result = compile_document(
        schema="# SCHEMA",
        index=make_index(["benchmark-data-contamination", "contamination-detection-methods"]),
        doc_id="DOC-041",
        document=RAW_DOC,
        load_page=make_load_page({"benchmark-data-contamination": page}),
        call_model=make_call_model(
            {"slugs": ["benchmark-data-contamination"]},
            {
                "decisions": [
                    {
                        "path": "A",
                        "page": "benchmark-data-contamination",
                        "why": "Adds evidence on rank stability under leakage.",
                        "classification": "scope-difference",
                    }
                ],
                "pages": [
                    {
                        "slug": "benchmark-data-contamination",
                        "body": new_body,
                        "summary": page.summary,
                        "flags": [],
                    }
                ],
                "source_page_body": (
                    "# Contamination Inflates Scores\n\nSummary.\n\n"
                    "## Pages affected\n\n- [[benchmark-data-contamination]] — A (extend)\n"
                ),
            },
        ),
    )

    assert len(result.decisions) == 1
    decision = result.decisions[0]
    assert decision.path == "A"
    assert decision.page == "benchmark-data-contamination"
    assert decision.classification == "scope-difference"

    assert len(result.pages) == 1
    assert result.pages[0].body == new_body
    assert "DOC-041" in result.pages[0].body
    assert result.pages[0].title == page.title
    assert result.pages[0].flags == []


def test_path_c_produces_contested_flag_and_disagreement_section():
    page = make_page(
        "benchmark-data-contamination",
        body="# Benchmark Data Contamination\n\nLeakage invalidates scores [DOC-018].\n",
    )
    contested_body = (
        "# Benchmark Data Contamination\n\n"
        "Leakage invalidates scores [DOC-018].\n\n"
        "## Disagreement\n\n"
        "**At issue:** Whether contamination changes relative model rankings.\n\n"
        "**Position — contamination invalidates comparison.** Leaderboard scores "
        "become unusable for comparing models [DOC-018].\n\n"
        "**Position — ranking is robust to leakage.** Contamination inflates "
        "absolute scores but rarely reorders models [DOC-041].\n\n"
        "**Not in dispute:** That contamination occurs and inflates absolute scores.\n"
    )
    result = compile_document(
        schema="# SCHEMA",
        index=make_index(["benchmark-data-contamination"]),
        doc_id="DOC-041",
        document=RAW_DOC,
        load_page=make_load_page({"benchmark-data-contamination": page}),
        call_model=make_call_model(
            {"slugs": ["benchmark-data-contamination"]},
            {
                "decisions": [
                    {
                        "path": "C",
                        "page": "benchmark-data-contamination",
                        "why": "Opens a new position on rank stability.",
                        "classification": "contradiction",
                        "at_issue": "whether contamination changes relative model rankings",
                        "new_position": "ranking is robust to leakage",
                        "positions": [
                            {
                                "label": "contamination invalidates comparison",
                                "sources": ["DOC-018"],
                            },
                            {
                                "label": "ranking is robust to leakage",
                                "sources": ["DOC-041"],
                            },
                        ],
                    }
                ],
                "pages": [
                    {
                        "slug": "benchmark-data-contamination",
                        "body": contested_body,
                        "summary": page.summary,
                        "flags": [
                            "contested: whether contamination changes relative model rankings"
                        ],
                    }
                ],
                "source_page_body": (
                    "# Contamination Inflates Scores\n\nSummary.\n\n## Pages affected\n\n"
                    "- [[benchmark-data-contamination]] — C (contradiction: rank stability)\n"
                ),
            },
        ),
    )

    decision = result.decisions[0]
    assert decision.path == "C"
    assert decision.classification == "contradiction"
    assert decision.at_issue == "whether contamination changes relative model rankings"
    assert decision.new_position == "ranking is robust to leakage"
    assert decision.aligns_with is None
    assert len(decision.positions) == 2

    compiled = result.pages[0]
    assert any(flag.startswith("contested: ") for flag in compiled.flags)
    assert "## Disagreement" in compiled.body
    assert "**At issue:**" in compiled.body
    assert "**Not in dispute:**" in compiled.body


def test_scope_difference_takes_path_a_and_adds_no_contested_flag():
    """§5.1: a document answering a different question is not a contradiction.

    Recording one as a contradiction manufactures a dispute the corpus does not
    contain, which is what makes contested pages worthless.
    """
    page = make_page(
        "multi-agent-systems",
        body="# Multi-Agent Systems\n\nAgents collaborate on complex tasks [DOC-002].\n",
        flags=["contested: whether running multiple agents improves or degrades production reliability"],
    )
    extended_body = (
        "# Multi-Agent Systems\n\nAgents collaborate on complex tasks [DOC-002]. "
        "Where the team is flat and shares one base model, performance does not "
        "improve; where it is hierarchical with structured feedback, it does "
        "[DOC-041].\n"
    )
    reasoning = (
        "P is whether running multiple agents improves or degrades production "
        "reliability. This document asserts that hierarchical teams with "
        "structured feedback outperform flat ones, which answers how to build "
        "such a system rather than whether it works. Both positions survive it, "
        "so it is a scope difference, not not-P."
    )
    result = compile_document(
        schema="# SCHEMA",
        index=make_index(["multi-agent-systems"]),
        doc_id="DOC-041",
        document=RAW_DOC,
        load_page=make_load_page({"multi-agent-systems": page}),
        call_model=make_call_model(
            {"slugs": ["multi-agent-systems"]},
            {
                "decisions": [
                    {
                        "path": "A",
                        "page": "multi-agent-systems",
                        "why": "States the condition under which each claim holds.",
                        "classification": "scope-difference",
                        "classification_reasoning": reasoning,
                    }
                ],
                "pages": [
                    {
                        "slug": "multi-agent-systems",
                        "body": extended_body,
                        "summary": page.summary,
                        "flags": list(page.flags),
                    }
                ],
                "source_page_body": (
                    "# Doc\n\nS.\n\n## Pages affected\n\n"
                    "- [[multi-agent-systems]] — A (extend)\n"
                ),
            },
        ),
    )

    decision = result.decisions[0]
    assert decision.path == "A"
    assert decision.classification == "scope-difference"
    assert decision.classification_reasoning == reasoning
    assert decision.at_issue is None
    assert decision.positions is None

    compiled = result.pages[0]
    # The pre-existing contested flag is carried through untouched; a scope
    # difference must not introduce a new one, and §5.3 makes contested terminal.
    assert compiled.flags == page.flags
    assert "## Disagreement" not in compiled.body
    assert "[DOC-041]" in compiled.body


def test_source_page_is_built_from_raw_frontmatter():
    result = compile_document(
        schema="# SCHEMA",
        index=make_index(["benchmark-data-contamination"]),
        doc_id="DOC-041",
        document=RAW_DOC,
        load_page=make_load_page({"benchmark-data-contamination": make_page("benchmark-data-contamination")}),
        call_model=make_call_model(
            {"slugs": ["benchmark-data-contamination"]},
            {
                "decisions": [
                    {
                        "path": "A",
                        "page": "benchmark-data-contamination",
                        "why": "Extends.",
                        "classification": "scope-difference",
                    }
                ],
                "pages": [],
                "source_page_body": "# Doc\n\nSummary.\n\n## Pages affected\n\n- [[benchmark-data-contamination]] — A\n",
            },
        ),
    )

    source = result.source_page
    assert source.doc_id == "DOC-041"
    assert source.title == "Contamination Inflates Scores But Rarely Reorders Models"
    assert source.source == "http://arxiv.org/abs/2506.00001v1"
    assert source.published == "2026-01-15"
    assert source.authors == ["A Researcher", "B Researcher"]
    assert source.source_type == "academic"
    assert source.sha256 == document_sha256(RAW_DOC)


def test_source_page_survives_a_colon_and_latex_in_the_title():
    """Nearly half the corpus has frontmatter that yaml.safe_load rejects."""
    document = RAW_DOC.replace(
        "title: Contamination Inflates Scores But Rarely Reorders Models",
        "title: 360$^\\circ$REA: Towards A Reusable Experience Accumulation",
    )
    result = _compile_minimal(document)

    assert result.source_page.title == "360$^\\circ$REA: Towards A Reusable Experience Accumulation"
    assert result.source_page.source == "http://arxiv.org/abs/2506.00001v1"
    assert result.source_page.source_type == "academic"
    assert result.source_page.authors == ["A Researcher", "B Researcher"]


def test_web_documents_are_practitioner_and_unknown_authors_are_empty():
    document = RAW_DOC.replace("type: arxiv", "type: web").replace(
        "authors: A Researcher, B Researcher", "authors: unknown"
    )
    result = _compile_minimal(document)

    assert result.source_page.source_type == "practitioner"
    assert result.source_page.authors == []


@pytest.mark.parametrize(
    "document, expected",
    [
        (RAW_DOC.replace("type: arxiv", "type: newsletter"), "unknown type"),
        (RAW_DOC.replace("type: arxiv", ""), "no 'type'"),
        (
            RAW_DOC.replace(
                "title: Contamination Inflates Scores But Rarely Reorders Models", ""
            ),
            "no 'title'",
        ),
    ],
)
def test_unusable_raw_frontmatter_raises_rather_than_guessing(document, expected):
    with pytest.raises(CompileError) as excinfo:
        _compile_minimal(document)
    assert expected in str(excinfo.value)


def _compile_minimal(document: str):
    """Compile `document` through a fake that takes one Path A on `real-page`."""
    return compile_document(
        schema="# SCHEMA",
        index=make_index(["real-page"]),
        doc_id="DOC-041",
        document=document,
        load_page=make_load_page({"real-page": make_page("real-page")}),
        call_model=make_call_model(
            {"slugs": ["real-page"]},
            {
                "decisions": [
                    {
                        "path": "A",
                        "page": "real-page",
                        "why": "Extends.",
                        "classification": "scope-difference",
                    }
                ],
                "pages": [],
                "source_page_body": "# Doc\n\nS.\n\n## Pages affected\n\n- [[real-page]] — A\n",
            },
        ),
    )


def test_summary_links_are_stripped_to_bare_slugs():
    """§4.1: links never appear in frontmatter, and a summary is frontmatter."""
    result = compile_document(
        schema="# SCHEMA",
        index=make_index(["real-page", "sibling-page"]),
        doc_id="DOC-041",
        document=RAW_DOC,
        load_page=make_load_page({"real-page": make_page("real-page")}),
        call_model=make_call_model(
            {"slugs": ["real-page"]},
            {
                "decisions": [
                    {
                        "path": "A",
                        "page": "real-page",
                        "why": "Extends.",
                        "classification": "scope-difference",
                    }
                ],
                "pages": [
                    {
                        "slug": "real-page",
                        "body": "# Real Page\n\nClaim [DOC-041].\n",
                        "summary": "Concerns this; for that, see [[sibling-page]] and [[other|display]].",
                        "flags": [],
                    }
                ],
                "source_page_body": "# Doc\n\nS.\n\n## Pages affected\n\n- [[real-page]] — A\n",
            },
        ),
    )

    summary = result.pages[0].summary
    assert "[[" not in summary
    assert "see sibling-page and other." == summary.split("for that, ")[1]


def test_reingesting_the_same_document_hash_returns_an_empty_result():
    calls = []

    def call_model(prompt, schema):
        calls.append(prompt)
        raise AssertionError("the model must not be called for an ingested document")

    digest = document_sha256(RAW_DOC)
    log = (
        "## [2026-09-11] ingest | DOC-041 | Contamination and ranking\n"
        f"- sha256: {digest}\n"
        "- A benchmark-data-contamination — extended\n"
    )
    result = compile_document(
        schema="# SCHEMA",
        index=make_index(["benchmark-data-contamination"]),
        doc_id="DOC-041",
        document=RAW_DOC,
        load_page=make_load_page({}),
        call_model=call_model,
        log=log,
    )

    assert result.decisions == []
    assert result.pages == []
    assert result.source_page is None
    assert result.tokens_in == 0 and result.tokens_out == 0
    assert calls == []


def test_stage_one_caps_page_selection_at_eight():
    slugs = [f"concept-page-{i:02d}" for i in range(20)]
    prompts: list[str] = []
    pages = {slug: make_page(slug) for slug in slugs}

    compile_document(
        schema="# SCHEMA",
        index=make_index(slugs),
        doc_id="DOC-041",
        document=RAW_DOC,
        load_page=make_load_page(pages),
        call_model=make_call_model(
            {"slugs": slugs},
            {
                "decisions": [
                    {
                        "path": "A",
                        "page": slugs[0],
                        "why": "Extends.",
                        "classification": "scope-difference",
                    }
                ],
                "pages": [],
                "source_page_body": f"# Doc\n\nSummary.\n\n## Pages affected\n\n- [[{slugs[0]}]] — A\n",
            },
            prompts=prompts,
        ),
    )

    stage2_prompt = prompts[1]
    assert stage2_prompt.count("--- PAGE: ") == 8
    for slug in slugs[:8]:
        assert f"--- PAGE: {slug}\n" in stage2_prompt
    assert "--- PAGE: concept-page-08\n" not in stage2_prompt


def test_stage_one_drops_slugs_that_are_not_in_the_index():
    prompts: list[str] = []
    compile_document(
        schema="# SCHEMA",
        index=make_index(["real-page"]),
        doc_id="DOC-041",
        document=RAW_DOC,
        load_page=make_load_page({"real-page": make_page("real-page")}),
        call_model=make_call_model(
            {"slugs": ["invented-page", "real-page"]},
            {
                "decisions": [
                    {
                        "path": "A",
                        "page": "real-page",
                        "why": "Extends.",
                        "classification": "scope-difference",
                    }
                ],
                "pages": [],
                "source_page_body": "# Doc\n\nSummary.\n\n## Pages affected\n\n- [[real-page]] — A\n",
            },
            prompts=prompts,
        ),
    )

    stage2_prompt = prompts[1]
    assert "--- PAGE: real-page\n" in stage2_prompt
    assert "--- PAGE: invented-page\n" not in stage2_prompt


@pytest.mark.parametrize(
    "stage2, expected",
    [
        ({"pages": [], "source_page_body": "x"}, "no decisions"),
        (
            {"decisions": [], "pages": [], "source_page_body": "x"},
            "no decisions",
        ),
        (
            {
                "decisions": [{"path": "Z", "page": "p", "why": "w", "classification": "contradiction"}],
                "pages": [],
                "source_page_body": "x",
            },
            "expected A, B or C",
        ),
        (
            {
                "decisions": [{"path": "A", "page": "p", "classification": "contradiction"}],
                "pages": [],
                "source_page_body": "x",
            },
            "missing 'why'",
        ),
        (
            {
                "decisions": [{"path": "A", "page": "p", "why": "w", "classification": "scope-difference"}],
                "pages": [{"slug": "p", "summary": "s", "flags": []}],
                "source_page_body": "x",
            },
            "has no body",
        ),
        (
            {
                "decisions": [{"path": "A", "page": "p", "why": "w", "classification": "scope-difference"}],
                "pages": [],
                "source_page_body": "",
            },
            "no source_page_body",
        ),
    ],
)
def test_malformed_model_output_raises(stage2, expected):
    with pytest.raises(CompileError) as excinfo:
        compile_document(
            schema="# SCHEMA",
            index=make_index(["real-page"]),
            doc_id="DOC-041",
            document=RAW_DOC,
            load_page=make_load_page({"real-page": make_page("real-page")}),
            call_model=make_call_model({"slugs": ["real-page"]}, stage2),
        )
    assert "DOC-041" in str(excinfo.value)
    assert expected in str(excinfo.value)


def test_stage_one_malformed_output_raises():
    with pytest.raises(CompileError) as excinfo:
        compile_document(
            schema="# SCHEMA",
            index=make_index(["real-page"]),
            doc_id="DOC-041",
            document=RAW_DOC,
            load_page=make_load_page({}),
            call_model=make_call_model({"pages": []}, {}),
        )
    assert "DOC-041: stage 1 returned no 'slugs' list" in str(excinfo.value)


def test_model_exception_is_wrapped_with_doc_id_and_stage():
    def call_model(prompt, schema):
        raise RuntimeError("503 backend unavailable")

    with pytest.raises(CompileError) as excinfo:
        compile_document(
            schema="# SCHEMA",
            index=make_index(["real-page"]),
            doc_id="DOC-041",
            document=RAW_DOC,
            load_page=make_load_page({}),
            call_model=call_model,
        )
    message = str(excinfo.value)
    assert "DOC-041" in message and "stage 1" in message and "503" in message


def test_new_page_takes_its_title_from_the_body_h1_and_reports_usage():
    result = compile_document(
        schema="# SCHEMA",
        index=make_index(["existing-page"]),
        doc_id="DOC-041",
        document=RAW_DOC,
        load_page=make_load_page({"existing-page": make_page("existing-page")}),
        call_model=make_call_model(
            {"slugs": ["existing-page"], "_usage": {"tokens_in": 100, "tokens_out": 20}},
            {
                "decisions": [
                    {
                        "path": "B",
                        "page": "rank-stability-under-leakage",
                        "why": "No page covers rank stability.",
                        "classification": "contradiction",
                    }
                ],
                "pages": [
                    {
                        "slug": "rank-stability-under-leakage",
                        "body": "# Rank Stability Under Leakage\n\nClaim [DOC-041].\n",
                        "summary": "Whether leakage reorders models rather than only inflating scores.",
                        "flags": ["non-seed"],
                    }
                ],
                "source_page_body": "# Doc\n\nS.\n\n## Pages affected\n\n- [[rank-stability-under-leakage]] — B\n",
                "_usage": {"tokens_in": 900, "tokens_out": 300},
            },
        ),
    )

    created = result.pages[0]
    assert created.title == "Rank Stability Under Leakage"
    assert created.type == "concept"
    assert created.flags == ["non-seed"]
    assert result.tokens_in == 1000
    assert result.tokens_out == 320
