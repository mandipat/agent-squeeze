"""Deep-research agent compression: fetched pages -> citation-worthy excerpts.

Deep-research agents hoard fetched pages — search-result pages, blog posts
wrapped in nav/cookie/footer boilerplate, docs, press releases, forum
threads. The final report cites a handful of quoted passages, yet the whole
page stays in context. This module keeps the cited/high-signal lines
verbatim (citations must be byte-exact quotes), holds the rest off-context
(byte-identical recall via `HoldStore` ref), and reduces pure-boilerplate
or exact-duplicate pages to a one-liner. Nothing kept is ever rewritten —
the same rule the admit-time gate uses.

Decisions per page: "keep_full" (short or all-signal), "keep_quotes"
(verbatim excerpt of cited/signal lines, rest held), "notice" (boilerplate,
error page, or exact duplicate — one-liner, full page held).
"""
import re

from .admit import HoldStore
from .messages import estimate_tokens

# Lines that carry no research signal, wherever they appear.
BOILERPLATE_RE = re.compile(
    r"(accept\s+(all\s+)?cookies|cookie\s+(policy|banner|consent)|"
    r"subscribe\s+(to|for|now)|sign\s*up\s+for\s+(our\s+)?newsletter|"
    r"follow\s+us\s+on|share\s+(this|on)\s|related\s+(articles|posts)|"
    r"you\s+may\s+also\s+like|privacy\s+policy|terms\s+of\s+(service|use)|"
    r"all\s+rights\s+reserved|©|copyright\s+©?|back\s+to\s+top|"
    r"skip\s+to\s+(content|main)|table\s+of\s+contents|"
    r"(login|log\s*in|register|create\s+an?\s+account)\b|"
    r"advertisement|sponsored\s+content|click\s+here\s+to|"
    r"home\s*>\s*|breadcrumb)", re.IGNORECASE)

# Pages that fetched nothing worth judging.
ERROR_PAGE_RE = re.compile(
    r"^\s*(404|403|401|500|502|503)\b|page\s+not\s+found|"
    r"access\s+denied|forbidden|captcha|please\s+verify\s+you\s+are\s+human|"
    r"rate\s+limit\s+exceeded|content\s+unavailable", re.IGNORECASE)

# Short low-content forum/social noise ("+1", "lol") — kept only if cited.
NOISE_RE = re.compile(
    r"^(\+1|me\s*too|following(\s+this)?|lol|haha|unsubscribed|bump|"
    r"this(\s+is\s+great)?|nice|thanks(\s+for\s+sharing)?)\W?.{0,40}$",
    re.IGNORECASE)

FULL_KEEP_CHARS = 800  # short pages: judging line-by-line costs more than it saves


def _task_keywords(task):
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9_\-]{3,}", task or "")}


def _looks_like_signal(line, keywords):
    """A line worth keeping verbatim: data, a quote, or task vocabulary."""
    if not line:
        return False
    low = line.lower()
    if re.search(r'\d', line) and len(line) > 25:  # numbers + prose = data
        return True
    if re.search(r'["\u201c\u201d].{10,}["\u201c\u201d]', line):  # quoted passage
        return True
    if re.search(r"https?://", line) and len(line.strip()) < 160:  # bare reference
        return True
    hits = sum(1 for k in keywords if k in low)
    return hits >= 2 and len(line) > 20


def deterministic_policy(page, task, cited):
    """Free offline judge. Returns (decision, keep_line_idxs)."""
    text = page.get("text", "") or ""
    lines = [l for l in (ln.strip() for ln in text.split("\n")) if l]
    if not lines:
        return "notice", []
    if any(ERROR_PAGE_RE.search(l) for l in lines[:5]):
        return "notice", []
    if all(BOILERPLATE_RE.search(l) for l in lines):
        return "notice", []
    if len(text) <= FULL_KEEP_CHARS:
        return "keep_full", list(range(len(lines)))

    cited = cited or ()
    keep = set()
    for i, l in enumerate(lines):
        if any(q and q in l for q in cited):
            keep.add(i)  # cited quotes are sacred: always verbatim
    keywords = _task_keywords(task)
    for i, l in enumerate(lines):
        if i in keep or BOILERPLATE_RE.search(l):
            continue
        if NOISE_RE.search(re.sub(r"^reply\s*\d+\s*:\s*", "", l, flags=re.IGNORECASE)):
            continue  # low-content noise, dropped unless cited above
        if _looks_like_signal(l, keywords):
            keep.add(i)
    # Fail-safe: never empty a page entirely — keep its most signal-dense line.
    if not keep:
        scored = sorted(range(len(lines)),
                        key=lambda i: sum(1 for k in keywords if k in lines[i].lower()),
                        reverse=True)
        keep.add(scored[0])
    return "keep_quotes", sorted(keep)


def squeeze_research_pages(pages, task, cited=(), policy_fn=None, store=None):
    """Compress a research corpus. Returns (pages_out, stats).

    pages: [{url, title, text}]. cited: verbatim quote strings the report
    will cite — any page line containing one is kept verbatim. Exact-duplicate
    page texts collapse to a notice pointing at the first copy.
    """
    policy_fn = policy_fn or deterministic_policy
    store = store or HoldStore()
    cited = list(cited or ())

    out, stats = [], {
        "pages": len(pages), "chars_in": 0, "chars_out": 0,
        "keep_full": 0, "keep_quotes": 0, "notice": 0, "duplicates": 0,
        "held_refs": [],
    }
    seen_texts = {}  # normalized text -> url of first copy

    for page in pages:
        url, title = page.get("url", ""), page.get("title", "")
        text = page.get("text", "") or ""
        stats["chars_in"] += len(text)
        norm = re.sub(r"\s+", " ", text).strip().lower()
        if norm and norm in seen_texts:
            ref = store.hold(url, text)
            stats["held_refs"].append(ref)
            stats["duplicates"] += 1
            stats["notice"] += 1
            entry = {"url": url, "title": title, "decision": "notice",
                     "excerpt": f"[duplicate of {seen_texts[norm]}; full page held]",
                     "ref": ref, "lines_kept": 0,
                     "lines_total": len([l for l in text.split(chr(10)) if l.strip()])}
            out.append(entry)
            stats["chars_out"] += len(entry["excerpt"])
            continue
        seen_texts.setdefault(norm, url)

        decision, keep_idxs = policy_fn(page, task, cited)
        lines = [l for l in (ln.strip() for ln in text.split("\n")) if l]
        if decision == "keep_full":
            excerpt = text
            ref = None
            stats["keep_full"] += 1
        elif decision == "notice":
            ref = store.hold(url, text)
            stats["held_refs"].append(ref)
            excerpt = (f"[page dropped: {len(lines)} lines, {len(text)} chars; "
                       f"full page held]")
            stats["notice"] += 1
        else:  # keep_quotes
            excerpt = "\n".join(lines[i] for i in keep_idxs)
            ref = store.hold(url, text)
            stats["held_refs"].append(ref)
            excerpt = (f"[kept {len(keep_idxs)}/{len(lines)} lines; "
                       f"full page held]\n{excerpt}")
            stats["keep_quotes"] += 1

        entry = {"url": url, "title": title, "decision": decision,
                 "excerpt": excerpt, "lines_kept": len(keep_idxs),
                 "lines_total": len(lines)}
        if ref:
            entry["ref"] = ref
        out.append(entry)
        stats["chars_out"] += len(excerpt)

    stats["reduction_pct"] = (100.0 * (stats["chars_in"] - stats["chars_out"])
                              / stats["chars_in"]) if stats["chars_in"] else 0.0
    stats["tokens_in"] = estimate_tokens(" ".join(
        (p.get("text", "") or "") for p in pages))
    return out, stats
