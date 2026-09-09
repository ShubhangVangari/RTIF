import os, re, httpx, trafilatura

OUT = "raw"
os.makedirs(OUT, exist_ok=True)

def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:70]

urls = [l.strip() for l in open("/Users/shubhangvangari/Documents/index-rag-project/index-rag/corpus/urls.txt")
        if l.strip() and not l.startswith("#")]

ok, failed = 0, []

for url in urls:
    try:
        r = httpx.get(url, follow_redirects=True, timeout=30,
                      headers={"User-Agent": "Mozilla/5.0 (research project)"})
        r.raise_for_status()
        text = trafilatura.extract(r.text, include_comments=False)
        meta = trafilatura.extract_metadata(r.text)

        if not text or len(text) < 500:
            failed.append((url, f"too short: {len(text or '')} chars"))
            continue

        title = (meta.title if meta else None) or url
        author = (meta.author if meta else None) or "unknown"
        date = (meta.date if meta else None) or "unknown"

        with open(f"{OUT}/web-{slug(title)}.md", "w") as f:
            f.write(
                f"---\ntitle: {title}\nsource: {url}\n"
                f"published: {date}\nauthors: {author}\ntype: web\n---\n\n"
                f"# {title}\n\n{text}\n"
            )
        ok += 1
        print(f"ok   {len(text):>6} chars  {title[:60]}")

    except Exception as e:
        failed.append((url, str(e)[:80]))
        print(f"FAIL {url}")

print(f"\n{ok} fetched, {len(failed)} failed")
for url, why in failed:
    print(f"  {url} — {why}")