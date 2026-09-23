"""Tests for agent_squeeze/research.py (plain asserts; pytest-free runner)."""
from agent_squeeze.research import squeeze_research_pages, deterministic_policy
from agent_squeeze.admit import HoldStore

TASK = "How do teams use Jev decision models to cut context cost?"


def test_boilerplate_page_notice():
    page = {"url": "u1", "title": "t",
            "text": ("Accept all cookies\nCookie policy\nSubscribe to our newsletter\n"
                     "Follow us on X\n© 2026 DataPress. All rights reserved.\n" * 4)}
    out, stats = squeeze_research_pages([page], TASK)
    assert out[0]["decision"] == "notice", out[0]["decision"]
    assert "ref" in out[0]
    assert stats["notice"] == 1


def test_cited_quote_kept_verbatim():
    q = '"We stopped summarizing the prefix entirely," says the SRE lead.'
    page = {"url": "u2", "title": "t",
            "text": "Accept all cookies\n" + q + "\nSome long filler sentence about "
                   "context costs and Jev decision models in production teams.\n" * 20}
    out, _ = squeeze_research_pages([page], TASK, cited=[q])
    assert out[0]["decision"] == "keep_quotes"
    assert q in out[0]["excerpt"]


def test_error_page_notice():
    page = {"url": "u3", "title": "404",
            "text": "404\nPage not found\nThe page you requested does not exist."}
    out, _ = squeeze_research_pages([page], TASK)
    assert out[0]["decision"] == "notice"


def test_exact_duplicate_collapses():
    text = ("Jev score_chunks judges each chunk with a noul question about "
            "whether the chunk is needed for the task.\n" * 20)
    pages = [{"url": "u4a", "title": "t", "text": text},
             {"url": "u4b", "title": "t", "text": text}]
    out, stats = squeeze_research_pages(pages, TASK)
    assert stats["duplicates"] == 1
    assert "duplicate of u4a" in out[1]["excerpt"]
    assert out[0]["decision"] == "keep_quotes"  # first copy judged on merits


def test_hold_roundtrip_byte_identical():
    text = "line one\nline two\n" * 50
    store = HoldStore()
    out, stats = squeeze_research_pages(
        [{"url": "u5", "title": "t", "text": "Accept all cookies\n" + text}], TASK, store=store)
    assert store.readmit(out[0]["ref"]) == "Accept all cookies\n" + text


def test_short_page_keep_full():
    page = {"url": "u6", "title": "t", "text": "Short dense note on Jev thresholds."}
    out, _ = squeeze_research_pages([page], TASK)
    assert out[0]["decision"] == "keep_full"
    assert "ref" not in out[0]


def test_noise_dropped_cited_kept():
    noise = "Reply 1: +1, following this thread"
    page = {"url": "u7", "title": "t",
            "text": (noise + "\n") * 10 + "We run Jev at threshold 0.6 on tool outputs only, keeping user text intact.\n" * 20}
    decision, keep = deterministic_policy(page, TASK, [])
    assert decision == "keep_quotes"
    kept = [l for i, l in enumerate(
        [ln.strip() for ln in page["text"].split("\n") if ln.strip()]) if i in keep]
    assert not any("+1" in l for l in kept), kept
