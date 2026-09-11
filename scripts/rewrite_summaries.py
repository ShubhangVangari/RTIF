"""Rewrite every page summary in survey.json against SCHEMA.md §3.

One Gemini call rewrites all summaries at once: §3.1 requires each summary be
written against its confusion set, which is impossible one page at a time.
"""

import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parent.parent
SURVEY_PATH = ROOT / "wiki_local" / "survey.json"
SCHEMA_PATH = ROOT / "SCHEMA.md"

REFERENCE_SLUG = "llm-evaluation-frameworks-and-critiques"
WORD_CEILING = 65
FLAG_WORD_CEILING = 20

PROHIBITED_OPENERS = [
    "Methods for", "Methods and", "Techniques for", "Benchmarks and",
    "Investigating", "Critiques of", "Practical guides", "Analysis of",
    "Adapting", "Strategies for", "Approaches to", "An overview",
    "A survey", "Surveys",
]

CONFUSION_SETS = [
    "llm-as-a-judge-evaluation-and-alignment vs llm-as-a-judge-bias-and-reliability "
    "— the largest collision in the index. The question \"how reliable are LLM "
    "judges?\" currently has no correct destination. Fix that.",
    "benchmark-data-contamination vs contamination-detection-methods",
    "llm-agent-design-patterns, llm-agent-architectures, llm-agent-training-and-"
    "self-improvement, llm-agent-evaluation vs multi-agent-systems",
    "long-context-evaluation vs long-context-training-and-sft vs "
    "context-rot-and-long-context-failure-modes",
    "dense-retrieval vs conversational-dense-retrieval vs "
    "hybrid-and-multi-hop-retrieval vs pseudo-relevance-feedback",
    "advanced-rag-architectures, graph-based-rag, domain-specific-rag, "
    "end-to-end-trainable-rag, rag-chunking-and-preprocessing, rag-vs-long-context",
]

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summaries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["slug", "summary"],
            },
        },
        "flag_payloads": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                    "flag_payload": {"type": "string"},
                },
                "required": ["slug", "flag_payload"],
            },
        },
    },
    "required": ["summaries", "flag_payloads"],
}

SEE_RE = re.compile(r"\bsee\s+[\[`'\"]*([a-z][a-z0-9\-]*)", re.IGNORECASE)


def schema_section_three() -> str:
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    start = text.index("## 3. Summary Requirements")
    end = text.index("## 4. Linking")
    return text[start:end].rstrip().rstrip("-").rstrip()


def word_count(text: str) -> int:
    return len(text.split())


def prohibited_opener(summary: str) -> str | None:
    lowered = summary.lstrip().lower()
    for opener in PROHIBITED_OPENERS:
        if lowered.startswith(opener.lower()):
            return opener
    return None


def build_prompt(clusters: list[dict]) -> str:
    lines = []
    for cluster in clusters:
        marker = "  <-- REFERENCE, RETURN VERBATIM" if cluster["slug"] == REFERENCE_SLUG else ""
        lines.append(
            f"{cluster['slug']} | {cluster['type']} | "
            f"is_contested={str(cluster['is_contested']).lower()} | "
            f"{cluster['contention_notes']} | {cluster['summary']}{marker}"
        )
    pages = "\n\n".join(lines)
    confusion = "\n".join(f"  - {s}" for s in CONFUSION_SETS)
    openers = ", ".join(f'"{o}"' for o in PROHIBITED_OPENERS)

    return f"""You are rewriting the `summary` field for every page in a compiled wiki.

The specification below is SCHEMA.md section 3, verbatim. It is the only
authority on what a summary must be. Follow it exactly.

===== BEGIN SCHEMA.md SECTION 3 =====
{schema_section_three()}
===== END SCHEMA.md SECTION 3 =====

Rewrite all {len(clusters)} summaries at once, as a set. Section 3.1 requires each
summary be written against its confusion set, never in isolation. You are seeing
all of them together for exactly that reason.

HARDEST CONSTRAINT — READ THIS TWICE

No summary may BEGIN with a category noun phrase. These exact openers are
rejected by an automated check, case-insensitive, and the whole batch is thrown
away if even one summary starts with any of them:

{openers}

That means a summary may not start with the bare words "Methods", "Techniques",
"Benchmarks", "Strategies", "Approaches", "Analysis", "Investigating",
"Adapting", "Critiques", "Surveys", "An overview", or "A survey" — in any
grammatical form.

Start with the subject matter itself: the phenomenon, mechanism, failure mode,
object, or question the page is about. The first six words are the most valuable
in the field and must carry the differentiator, because a shared opening makes
siblings look alike at exactly the moment the selector is choosing between them.

Worked transformations:
  REJECTED: "Benchmarks and frameworks for evaluating agents on multi-step tasks..."
  ACCEPTED: "Whether an agent actually completed a multi-step task, and how..."

  REJECTED: "Techniques for segmenting documents before retrieval..."
  ACCEPTED: "Where to cut a document so the retrieved span stays coherent..."

  REJECTED: "Adapting dense retrieval to multi-turn conversational search..."
  ACCEPTED: "Query rewriting and session embedding that carry conversational..."

The reference page below opens "Why static benchmarks fail as evaluation and what
replaces them:" — the subject leads, not the category. Do that {len(clusters)} times.

Before you emit each summary, read its first three words back. If they name a
category of work rather than the subject, rewrite the sentence.

REQUIREMENTS

1. Required content, in section 3.2's order:
   a. Scope — led by the distinguishing feature, not by the category. Not
      "Methods and frameworks for X" but the thing about X that this page and no
      sibling covers.
   b. Boundary — of the form `Concerns <this>; for <that>, see <sibling-slug>.`
      Required wherever a confusion set exists. Name the sibling by its exact
      slug, spelled as it appears in the page list below.
   c. Contention marker — where is_contested is true, a clause stating that the
      corpus disagrees and on what. A reader looking for the disagreement must be
      able to route here from the index alone.

2. Length: target 40-55 words, hard ceiling 65 words.

3. Prohibited by section 3.3:
   - Generic category openers — see the HARDEST CONSTRAINT section above. This
     is the rule most likely to fail; check every summary against it twice.
   - Any digit, or any written-out count of documents, sources, papers, or
     positions.
   - Identifying a page by who wrote its sources ("practitioner guides",
     "academic papers"). Pages are identified by subject.
   - Hedged scope: "various aspects of", "topics including", "and related
     issues".

4. These confusion sets need an explicit boundary stated on BOTH members, not
   just one:
{confusion}

5. Apply the section 3.5 summary test to every summary before you emit it: name
   three questions this page should win, and one question it should lose, naming
   the sibling that should win it. If you cannot name the losing sibling, the
   boundary is undefined and both summaries need rewriting. Do not output the
   test itself — only the summaries it produced.

6. `{REFERENCE_SLUG}` already conforms and is the worked example of what good
   looks like. Return its summary verbatim, completely unchanged.

7. For every cluster with is_contested=true, also produce a `flag_payload`: the
   single proposition at issue, stated once. Under {FLAG_WORD_CEILING} words, one
   clause, no narration, no source names, no digits. The existing
   contention_notes are paragraphs of narration; compress each to its
   proposition. Example shape:
   `whether multi-agent orchestration improves or degrades production reliability`

Every `see <slug>` you write must name one of the {len(clusters)} slugs below, spelled
exactly. Never link a page to itself.

===== PAGES =====
Fields: slug | type | is_contested | contention_notes | current summary

{pages}
"""


def validate(new_summaries: dict, flag_payloads: dict, clusters: list[dict]) -> list[str]:
    violations = []
    slugs = {c["slug"] for c in clusters}
    contested = {c["slug"] for c in clusters if c["is_contested"]}

    if len(new_summaries) != len(clusters):
        violations.append(
            f"[count] expected {len(clusters)} summaries, got {len(new_summaries)}"
        )
    for missing in sorted(slugs - set(new_summaries)):
        violations.append(f"[{missing}] missing from response")
    for unknown in sorted(set(new_summaries) - slugs):
        violations.append(f"[{unknown}] not a slug in survey.json")

    for slug in sorted(set(new_summaries) & slugs):
        summary = new_summaries[slug]
        words = word_count(summary)
        if not summary.strip():
            violations.append(f"[{slug}] empty summary")
        if words > WORD_CEILING:
            violations.append(f"[{slug}] {words} words, ceiling is {WORD_CEILING}")
        if any(ch.isdigit() for ch in summary):
            violations.append(f"[{slug}] contains a digit (count prohibition)")
        opener = prohibited_opener(summary)
        if opener:
            violations.append(f"[{slug}] starts with prohibited opener \"{opener}\"")
        for referenced in SEE_RE.findall(summary):
            if referenced not in slugs:
                violations.append(
                    f"[{slug}] boundary names \"{referenced}\", which is not a page"
                )
            elif referenced == slug:
                violations.append(f"[{slug}] boundary points at itself")

    for missing in sorted(contested - set(flag_payloads)):
        violations.append(f"[{missing}] contested but no flag_payload returned")
    for unknown in sorted(set(flag_payloads) - contested):
        violations.append(f"[{unknown}] flag_payload for a page that is not contested")
    for slug in sorted(set(flag_payloads) & contested):
        payload = flag_payloads[slug]
        words = word_count(payload)
        if not payload.strip():
            violations.append(f"[{slug}] empty flag_payload")
        if words > FLAG_WORD_CEILING:
            violations.append(
                f"[{slug}] flag_payload {words} words, ceiling is {FLAG_WORD_CEILING}"
            )
        if any(ch.isdigit() for ch in payload):
            violations.append(f"[{slug}] flag_payload contains a digit")

    return violations


def main() -> None:
    load_dotenv(ROOT / ".env")
    data = json.loads(SURVEY_PATH.read_text(encoding="utf-8"))
    clusters = data["clusters"]
    originals = {c["slug"]: c["summary"] for c in clusters}

    client = genai.Client(
        vertexai=True,
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location="us-central1",
    )

    print(f"rewriting {len(clusters)} summaries with gemini-2.5-pro...")
    resp = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=build_prompt(clusters),
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        ),
    )

    payload = json.loads(resp.text)
    new_summaries = {e["slug"]: e["summary"].strip() for e in payload["summaries"]}
    flag_payloads = {e["slug"]: e["flag_payload"].strip() for e in payload["flag_payloads"]}

    # The reference page conforms already; pass it through untouched (§3.6).
    if REFERENCE_SLUG in new_summaries:
        new_summaries[REFERENCE_SLUG] = originals[REFERENCE_SLUG]

    violations = validate(new_summaries, flag_payloads, clusters)
    if violations:
        print(f"\n{len(violations)} violation(s), nothing written:\n")
        for v in violations:
            print(f"  {v}")
        sys.exit(1)

    for cluster in clusters:
        slug = cluster["slug"]
        before = originals[slug]
        after = new_summaries[slug]
        print(f"\n=== {slug}")
        print(f"  before [{word_count(before):>2}w]: {before}")
        print(f"  after  [{word_count(after):>2}w]: {after}")
        if cluster["is_contested"]:
            print(f"  flag   [{word_count(flag_payloads[slug]):>2}w]: {flag_payloads[slug]}")
        cluster["summary"] = after
        if cluster["is_contested"]:
            cluster["flag_payload"] = flag_payloads[slug]

    SURVEY_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    before_words = sum(word_count(s) for s in originals.values())
    after_words = sum(word_count(s) for s in new_summaries.values())
    u = resp.usage_metadata
    print(f"\nin {u.prompt_token_count:,} / out {u.candidates_token_count:,} tokens")
    print(f"words: {before_words} -> {after_words} ({after_words - before_words:+d})")
    print(f"flag payloads written: {len(flag_payloads)}")
    print(f"wrote {SURVEY_PATH}")


if __name__ == "__main__":
    main()
