from datetime import datetime, timezone

from core.models import Page
from core.query import FINAL_ANSWER_SCHEMA, answer_question


# ---------------------------------------------------------------
# Small helpers for building fakes. Real tests start further down.
# ---------------------------------------------------------------


# A tiny index with one row per slug, so extract_links can find them.
def make_index(slugs: list[str]) -> str:
    rows = "\n".join(f"- [[{slug}]] — About {slug}." for slug in slugs)
    return f"# Index\n\n{rows}\n"


# A page with sensible defaults, so each test only has to say what matters.
def make_page(slug: str, body: str = "Some body text.") -> Page:
    return Page(
        slug=slug,
        title=slug.replace("-", " ").title(),
        type="concept",
        summary=f"Summary of {slug}.",
        body=body,
        updated=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


# load_page, backed by a plain dictionary instead of a real store.
def make_load_page(pages: dict[str, Page]):
    def load_page(slug: str) -> Page | None:
        return pages.get(slug)

    return load_page


# call_model, backed by a scripted list of replies instead of Gemini. Each
# call pops the next reply off the list and (optionally) records the prompt
# and schema it was given, so a test can check what the model was actually told.
def make_call_model(
    replies: list[dict],
    prompts_seen: list[str] | None = None,
    schemas_seen: list[dict] | None = None,
):
    replies = list(replies)

    def call_model(prompt: str, schema: dict) -> dict:
        if prompts_seen is not None:
            prompts_seen.append(prompt)
        if schemas_seen is not None:
            schemas_seen.append(schema)
        return replies.pop(0)

    return call_model


# ---------------------------------------------------------------
# The tests
# ---------------------------------------------------------------


def test_simple_case_reads_one_page_then_answers():
    page = make_page("dense-retrieval", body="Dense retrieval uses embeddings.")
    call_model = make_call_model(
        [
            {"read_page": "dense-retrieval"},
            {"answer": "Dense retrieval maps text into a shared vector space."},
        ]
    )

    result = answer_question(
        index=make_index(["dense-retrieval"]),
        question="What is dense retrieval?",
        load_page=make_load_page({"dense-retrieval": page}),
        call_model=call_model,
    )

    assert result.pages_read == ["dense-retrieval"]
    assert result.hops == 2
    assert result.text == "Dense retrieval maps text into a shared vector space."
    assert result.trace == ["read dense-retrieval", "answered"]


def test_multi_hop_follows_a_link_to_a_second_page():
    first = make_page("rag-vs-long-context", body="See [[dense-retrieval]] for details.")
    second = make_page("dense-retrieval", body="Dense retrieval details.")
    call_model = make_call_model(
        [
            {"read_page": "rag-vs-long-context"},
            {"read_page": "dense-retrieval"},
            {"answer": "Both pages agree retrieval still matters."},
        ]
    )

    result = answer_question(
        index=make_index(["rag-vs-long-context", "dense-retrieval"]),
        question="Does retrieval still matter with long context?",
        load_page=make_load_page(
            {"rag-vs-long-context": first, "dense-retrieval": second}
        ),
        call_model=call_model,
    )

    assert result.pages_read == ["rag-vs-long-context", "dense-retrieval"]
    assert result.hops == 3
    assert result.trace == [
        "read rag-vs-long-context",
        "read dense-retrieval",
        "answered",
    ]


def test_missing_page_does_not_crash_and_model_is_told():
    prompts_seen: list[str] = []
    call_model = make_call_model(
        [
            {"read_page": "nonexistent-page"},
            {"answer": "The wiki does not cover this."},
        ],
        prompts_seen=prompts_seen,
    )

    result = answer_question(
        index=make_index(["dense-retrieval"]),
        question="What about quantum computing?",
        load_page=make_load_page({}),  # nothing exists
        call_model=call_model,
    )

    # It did not crash, and the missing page was never counted as read.
    assert result.pages_read == []
    assert result.hops == 2
    assert result.text == "The wiki does not cover this."

    # The next prompt told the model plainly that the page does not exist,
    # and reminded it what does.
    second_prompt = prompts_seen[1]
    assert "no page called 'nonexistent-page'" in second_prompt
    assert "dense-retrieval" in second_prompt

    # The trace shows the failed attempt, not just the eventual answer.
    assert result.trace == ["asked for nonexistent-page — no such page", "answered"]


def test_duplicate_page_request_sends_the_body_only_once():
    page = make_page("dense-retrieval", body="UNIQUE_BODY_MARKER_12345")
    prompts_seen: list[str] = []
    call_model = make_call_model(
        [
            {"read_page": "dense-retrieval"},
            {"read_page": "dense-retrieval"},  # asks again
            {"answer": "done"},
        ],
        prompts_seen=prompts_seen,
    )

    result = answer_question(
        index=make_index(["dense-retrieval"]),
        question="Tell me about dense retrieval.",
        load_page=make_load_page({"dense-retrieval": page}),
        call_model=call_model,
    )

    # Only one entry in pages_read, even though it was asked for twice.
    assert result.pages_read == ["dense-retrieval"]

    # The body text appears exactly once in the final prompt, not twice.
    final_prompt = prompts_seen[2]
    assert final_prompt.count("UNIQUE_BODY_MARKER_12345") == 1

    # The trace records the repeat request, not just the one real read.
    assert result.trace == [
        "read dense-retrieval",
        "asked for dense-retrieval again — already read",
        "answered",
    ]


def test_hop_cap_stops_the_loop_and_tries_once_more():
    schemas_seen: list[dict] = []
    # This model never answers, not even on the forced final try — it keeps
    # trying to ask for a page even once that option has been taken away.
    call_model = make_call_model(
        [
            {"read_page": "dense-retrieval"},
            {"read_page": "dense-retrieval"},
            {"read_page": "dense-retrieval"},
            {"read_page": "dense-retrieval"},
        ],
        schemas_seen=schemas_seen,
    )

    result = answer_question(
        index=make_index(["dense-retrieval"]),
        question="Tell me everything.",
        load_page=make_load_page({"dense-retrieval": make_page("dense-retrieval")}),
        call_model=call_model,
        max_hops=3,
    )

    # 3 hops in the loop, plus exactly 1 forced final try. It never asks a 5th time.
    assert result.hops == 4
    assert result.text == ""
    assert result.trace == [
        "read dense-retrieval",
        "asked for dense-retrieval again — already read",
        "asked for dense-retrieval again — already read",
        "hit the 3-hop limit — asking once more",
        "still no answer",
    ]

    # The first 3 calls could still ask for a page. The 4th — the forced
    # final try — could not, at the schema level, not just by instruction.
    assert schemas_seen[0] != FINAL_ANSWER_SCHEMA
    assert "read_page" in schemas_seen[0]["properties"]
    assert schemas_seen[3] == FINAL_ANSWER_SCHEMA
    assert "read_page" not in schemas_seen[3]["properties"]


def test_second_repeat_of_the_same_page_escalates_to_a_direct_command():
    page = make_page("dense-retrieval")
    prompts_seen: list[str] = []
    call_model = make_call_model(
        [
            {"read_page": "dense-retrieval"},  # read
            {"read_page": "dense-retrieval"},  # 1st repeat: plain notice
            {"read_page": "dense-retrieval"},  # 2nd repeat: escalate
            {"answer": "done"},
        ],
        prompts_seen=prompts_seen,
    )

    answer_question(
        index=make_index(["dense-retrieval"]),
        question="Tell me about dense retrieval.",
        load_page=make_load_page({"dense-retrieval": page}),
        call_model=call_model,
    )

    # Sent right after the 1st repeat: a plain notice, no command to answer.
    after_first_repeat = prompts_seen[2]
    assert "You already have the 'dense-retrieval' page" in after_first_repeat
    assert "answer now" not in after_first_repeat.lower()

    # Sent right after the 2nd repeat: escalated to a direct command.
    after_second_repeat = prompts_seen[3]
    assert "answer now" in after_second_repeat.lower()
