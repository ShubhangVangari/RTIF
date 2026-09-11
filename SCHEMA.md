# SCHEMA.md

The language specification for this wiki.

This is the only human-authored file in the system. Everything in `wiki/` is
generated. The LLM is the compiler, `raw/` is source, `wiki/` is build output,
`check.py` is the test suite, and queries are runtime.

Every ingest is an independent LLM call with no memory of any other. This file
is the only thing that makes 218 independent calls produce one coherent wiki
rather than 218 improvised formats. Where it is ambiguous, the compiler will
improvise, and the improvisations will not agree with each other. Prefer an
over-specified rule to a tasteful one.

## 0. Layout

```
raw/                      223 immutable source documents. Read, never written.
wiki/
  index.md                Routing catalog. Concept and entity pages ONLY.
  log.md                  Append-only ledger of every operation.
  concepts/<slug>.md      Concept pages. Seeded from the survey taxonomy.
  entities/<slug>.md      Entity pages. Empty by construction — see §2.2.
  sources/<DOC-ID>.md     One page per ingested document. NEVER in index.md.
SCHEMA.md                 This file.
```

`wiki/sources/` is deliberately outside the routing catalog. Adding ~220 source
pages to `index.md` would multiply the per-query index budget by roughly seven
and degrade page selection (§3.4). Source pages are reached by following
citations, never by selection.

`wiki/entities/` exists as a slot in the layout and is empty. This is a
deliberate scope decision, not an unimplemented feature (§2.2).

---

## 1. Page Anatomy

### 1.1 Concept page frontmatter

Frontmatter contains only fields that cannot be derived from the body.

```yaml
---
slug: benchmark-data-contamination
title: Benchmark Data Contamination
type: concept
summary: >-
  Test data leaking into training sets: how it happens, what it does to
  leaderboard validity, and contamination-free benchmark design — including the
  disputed claim that leakage inflates absolute scores without changing model
  rankings. Concerns the problem; for how it is measured, see
  contamination-detection-methods.
updated: 2026-09-09T18:04:11Z
flags:
  - "contested: whether contamination changes relative model rankings"
---
```

- `slug` — lowercase, hyphenated, matches the filename. Immutable once created.
- `title` — human-readable. Rendered in the UI; not used for selection.
- `type` — `concept` on every page. The compiler may not emit any other value.
- `summary` — see §3. This field determines whether the page is reachable.
- `updated` — ISO 8601 UTC. Written only when the body actually changed; the
  store compares a hash of the rendered body against the stored object and
  skips the write entirely when they match. The compiler emits a page on every
  ingest; the store decides whether anything happened. This keeps every object
  version in the bucket a real diff.
- `flags` — see §1.6. Omit the key when empty; never write `flags: []`.

**Not in frontmatter, by design:** `sources`, `links`, and any count. All are
derivable from the body and would drift if copied (§2.5). `check.py` derives
sources from inline `[DOC-xxx]` citations; `core/graph.py` derives edges from
inline `[[slug]]` links. Frontmatter contains only what cannot be derived.

### 1.2 Concept page body

```markdown
# Benchmark Data Contamination

Opening paragraph: what this concept is and what falls inside its scope.
Every claim carries a citation [DOC-018].

## <Thematic heading>

Claims grouped by theme, never by source document. A paragraph synthesising two
sources cites both [DOC-018][DOC-071]. See [[contamination-detection-methods]]
for how leakage is measured.

## Disagreement

Present only when the page carries a `contested` flag. Format per §5.
```

Rules:

- Exactly one `#` H1, matching `title`.
- `##` for body sections. `###` is permitted where a page genuinely needs
  sub-structure; `check.py` reports its use as a review trigger, not a failure.
- Every body paragraph contains at least one `[DOC-xxx]` citation (§6).
- No `## Sources` or `## References` section. Citations are inline only.
- No sentence stating a count of anything the system can compute (§2.5).
- If present, `## Disagreement` is the final section.

### 1.3 Source pages

One file per ingested document at `wiki/sources/<DOC-ID>.md`.

```yaml
---
doc_id: DOC-040
title: <exact title from the raw document frontmatter>
source: <url or citation from raw frontmatter>
published: <date from raw frontmatter>
authors: [<from raw frontmatter>]
source_type: academic | practitioner
sha256: <content hash, matching doc_map.json>
ingested: 2026-09-09T18:04:11Z
---
```

Body: a compiled summary of the document in its own terms, followed by an
explicit list of every concept page this document affected, as `[[slug]]`
links, each with the path taken.

```markdown
# <title>

Compiled summary of what this document claims and argues.

## Pages affected

- [[benchmark-data-contamination]] — C (contradiction: opened a new position on
  whether leakage changes relative rankings)
- [[contamination-detection-methods]] — A (extend)
```

**Bidirectional provenance.** Concept pages cite documents; source pages link
to concept pages. This is the same relation stored twice, which §2.5 otherwise
forbids. The exception is deliberate and narrow: **redundancy that `check.py`
verifies is not drift; redundancy that nothing verifies is.** The second copy
exists precisely so the two can be compared, and disagreement between them is a
compile error that no other check would catch (§6.3).

Source pages are never linked from concept page prose and never appear in
`index.md`. They are reached through citations only.

### 1.4 index.md

Generated, never hand-edited. One row per concept and entity page.

```markdown
# Index

- [[benchmark-data-contamination]] — <summary text, verbatim from frontmatter>
- [[contamination-detection-methods]] — <summary text, verbatim from frontmatter>
```

The index contains no counts, no dates, no source tallies, no grouping by
category (§2.5). It contains slugs and summaries. Anything else is spending the
per-query token budget on text that does not help selection.

### 1.5 log.md

Append-only. One entry per operation, never rewritten, never reordered.

Each entry begins with a fixed prefix so the log is parseable with `grep`
without a parser:

```
## [2026-09-09] ingest | DOC-040 | Benchmark contamination and leaderboard ranking
- C benchmark-data-contamination — new position on rank stability
- A contamination-detection-methods — extended
```

Prefix format: `## [YYYY-MM-DD] <operation> | <subject>`, where operation is
`ingest`, `query`, or `check`. `grep "^## \[" log.md | tail -20` must return a
readable history.

### 1.6 Flags

A flag is a string of the form `kind: payload`. Two families, and the
distinction is load-bearing.

**Terminal** — a recorded, finished state. Not a defect. `check.py` ignores it.

- `contested: <the proposition at issue>` — the corpus genuinely disagrees
  here (§5). This is the correct permanent end state for a contested page and
  is never resolved by the compiler.

**Defect** — something is wrong; a human should look. `check.py` reports it.

- `unresolved: <what>` — a claim the compiler could not attribute or place.
- `oversized: <why>` — the page exceeds the review threshold but cannot be
  split without producing sub-floor pages (§2.3).
- `non-seed` — the page was created during ingest rather than seeded from the
  survey. Every such page is reviewed.

The two families exist because a contested page is the system working as
designed. A check that fails permanently on `multi-agent-systems` is a check
nobody reads.

---

## 2. What Earns a Page

`index.md` is read in full on every query and is the only mechanism by which a
page can be selected. Page granularity is therefore governed by retrieval
economics, not by how the subject matter is best organised. Too many pages
degrade selection; too few degrade precision inside the selected page.

### 2.1 The three ingest paths

For each concept a document touches, the compiler takes exactly one path.
Paths are ordered by cost. Take the earliest applicable path.

**Path A — extend.** A page for the concept exists and the document's claims
are compatible with what it already says. Add the claims, grouped into existing
thematic sections where possible.

**Path C — flag.** A page exists and the document contradicts it. Record both
positions with attribution, add or update the `contested` flag, and do not
resolve. See §5. Path C modifies the page without asserting new consensus.

**Path B — create.** No page covers the concept and the floor in §2.2 is met.
Write the page, write its summary against its confusion set (§3.1), add inbound
links from related pages, add the index row, and flag it `non-seed`.

Extending changes one page. Creating changes the index, which every future
query must read, and forces the summaries of neighbouring pages to be revised
(§3.6). When in doubt, extend.

Linking is not a path. Every path carries the linking obligations in §4.

### 2.2 The floor — minimum evidence for a page

**Concept pages** require **two independent sources**. A concept attested by
exactly one document is more likely that document's private vocabulary than a
feature of the corpus.

Independence means two distinct files in `raw/`. Two arXiv abstracts covering
the same work by the same authors are one source.

**Entity pages may only exist if present in the seed taxonomy.** The seed
contains none, so **the compiler may never create an entity page.** A named
model, benchmark, dataset, or system encountered during ingest is recorded on
the nearest concept page, never given its own.

The reasoning: an entity's identity does not depend on how many sources discuss
it, but its relevance to *this* wiki does. A one-source floor for entities would
regenerate exactly the noise removed during the survey — papers introducing a
model, which nobody would look up in a wiki about LLM systems.

This makes the wiki structurally weak at entity lookup ("what does paper X
say", "what is system Y"). That is a known and accepted cost, measured
explicitly in the evaluation harness rather than left to be discovered.

### 2.3 The ceiling — when a page splits

A page splits for exactly one reason: **it is doing more than one job.** The
test is the summary test (§3.5):

> If you cannot write a one-line summary that distinguishes this page from
> every sibling in the index without using "and", the page is doing two jobs.

Size is a review trigger, not a reason. `check.py` reports any page over
**2,000 words** of body prose so a human looks at it. *(Placeholder. Set from
observed page lengths during M5; do not ship an unmeasured number.)* A large
page that passes the summary test stays large. A short page that fails it
splits.

**A split is only valid if every resulting page independently meets the floor
in §2.2.** A split producing a single-source page is not a split; it is
fragmentation. Where a page cannot be split without producing sub-floor pages,
leave it intact and raise `oversized:<reason>`.

### 2.4 Disagreement is never a reason to split

Where sources contradict each other on a concept, the disagreement is a
property of that concept's page. It never becomes a page of its own, and it
never justifies splitting the page into per-position pages.

A separate page for the counterargument reproduces exactly the failure this
system exists to avoid: the objection is present in the corpus but never
surfaces for a reader who asks the question. The page named for the concept
carries every position on it.

This rule has no exceptions.

### 2.5 Pages document shape, not values

A page states what a thing is, what is claimed about it, and who claims it. A
page never states a quantity the system can compute for itself — source counts,
page counts, link counts, "the six failure modes". Such values are copies, they
drift silently from the thing they describe, and readers trust copies. Counts
are derived by `check.py` and rendered at read time.

Quantities *reported by a source* — a benchmark score, a reported win rate, a
model size — are claims about the world, not facts about the wiki, and are
written in full with their citation.

### 2.6 Calibration

The concept space is seeded from `wiki_local/survey.json`: **31 pages, 218
documents, 2–18 documents per page.** A compile run proposing more than a small
number of new pages is a signal that §2.2 is being applied too loosely, not
that the corpus is richer than the survey found. Creates-per-ingest is
instrumented and should decay toward zero across the run.

---

## 3. Summary Requirements

The `summary` field is the only text about a page that appears in `index.md`,
and `index.md` is the only mechanism by which a query selects a page. A page
whose summary fails to distinguish it from its neighbours is invisible at query
time regardless of the quality of its contents.

A summary is not a description. It is a discriminator.

### 3.1 The discrimination rule

A summary is written against its neighbours, never in isolation. Before writing
or revising a summary, the compiler must be shown the summaries of every page
in the same **confusion set** — pages whose slugs share a topic family
(`*-evaluation*`, `*-agent-*`, `long-context-*`, `*hallucination*`,
`*contamination*`, `*judge*`).

Where a page's scope is adjacent to a sibling's, the summary states the
boundary and names the sibling. Adjacency without a stated boundary is the
primary failure mode of this field.

### 3.2 Required content

In this order:

1. **Scope** — what the page holds, led by the distinguishing feature rather
   than the category. Not "Methods and frameworks for X" but the thing about X
   that this page and no sibling covers.
2. **Boundary** — where the page stops and which sibling continues, in the form
   `Concerns <this>; for <that>, see <sibling-slug>.` Required wherever a
   confusion set exists.
3. **Contention marker** — for pages flagged `contested`, a clause stating that
   the corpus disagrees and on what. A reader looking for the disagreement must
   be able to route here from the index alone.

### 3.3 Prohibited forms

- **Generic openers.** No summary begins with a category noun phrase —
  "Methods for", "Techniques for", "Benchmarks and", "Investigating and",
  "Critiques of", "Practical guides". The first six words are the most valuable
  in the field and must carry the differentiator. A shared opening makes
  siblings look alike at precisely the moment the selector is choosing between
  them.
- **Counts.** No number of documents, sources, papers, or positions (§2.5).
- **Source type as identity.** A page is never identified by who wrote its
  sources ("practitioner guides", "academic papers"). Pages are identified by
  subject; provenance is visible in the citations. A page defined by the voice
  of its sources is invisible to a subject query, which is the same failure as
  §2.4 in a milder form.
- **Hedged scope.** "Various aspects of", "topics including", "and related
  issues" describe nothing and discriminate against nothing.

### 3.4 Length

Target **40–55 words**. Hard ceiling **65 words**.

The ceiling is derived, not chosen. `index.md` is read in full on every query
alongside 2–3 retrieved pages and the generated answer. Capping the index at
~4,000 tokens across ~40 rows yields ~100 tokens per row, which is roughly 65
words once slug and markup are deducted.

The budget is a ceiling, not a target, and it is **not currently binding**.
Measured: the 31-page seed index renders at 2,138 tokens, 53% of the
4,000-token cap. Summary length is therefore set by how much text
discrimination actually requires. The ceiling exists to define where this
architecture stops scaling — which is the honest answer to "why not a
thousand documents?"

### 3.5 The summary test

A summary passes if, given the page's confusion set, you can state:

- **three questions this page should win**, and
- **one question it should lose, naming the sibling that should win it.**

If the losing sibling cannot be named, the boundary is undefined and both
summaries are rewritten. If two summaries in a confusion set could each
plausibly describe the other's page, **both** are rewritten, not one.

This test requires semantic judgment and is **not** automated in `check.py`.
Lexical overlap between summaries was measured against the seed taxonomy and
does not correlate with confusability: the highest-overlap pair is not a
confusable pair, and the most confusable pair has near-zero overlap. The test
belongs in the evaluation harness, applied by a judge model.

### 3.6 When summaries are rewritten

- The page's scope changes materially through Path A.
- A Path B create introduces a new page into a confusion set — in which case
  the summaries of the **existing** members are revised too, not only the new
  page's.

The second case is a real cost of page creation and is part of why Path B is
last-preferred (§2.1).

### 3.7 What check.py verifies

Mechanical checks only:

- summary present, non-empty, within the word ceiling;
- summary contains no digits (count prohibition);
- summary does not begin with a prohibited opener (fixed string list);
- every sibling slug named in a boundary clause resolves to an existing page.

Discriminability is not checkable in Python and is not attempted here.

---

## 4. Linking

Links are the graph. `core/graph.py` parses them as edges, so the format is
load-bearing and the parser is strict.

### 4.1 Syntax

- `[[slug]]` — target slug exactly as it appears in that page's frontmatter.
- `[[slug|display text]]` — same edge, alternate surface text.
- `[DOC-xxx]` — a citation, and simultaneously an edge to
  `wiki/sources/DOC-xxx.md`. Citation and link are the same act; source pages
  are never linked with `[[...]]`.

Links appear inline in body prose. Never in frontmatter, never in headings,
never in a "related pages" list at the foot of a page — a link exists because a
sentence needed it.

A `[[slug]]` that does not resolve to a file in `wiki/concepts/` or
`wiki/entities/` is a broken link and a check failure. The compiler may only
link to pages it has confirmed exist; it may not link forward to pages it
expects to be created later in the run.

### 4.2 When to link

Link when the prose refers to a concept with its own page and the reader would
need that page to follow the argument.

- **First mention only** within a page. Repeat mentions are plain text.
- Link the concept, not the phrasing: if the body says "single-agent
  orchestration" and the page is `llm-agent-design-patterns`, write
  `[[llm-agent-design-patterns|single-agent orchestration]]`.
- Never link a page to itself.
- Do not link a term merely because a page with that name exists. The test is
  whether a reader following the link finds the continuation of this argument.

### 4.3 How many

Target **3–8 outbound `[[slug]]` links** per concept page.

Below three, the page is largely isolated and multi-hop queries cannot reach
it. Above eight, the edges stop carrying information — if everything connects
to everything, the graph says nothing about structure and gap detection has
nothing to find.

These are compiler guidance, not check failures. `check.py` reports only two
link conditions as defects:

- **broken links** — a `[[slug]]` with no corresponding file;
- **orphans** — a concept page with zero inbound links, unreachable by
  navigation and effectively invisible unless selected directly from the index.

Source pages are exempt from the orphan check by construction; their inbound
edges are the `[DOC-xxx]` citations on concept pages.

---

## 5. Conflict Policy

Contradiction handling is the reason this system exists. In a vector store a
counterargument sits in the index and surfaces only if the question happens to
retrieve it. Here it is written onto the page that answers the question, so a
reader asking about the concept cannot miss the dispute.

The compiler never resolves a disagreement. Not by choosing, not by averaging,
not by hedging, not by ordering.

### 5.1 Detection — three things that look alike

Before taking Path C, classify. Three cases arrive looking identical and need
different handling.

**Scope difference — Path A, no flag.** The sources address different
conditions, populations, or scales, and appear to conflict only because the
conditions went unstated. Test: can both claims be true if their conditions
differ? Handling: extend normally, and state the condition under which each
claim holds. Recording a scope difference as a contradiction manufactures a
dispute that is not in the corpus.

**Supersession — Path A, with a timeline.** A later source had access to the
earlier one and corrects it, or the same authors revise their own result. Test:
is there an explicit temporal correction relationship, verifiable from the
`published` dates and the document's own text? Handling: record both, mark the
earlier claim superseded with its date and the superseding citation, and **do
not delete it**. A corrected claim remains visible as a signposted dead end.

**Contradiction — Path C.** Two sources assert incompatible claims about the
same referent under the same conditions. Test: can you write a single
proposition P such that one source asserts P and another asserts not-P?

**When the classification is uncertain, treat it as a contradiction.** The
failure is asymmetric. Wrongly flagging a scope difference costs a reader some
noise on a page they can read and correct. Wrongly resolving a real
contradiction removes the counterargument from the system permanently, which is
the failure this project exists to attack.

### 5.2 Recording — the Disagreement section

```markdown
## Disagreement

**At issue:** Whether benchmark contamination changes the relative ranking of
models, or only inflates absolute scores.

**Position — contamination invalidates comparison.** Leakage of test data into
training sets makes leaderboard scores unusable for comparing models, and
contamination-free benchmark construction is the necessary response
[DOC-018][DOC-071][DOC-119].

**Position — ranking is robust to leakage.** Contamination inflates absolute
scores but rarely reorders models relative to one another, so leaderboard
comparisons retain their practical value [DOC-040].

**Not in dispute:** That contamination occurs, and that it inflates absolute
benchmark scores.
```

Requirements:

- **`At issue`** states the proposition in one sentence. If it cannot be
  written as one proposition, this is more than one disagreement and each gets
  its own `###` sub-block under `## Disagreement`.
- **Every position is attributed.** A position with no `[DOC-xxx]` behind it is
  not a position; it is the compiler's opinion, and is prohibited (§6.2).
- **Position labels are neutral** and state the claim, not its standing. Never
  "the naive view", "the consensus", "the outlier".
- **`Not in dispute`** is required where common ground exists. Without it, a
  disagreement makes the whole page look uncertain, and the reader loses the
  part that is settled.
- **Position order is computed, not chosen:** ascending by earliest publication
  date among each position's supporting sources. Order carries no verdict, and
  the rule is reproducible without judgment.

### 5.3 The non-resolution rule

The compiler must not:

- state which position is correct, better supported, or more widely held;
- synthesise a middle position that no source holds;
- write "some argue X while others argue Y" without attaching citations to X
  and to Y;
- use asymmetric hedging — "although some claim", "despite the consensus",
  "one outlier argues" — which smuggles a verdict in through grammar;
- resolve by source authority, in either direction. An academic source does not
  outrank a practitioner source, and a practitioner source does not outrank an
  academic one;
- resolve using knowledge not present in the corpus (§6.2).

`contested` is terminal. The compiler never removes it. Only a human editing
the page can.

### 5.4 Interaction with extend — the third source

A new document arriving on an already-contested page:

**Agrees with an existing position.** Add its citation to that position's
attribution list. **The structure stays symmetric; the evidence does not.**
Positions remain equally labelled, equally stated, equally ordered. No sentence
is added about which position is now better supported, and no count is written
(§2.5). A reader seeing three citations under one position and one under the
other draws their own conclusion. The wiki displays evidence; it never scores
it.

Weighting positions by source count would assert that majority equals truth, in
a corpus where practitioner sources were deliberately selected for being
outnumbered.

**Introduces a third position.** Add a new position block. Re-sort by the date
rule in §5.2.

**Contradicts on a different proposition.** Add a second `###` block under
`## Disagreement`, with its own `At issue`.

**Explicitly retracts or supersedes a position.** Mark that position superseded
with the date and the superseding citation. Keep it visible. Do not delete it,
and do not remove the `contested` flag — a resolved dispute is still a dispute
the corpus contains.

### 5.5 What the compiler emits

Path C emits a structured record per affected page, which streams to the
decision log in the UI:

```json
{
  "path": "C",
  "page": "benchmark-data-contamination",
  "at_issue": "whether contamination changes relative model rankings",
  "incoming_source": "DOC-040",
  "classification": "contradiction",
  "aligns_with": null,
  "new_position": "ranking is robust to leakage",
  "positions": [
    {"label": "contamination invalidates comparison",
     "sources": ["DOC-018", "DOC-071", "DOC-119"]},
    {"label": "ranking is robust to leakage",
     "sources": ["DOC-040"]}
  ]
}
```

- `classification` is one of `contradiction`, `scope-difference`,
  `supersession` — emitted even when the result is Path A, so the decision log
  shows what was considered and rejected.
- Exactly one of `aligns_with` and `new_position` is non-null.
- The page's `contested` flag payload is the `at_issue` string.

---

## 6. Provenance

Every claim on every page is traceable to a file in `raw/`. Nothing appears on
a page that is not derived from a source in the corpus.

### 6.1 Citation format

`[DOC-041]` inline, immediately after the claim it supports. Multiple sources
for one claim: `[DOC-041][DOC-112]`, no separator.

DOC IDs resolve through `wiki_local/doc_map.json`, which maps each ID to a
filename in `raw/` and the SHA-256 of that file's contents. **IDs bind to
content, not to position.** A document added, removed, or reordered in `raw/`
does not change any other document's ID.

### 6.2 What may be written

Only what the cited sources support. Prohibited:

- **Model knowledge.** If the corpus does not say it, it does not go on the
  page, however true it is. This wiki is a compilation of these 223 documents,
  not of the model's training data.
- **Uncited connective inference.** A sentence drawing a conclusion from two
  sources is a synthesis and cites both. A sentence drawing a conclusion from
  neither is unsupported: drop it, or flag it `unresolved`.
- **Hedged attribution.** "Some researchers argue" without a DOC ID is not a
  citation. Name the source or drop the claim.

Direct quotation is permitted only where exact wording carries the claim — a
definition, a stated position in a disagreement — and is kept under fifteen
words. Everything else is paraphrase.

### 6.3 What check.py verifies

- Every `[DOC-xxx]` resolves to an entry in `doc_map.json`.
- Every body paragraph on a concept page contains at least one citation.
- **Every source page links out to at least one concept page.** A document that
  was read and produced nothing is a compile failure, not an absence. This is
  the check that makes ingest auditable: in a vector store a badly-processed
  document sits inert and invisible; here it surfaces as a defect.
- **Bidirectional agreement.** For every `[[slug]]` on a source page, the
  corresponding concept page cites that DOC ID, and vice versa. Disagreement
  between the two directions is a compile error no other check would catch
  (§1.3).
- Documents listed in `wiki_local/excluded.json` are exempt from all of the
  above. Excluded documents are recorded with a reason, not silently dropped.
