"""Tests for agent_squeeze/support.py (plain asserts; pytest-free runner)."""
from agent_squeeze.support import squeeze_support_thread, deterministic_policy
from agent_squeeze.admit import HoldStore


def test_greeting_turn_noticed():
    t = {"role": "agent", "text": "Hello! Thanks for reaching out today.\nHow can I help you?"}
    d, e = deterministic_policy(t, set(), set())
    assert d == "notice", (d, e)
    assert e == "[pleasantries]"


def test_template_apology_noticed():
    t = {"role": "agent",
         "text": "I am so sorry for the inconvenience.\nI completely understand how frustrating this is."}
    d, e = deterministic_policy(t, set(), set())
    assert d == "notice", (d, e)


def test_resolution_summary_keep_full():
    t = {"role": "agent",
         "text": "Resolution: refund of $129.99 issued for order #88421-XYZ.\nCase number CASE-2026-9931 is now closed."}
    out, stats = squeeze_support_thread([t])
    assert out[0]["decision"] == "keep_full"
    assert "CASE-2026-9931" in out[0]["text"]
    assert "$129.99" in out[0]["text"]


def test_first_verification_keep_full_second_noticed():
    first = {"role": "agent", "text": "Please verify your account number and date of birth."}
    facts = {"role": "customer", "text": "Account 771-2044, DOB 03/14/1989."}
    again = {"role": "agent", "text": "Can you confirm your account number once more?"}
    out, _ = squeeze_support_thread([first, facts, again])
    assert out[0]["decision"] == "keep_full", out[0]["decision"]
    assert out[2]["decision"] == "notice", out[2]["decision"]
    assert "facts established earlier" in out[2]["excerpt"]


def test_duplicate_script_step_collapses():
    step = ("Let me walk you through the steps:\n"
            "1. Unplug the router for 30 seconds.\n2. Plug it back in and wait.\n"
            "3. Test the connection and report back.")
    turns = [{"role": "agent", "text": step}, {"role": "agent", "text": step}]
    out, stats = squeeze_support_thread(turns)
    assert out[1]["decision"] == "notice", out[1]["decision"]
    assert stats["notice"] >= 1


def test_hold_roundtrip_byte_identical():
    long_step = "Troubleshooting step detail line.\n" * 60 + "Refund of $42.00 processed.\n"
    t = {"role": "agent", "text": long_step}
    store = HoldStore()
    out, _ = squeeze_support_thread([t], store=store)
    assert out[0]["decision"] == "keep_excerpt"
    assert "$42.00" in out[0]["excerpt"]
    assert store.readmit(out[0]["ref"]) == long_step


def test_closing_pleasantries_noticed():
    t = {"role": "agent",
         "text": "Is there anything else I can help you with today?\nHave a great day!"}
    d, e = deterministic_policy(t, set(), set())
    assert d == "notice", (d, e)
