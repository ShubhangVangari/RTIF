import glob, re, html, json
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
OUT = ROOT / "wiki_local" / "corpus_surface.txt"

NOISE = re.compile(
    r"newsletter|subscribe|sign up|delivered monthly|to your inbox|"
    r"product updates|community spotlight|cookie|privacy policy|"
    r"follow us|share this|read more|all rights reserved|"
    r"^note:|^table of contents|^published |^posted ", re.I)

LEAD_NOISE = re.compile(r"^(for our current approach|note:|see also|read more|"
                        r"this post|update:|edit:)", re.I)

def field(t, n):
    m = re.search(rf"^{n}:\s*(.+)$", t, re.M)
    return m.group(1).strip() if m else ""

def clean(s):
    s = html.unescape(s)
    s = re.sub(r"\$\^?\\?circ\$", "°", s)
    s = re.sub(r"\$[^$]*\$", "", s)
    s = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", s)
    s = re.sub(r"[{}\\]", "", s)
    return re.sub(r"\s+", " ", s).strip(" -–—:|")

def strip_site(t):
    return re.sub(r"\s*[–—|-]\s*[^–—|-]{0,30}(blog|\.com|\.dev|\.ai)[^–—|-]*$",
                  "", t, flags=re.I).strip()

def lead(text, title, n=280):
    body = re.sub(r"^---.*?---", "", text, flags=re.S)
    body = re.sub(r"^#+ .*$", "", body, flags=re.M)
    body = clean(re.sub(r"\s+", " ", body).strip())
    kept, total = [], 0
    for i, s in enumerate(re.split(r"(?<=[.!?]) ", body)):
        s = s.strip()
        if len(s) < 40 or NOISE.search(s):
            continue
        if i < 2 and LEAD_NOISE.match(s):
            continue
        if s.lower().startswith(title.lower()[:25]):
            continue
        kept.append(s); total += len(s)
        if total > n:
            break
    return (" ".join(kept)[:n].rstrip() + ("…" if total > n else "")) if kept else "[no clean lead]"

def build():
    files = sorted(glob.glob(str(RAW / "*.md")))
    rows, flagged = [], []
    for i, path in enumerate(files, 1):
        text = Path(path).read_text()
        title = clean(field(text, "title"))
        if "/web-" in path:
            title = strip_site(title)
            a = clean(field(text, "authors"))
            a = "" if a.lower() in ("unknown", "") else a.split(",")[0]
            d = urlparse(field(text, "source")).netloc.replace("www.", "")
            kind = f"Practitioner ({a}, {d})" if a else f"Practitioner ({d})"
        else:
            kind = "Academic"
        ld = lead(text, title)
        if ld == "[no clean lead]":
            flagged.append(f"DOC-{i:03d}")
        rows.append(f"[DOC-{i:03d}] | {kind} | {title}\n    {ld}")

    # interleave so practitioner sources aren't clustered at the end
    acad = [r for r in rows if "| Academic |" in r]
    prac = [r for r in rows if "| Practitioner" in r]
    out, pi = [], 0
    for i, a in enumerate(acad):
        out.append(a)
        if i % 6 == 5 and pi < len(prac):
            out.append(prac[pi]); pi += 1
    out.extend(prac[pi:])

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(out) + "\n")

    dmap = {f"DOC-{i:03d}": p for i, p in enumerate(files, 1)}
    (ROOT / "wiki_local" / "doc_map.json").write_text(json.dumps(dmap, indent=2))

    print(f"{len(rows)} documents -> {OUT}")
    if flagged:
        print("no clean lead:", flagged)

if __name__ == "__main__":
    build()