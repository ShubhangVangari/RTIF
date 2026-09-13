# RTIF - Read The Index First

A knowledge system that reads documents once, at ingest time, and compiles them
into wiki pages a person can read.

## 1. What this is

Most retrieval systems cut documents into chunks, turn each chunk into a list
of numbers, and at question time find the closest chunks by arithmetic. Nothing
is read until an answer is being written. This system works the other way
round. Every document is read by a model when it arrives and folded into a set
of concept pages. When a new document says something a page already covers, it
is added to that page. When it says something a page contradicts, both
positions are written onto the page side by side and the page is marked
contested.

To answer a question, a model reads an index of all the page summaries, decides
which pages to open, reads them, and can follow a link from one page to
another. It is closer to using a library catalogue than a search engine. This
repo contains that system, a standard vector RAG system built over the same
documents as a control, and an evaluation harness that compares them on 30
questions graded blind by a third model. The vector system scored slightly
higher overall. The interesting part is which questions each one wins.

## 2. Why build it this way

A vector store holds chunks. The relationship between two chunks is not stored
anywhere — it is recomputed, approximately, every time somebody asks a
question. If two documents in the corpus contradict each other, that
contradiction exists nowhere. It is not in the first document, which does not
know about the second. It is not in the second, which does not know about the
first. It surfaces only if a query happens to retrieve both chunks at once and
the model happens to notice.

**A disagreement between two documents is in neither document. It only exists
once something writes it down.**

So this system pays that cost once, at ingest, instead of partially and
unreliably on every query. The relationships end up as text: a `## Disagreement`
section on the page, with both positions stated and attributed. The reader
asking about the concept cannot miss the dispute, because it is on the page
they are already reading. This is expensive — roughly 5 hours and $25 to
compile 152 documents with `gemini-2.5-pro`, against 7.3 seconds and $0.016 to
embed the same set. Section 6 is about whether it was worth it.

The corpus was picked to test exactly this. 223 documents were collected: 193
arXiv abstracts from cs.CL and cs.IR, and 30 hand-picked practitioner blog
posts and framework docs. 152 of them were compiled before the run was stopped
— 131 papers and 21 practitioner posts. The practitioner sources were chosen
because they disagree with each other and with the papers.
One post is titled "Don't Build Multi-Agents". Another is "How we built our
multi-agent research system". Both are in the corpus, and neither mentions the
other. A system that handles agreement well and disagreement badly would look
fine on a corpus that does not contain any.

## 3. How it works

Three operations.

**Ingest.** One document at a time. The model is shown the index of page
summaries and the document, and picks the pages that document affects. It reads
those pages, then rewrites them. For each concept the document touches, one of
four things happens:

- **extend** an existing page with the document's claims,
- **create** a new page, if no page covers the concept,
- **link** one page to another where the prose needs it,
- **flag** a contradiction, recording both positions without resolving them.

Extending is preferred. Creating a page changes the index, which every future
query has to read, so it is the last resort. Linking is not an alternative to
the others — every path carries the same obligation to link.

**Query.** The model reads the index of 31 page summaries, picks pages to open,
and reads them. Inside a page it may follow a `[[link]]` to open another page.
This is capped at 5 rounds, after which it must answer with what it has.

**Check.** A pure Python audit of the compiled wiki. No model calls —
everything it reports is mechanically derivable, and a check that needs a model
to run is a check you cannot trust to grade the model. What it currently finds:

| Check | Result |
|---|---|
| Broken links | 0 |
| Index mismatches | 0 |
| Orphan pages | 2 |
| Uncited sentences | 112, across 26 pages |
| Pages over 2,000 words | 4 |
| Documents with no trace | 75 |

The 75 untraced documents are the 71 that were never ingested plus 4 that
failed during the run. The other numbers are discussed in section 7.

The result is 31 concept pages, 6 of them marked contested. The wiki on disk:

```
raw/                      223 source documents. Read, never written.
wiki/
  index.md                The routing catalog: one row per page, slug + summary.
  log.md                  Append-only ledger of every operation.
  concepts/<slug>.md      The 31 concept pages.
  sources/<DOC-ID>.md     One page per ingested document. Never in the index.
SCHEMA.md                 The spec. The only hand-written file in the system.
```

Source pages are deliberately left out of `index.md`. Adding 152 more rows
would multiply the per-query index cost and make page selection worse. They are
reached by following a citation, never by being selected.

## 4. Design decisions

### There is a hand-written schema file

Every document is a separate model call with no memory of any other call. There
is no shared context that makes 152 independent calls agree on anything.
Without a strict written spec you get 152 improvised formats, and nothing
downstream can parse them. `SCHEMA.md` is the only human-authored file in the
system: frontmatter fields, citation format, link syntax, what earns a page,
how a disagreement is recorded. Where the spec is ambiguous, the compiler
improvises, and the improvisations do not agree with each other. So it prefers
an over-specified rule to a tasteful one.

### The page taxonomy came from the corpus, not from me

Before any document was compiled, a cheap pass over the titles and lead
sentences of all 223 documents produced 31 concepts. Those became the seed
pages.

The alternative is letting the first document define the taxonomy. That sets
the shape of the whole wiki from a sample size of one, and every document after
it inherits that shape. Deriving the taxonomy from the whole corpus first means
the page boundaries reflect what the corpus is actually about.

### Disagreement never gets its own page

If sources conflict about X, both positions go on X's page, and X is flagged
contested. There is never a "critiques of X" page.

A separate page for the counterargument reproduces exactly the failure this
system exists to fix. The objection would sit in the corpus, correctly
recorded, and never surface for the person asking about X. An early taxonomy
run did produce a separate critique page. It was caught and removed. The rule
has no exceptions.

### Positions are never weighted by source count

One page has ten sources on one side of a dispute and one on the other. The
page presents both evenly — same labels, same structure, no sentence saying
which is better supported.

Ten papers proposing a technique does not outrank one practitioner reporting
that it broke in production. They are different kinds of evidence, not
different amounts of the same evidence. The reader sees ten citations under one
position and one under the other, and can draw their own conclusion. The wiki
displays evidence; it does not score it.

### Anything computable is computed in Python

Counts, citations, links, hop counts, page sizes. None of these are ever asked
of the model.

The model was repeatedly wrong on arithmetic while being right on judgment. So
judgment is the only thing it is asked for. Pages are also forbidden from
stating a quantity the system can compute — "the six failure modes" is a copy
that drifts silently when a seventh is added, and readers trust copies.

### Ingest order is interleaved

Sorted alphabetically, the corpus put all 193 academic papers before all 30
practitioner posts. Contested pages filled up with same-side positions for
hours before any opposing evidence arrived, which changes what those pages look
like when the opposing document finally lands.

Ingest now interleaves: one practitioner document every six academic ones. The
compiled set came out at 131 papers to 21 posts, a ratio of 6.2 to 1, so the
cadence held. The flag rate on practitioner documents is about 5x higher than
on academic ones, so this spreads the disagreements across the run instead of
stacking them at the end.

### The eval grader is Claude, not Gemini

Gemini wrote both sets of answers — the wiki's and the baseline's. A model
grading its own output scores it favourably. There are papers about this
effect in this corpus.

So grading uses `claude-sonnet-4-5`, a different model family, and the grader
is never told which system produced the answer it is looking at. It sees a
question, an expected element, and one answer.

### `core/` imports nothing cloud-related

The model call and the page loader are passed into `core/` as arguments, not
imported. `core/compile.py` does not know Gemini exists; it knows it has a
function that takes a prompt and returns a dict.

This means the entire compile and query path runs offline against fakes. The
tests need no network, no credentials, and no spend. It also means swapping the
storage backend from local disk to Cloud Storage touched no logic at all.

### Deployment came before any application code

A `/health` endpoint and a `/smoke` endpoint went to Cloud Run on day one,
before there was anything to deploy.

Deployment was the highest-variance task in the project — the one most likely
to fail in a way unrelated to the actual work. Doing it first caught a missing
environment variable immediately, which would otherwise have surfaced days
later, tangled up with real code and much harder to isolate.

## 5. Bugs found and fixed

### The compiler was inventing disagreements

A paper arguing that multi-agent systems work well when structured
hierarchically was flagged as contradicting the multi-agent page. It does not
contradict it. The page says the corpus disputes whether these systems are
reliable in production. The paper says something about *how* to build them. It
answers a different question.

The fix is a mandatory check before anything can be flagged. The model must
quote the exact proposition in dispute, state what the incoming document claims
about that specific proposition, and confirm the document asserts the opposite.
If it cannot do all three, it takes the extend path instead. A system that
manufactures contradictions is worse than one that misses them — it fills the
pages with noise and destroys trust in the real flags.

### Source types were silently wrong on every document

Frontmatter was parsed with `yaml.safe_load`, and parse failures were swallowed
with `return {}`. 107 of the 223 documents fail YAML parsing, because arXiv
titles contain unquoted colons and raw LaTeX. An empty dict meant no
`source_type` field, which defaulted to practitioner.

So every academic paper in the corpus was labelled practitioner. Nothing
errored, nothing looked wrong, and the interleaving decision above was being
made on garbage data. The fix is a flat line-based parser that handles all 223
documents and raises on anything it cannot read, rather than guessing. The
general lesson is the one in the bug: a silent fallback to a default is an
error that never gets reported.

### Document IDs broke when files moved

IDs were assigned by sorted position in `raw/`. Deleting a file and restoring
it silently repointed every ID after the change, so `DOC-112` now referred to a
different document than it had an hour earlier. This caused the wrong documents
to be deleted during a cleanup.

IDs now bind to content, not position: `doc_map.json` maps each ID to a
filename and the SHA-256 of that file's contents. Adding, removing, or
reordering documents cannot change any other document's ID. Cost about two
hours to find and fix.

## 6. The comparison

The control group is an ordinary vector RAG system over the same 152
documents: 302 chunks of 500 words with 50 words of overlap, embedded with
`text-embedding-005`, retrieved by cosine similarity, top 5. It was built in
7.3 seconds for $0.016.

Both systems were asked the same 30 questions, 5 in each of 6 categories, and
both used `gemini-2.5-pro` to write the answer. Answers were graded blind by
`claude-sonnet-4-5`: 1.0 for correct, 0.5 for partial, 0.0 for wrong.

| Question type | Wiki | Vector RAG |
|---|---|---|
| simple_lookup | 4.0/5 | 4.0/5 |
| paper_specific | 2.5/5 | 4.5/5 |
| exact_detail | 0.5/5 | 5.0/5 |
| synthesis | 3.0/5 | 1.0/5 |
| disagreement | 5.0/5 | 2.5/5 |
| absence | 5.0/5 | 4.5/5 |
| **total** | **20.0/30** | **21.5/30** |

**The vector system scored higher overall.** That is the honest result. The two
systems are good at different questions, and that split is the actual finding.

| Per question | Wiki | Vector RAG |
|---|---|---|
| Latency | 36.9s | 11.8s |
| Cost | $0.05 | ~$0.005 |
| Tokens in | 13,527 | 2,319 |

**Where the vector system wins: anything needing the exact text.** It scored
5.0/5 on exact figures against the wiki's 0.5/5. Compiling is lossy. The wiki
kept the benchmark names — FAITHQA, CLongEval, EscapeBench — and lost every
number attached to them. A chunk keeps the sentence verbatim, so the number is
still there. The same applies to questions about one named paper: the baseline
retrieves that paper's chunk directly, while the wiki has no per-paper pages at
all (see section 7).

**Where this system wins: relationships.** It scored 5.0/5 on disagreement
against 2.5/5, and 3.0/5 on synthesis against 1.0/5. The sharpest case is q25,
"Is single-pass retrieval sufficient for production RAG systems?" One document
argues the minority position against seventeen on the other side. Top-5 cosine
similarity retrieved five chunks that all agreed with each other, so the
baseline answered confidently that iterative retrieval is required and scored
0.0. The wiki had the dispute written onto the page, because the compiler had
to decide what to do with that document when it arrived, and scored 1.0.

**The cost of that is about 3x the money and 3x the wait per question.**

## 7. Known limitations

### New pages can never be created

A new page requires two independent sources. That check runs inside a single
document's compile call, which can only ever see one document. So the condition
is structurally unsatisfiable, the create path is unreachable, and the 31 seed
pages are final.

The fix is to evaluate the floor against accumulated state instead of a single
call: record a concept that falls below the floor as a candidate on the nearest
page, and promote it to its own page in `check.py` once a second independent
source appears. Not built.

### Pages grow and never split

Splitting a page means creating a page, which cannot happen for the reason
above. Four pages are already over 2,000 words.

### The system falsely claimed a topic was absent

Asked "What is the Agents Rule of Two?", the system answered that the corpus
does not cover it. The corpus does cover it, in a section of
`llm-agent-safety-and-security`. This happened twice — in the smoke test and
again in the full run, so it is reproducible rather than a fluke.

The cause is that the page's one-line summary does not contain the term, and
the summary is all that page selection sees. This exact failure mode is written
into `SCHEMA.md` as a known risk of summary-based routing. It is now measured
rather than predicted.

### 112 uncited sentences across 26 pages

Roughly 15% of body sentences carry no citation, on pages that claim full
provenance for every claim. This is the model writing its own connective text
between cited claims. A prompt change brought it down from 72% to 15%, but
prompt pressure alone does not close the gap — it needs a validation pass that
rejects the page.

### No entity pages, by design

There is no page for a specific paper, model, or benchmark. Named things are
recorded on the nearest concept page. This makes the wiki structurally weak at
"what does paper X say" questions, and the 2.5/5 on paper_specific is that
weakness being measured. It was an accepted trade — entity pages for every
paper would regenerate the noise the taxonomy step removed — but it is a real
cost, not a neutral one.

### Only 152 of 223 documents were compiled

The run was stopped deliberately. Cost per document climbed from about $0.09 to
about $0.13 and time per document from about 90 seconds to about 400 seconds,
because pages get longer as they absorb more documents, and every ingest reads
the pages it might affect. The cost of ingesting document *n* grows with *n*.

### Ingest order still matters

Interleaving reduced the effect. It did not remove it. What a page says still
depends on the sequence its documents arrived in.

### The run had no handling for a hard stop

A billing spend cap was breached mid-run. 50 documents failed in a row, because
a 403 from the API was retried and then recorded the same way as a malformed
model response. A permanent failure and a transient one were indistinguishable
to the runner.

### The vector baseline is read-only here — but that hides a real advantage

Adding a document to the baseline is chunk, embed, append. Adding one to the
wiki means recompiling pages, which costs roughly $0.13 and several minutes and
may change text a reader has already read. Incremental update is genuinely
easier for vector RAG, and that is a real point in its favour.

### Scale

The index is read in full on every query, so it has to stay small. Not because
of the context window — that is a million tokens, and the current index is
2,138 — but because cost scales with it and page selection gets worse as the
list of summaries grows. Past roughly 40 pages this design needs a hierarchical
index, which reintroduces the routing problem it was avoiding.

## 8. What I would do next

**The candidates mechanism**, so new pages can exist. Record sub-floor concepts
as candidates on the nearest page, and promote them in `check.py` when a second
independent source appears. This is the single change that unblocks page
creation and page splitting together.

**A validation pass that rejects pages rather than reporting them.** `check.py`
currently audits after the fact and prints a list. Uncited sentences would be
better handled by failing the compile and asking again, with the specific
sentence quoted.

**Key terms in the index rows.** The false-absence bug is a routing failure:
the term exists on the page but not in its summary. Adding a short list of key
terms per row would fix that class of miss without rewriting the summaries.

**An order-dependence experiment.** Ingest the same corpus twice in two
different orders and measure how far the two wikis diverge — same pages, same
flags, same claims? This is the obvious experiment and I did not get to run it.
The honest position right now is that I know order matters and do not know how
much.

## 9. Running it

Built and run on Python 3.14. Needs a Google Cloud project with Vertex AI
enabled, and an Anthropic API key for the eval grader only.

```bash
python -m venv index-rag-venv
source index-rag-venv/bin/activate
pip install -r requirements.txt
```

Set these in a `.env` file at the repo root:

```
GOOGLE_CLOUD_PROJECT=your-project-id
BUCKET=your-gcs-bucket-name
ANTHROPIC_API_KEY=sk-ant-...
```

Cloud credentials come from `gcloud auth application-default login`. The
`BUCKET` variable is only needed for the `--store gcs` paths and the deployed
service; everything runs locally against `wiki_local/` by default.

```bash
# Seed the 31 concept pages from the survey taxonomy.
python scripts/bootstrap.py

# Compile documents into the wiki, in interleaved order.
python scripts/ingest.py --docs 10
python scripts/ingest.py --docs 10 --store gcs

# Ask the wiki a question.
python scripts/ask.py "what do sources disagree about regarding multi-agent systems?"

# Audit the compiled wiki. No model calls, no cost.
python scripts/check_wiki.py

# Build the vector baseline over the same documents, then ask it.
python scripts/ask_baseline.py --build
python scripts/ask_baseline.py "what is context rot?"

# Run the eval. 30 questions through both systems, graded blind.
python evals/runner.py
python evals/runner.py --only disagreement
python evals/report.py

# Tests. No network, no credentials, no spend.
pytest

# The web service, locally.
python main.py
```

## 10. Repo layout

```
core/          The system. No cloud imports anywhere in here.
  models.py    Page, Decision, Issue, and the markdown serialisation.
  compile.py   The ingest compiler: one document in, pages and decisions out.
  query.py     The query loop: read index, pick pages, follow links, answer.
  check.py     The audit. Pure Python, no model calls.
store/         Storage backends. Local disk and Cloud Storage, same interface.
scripts/       Command-line entry points. Everything with a __main__ lives here.
baseline/      The vector RAG control group: chunk, embed, retrieve.
evals/         The comparison harness: questions, blind grader, runner, report.
prompts/       Prompt text, read off disk at call time rather than hardcoded.
tests/         78 offline tests against fake model calls.
wiki_local/    The compiled wiki, plus doc_map.json and the survey taxonomy.
raw/           The 223 source documents. Immutable.
static/        The web console served at /.
main.py        FastAPI service: the console, a health check, and four JSON
               endpoints (/ask, /ask-baseline, /page/<slug>, /index).
SCHEMA.md      The spec.
```

The three-way split is the important part.

`core/` holds the logic and imports nothing cloud-related — the model call and
the page loader arrive as arguments. That is what lets the compile and query
paths be tested offline against fakes.

`store/` holds everything that knows about disk or buckets, behind one
interface. Local and GCS are interchangeable, which is why `--store gcs` is a
flag rather than a rewrite.

`scripts/` holds the wiring: reading the command line, building a real Gemini
client, passing it into `core/`. All the credentials, retries, and pricing
constants live at this layer, so the layer underneath stays testable.
