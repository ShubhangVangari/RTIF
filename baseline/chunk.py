"""baseline/chunk.py — cuts a raw document into overlapping word chunks.

Plain Python, no cloud imports. This file only knows how to turn one string
of document text into a list of Chunk pieces, so it can be read and tested
entirely on its own, with no store and no network.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    text: str


# Cuts off the YAML frontmatter at the top of a raw document. Every raw file
# starts with a block between two "---" lines holding metadata like title
# and author, and that block is not part of the document's content — it
# would just add noise to a chunk. It works by finding the first two lines
# that are exactly "---" and keeping only what comes after the second one.
def _strip_frontmatter(text: str) -> str:
    lines = text.splitlines()

    if not lines or lines[0].strip() != "---":
        return text

    end_line = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_line = i
            break

    if end_line is None:
        return text

    remaining_lines = lines[end_line + 1 :]
    return "\n".join(remaining_lines)


# Splits one document into overlapping chunks of words. A whole document is
# too long to hand a language model or an embedding model at once, so it
# gets cut into smaller pieces first. It works by splitting the text on
# whitespace into a list of words, then walking through that list taking
# 500 words at a time and moving forward by 450 words each step — the 50
# words two neighbouring chunks share stop a sentence from being cut
# cleanly in half right at a chunk boundary.
def chunk_document(
    doc_id: str,
    text: str,
    words_per_chunk: int = 500,
    overlap_words: int = 50,
) -> list[Chunk]:
    content = _strip_frontmatter(text)
    words = content.split()
    step = words_per_chunk - overlap_words

    chunks: list[Chunk] = []
    chunk_number = 0
    start = 0

    while start < len(words):
        # Take the next 500 words, starting from where this step landed.
        end = start + words_per_chunk
        chunk_words = words[start:end]
        chunk_text = " ".join(chunk_words)

        chunk_id = f"{doc_id}-chunk-{chunk_number}"
        chunks.append(Chunk(chunk_id=chunk_id, doc_id=doc_id, text=chunk_text))

        chunk_number += 1
        start += step

    return chunks
