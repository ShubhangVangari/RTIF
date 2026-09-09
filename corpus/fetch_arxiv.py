import os, re, arxiv

OUT = "raw"

QUERIES = [
    'cat:cs.CL AND abs:"LLM evaluation"',
    'cat:cs.CL AND abs:"retrieval augmented generation"',
    'cat:cs.CL AND abs:"LLM as a judge"',
    'cat:cs.IR AND abs:"dense retrieval"',
    'cat:cs.CL AND abs:"language model agents"',
    'cat:cs.CL AND abs:"long context"',
    'cat:cs.CL AND abs:"hallucination"',
    'cat:cs.CL AND abs:"benchmark contamination"',
]


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:70]

os.makedirs(OUT, exist_ok=True)
client = arxiv.Client(page_size=25, delay_seconds=5, num_retries=5)
seen = set()

for q in QUERIES:
    print(f"fetching: {q}")
    search = arxiv.Search(query=q, max_results=25,
                          sort_by=arxiv.SortCriterion.Relevance)
    for r in client.results(search):
        name = slug(r.title)
        if name in seen:
            continue
        seen.add(name)
        with open(f"{OUT}/arxiv-{name}.md", "w") as f:
            f.write(
                f"---\ntitle: {r.title}\nsource: {r.entry_id}\n"
                f"published: {r.published.date()}\n"
                f"authors: {', '.join(a.name for a in r.authors[:5])}\n"
                f"type: arxiv\n---\n\n"
                f"# {r.title}\n\n## Abstract\n\n{r.summary}\n"
            )
    print(f"  total: {len(seen)}")

print(f"done: {len(seen)} documents")