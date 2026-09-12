"""baseline/embed.py — turns chunks into embedding numbers and saves them.

Uses Vertex AI's text-embedding-005 model. The client is handed in as an
argument rather than built here, so this file does not need to know how
credentials or a project ID were set up — scripts/ask_baseline.py owns that.

No vector database. At 153 documents and a few thousand chunks, a plain
Python list of chunks plus a plain Python list of their vectors, searched
with a for loop, is simpler than standing up a database — and just as fast
at this scale, since there is nothing here a database would meaningfully
speed up.
"""

from __future__ import annotations

import pickle
from pathlib import Path

from baseline.chunk import Chunk

EMBEDDING_MODEL = "text-embedding-005"

# text-embedding-005 rejects any single request over 20,000 input tokens,
# combined across the whole batch. 100 chunks at ~500 words each comes to
# roughly 50,000 tokens and fails outright — confirmed against the real
# API, not a guess. 15 chunks keeps even a worst-case batch of the densest
# chunks in this corpus (~975 tokens each) safely under that limit.
CHUNKS_PER_BATCH = 15

# Vertex list price for text-embedding-005, USD per 1M input tokens.
PRICE_PER_MILLION_TOKENS = 0.10


# Turns a list of chunk texts into a list of embedding vectors, one vector
# per text, in the same order the texts came in. One call to Vertex AI can
# only hold so much text, so this sends the texts in small batches rather
# than one at a time or all at once. It works by slicing the texts into
# batches, calling the embedding model on each batch, and collecting every
# vector it gets back — and along the way, it keeps a running total of how
# many tokens were billed, so the cost can be printed once at the end.
def embed_texts(texts: list[str], client) -> list[list[float]]:
    all_vectors: list[list[float]] = []
    total_tokens = 0.0

    for start in range(0, len(texts), CHUNKS_PER_BATCH):
        batch = texts[start : start + CHUNKS_PER_BATCH]
        response = client.models.embed_content(model=EMBEDDING_MODEL, contents=batch)

        # Each embedding in the response lines up with the same position
        # in this batch, so we can just append them in order.
        for embedding in response.embeddings:
            all_vectors.append(embedding.values)
            if embedding.statistics is not None and embedding.statistics.token_count:
                total_tokens += embedding.statistics.token_count

        print(f"  embedded {start + len(batch)} of {len(texts)} chunks")

    cost = total_tokens / 1_000_000 * PRICE_PER_MILLION_TOKENS
    print(f"  embedding tokens: {int(total_tokens):,} | cost: ${cost:.4f}")

    return all_vectors


# Embeds every chunk and saves the whole index to one file on disk: the
# chunks themselves, plus the vector for each one. This is the step that
# turns a plain list of chunks into something find_closest_chunks() can
# search. It works by pulling the text out of each chunk, embedding all of
# them in one go, and writing the chunk list and the vector list to
# out_path together with pickle.
def build_index(chunks: list[Chunk], client, out_path: Path) -> None:
    texts: list[str] = []
    for chunk in chunks:
        texts.append(chunk.text)

    vectors = embed_texts(texts, client)

    with open(out_path, "wb") as index_file:
        pickle.dump((chunks, vectors), index_file)


# Loads a previously built index back off disk. This is how retrieve.py and
# scripts/ask_baseline.py get hold of the chunks and their vectors without
# calling the embedding model again every time a question is asked. It
# works by reading the pickle file and handing back the same
# (chunks, vectors) pair that build_index() saved.
def load_index(path: Path) -> tuple[list[Chunk], list[list[float]]]:
    with open(path, "rb") as index_file:
        chunks, vectors = pickle.load(index_file)
    return chunks, vectors
