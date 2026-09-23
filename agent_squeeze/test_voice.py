"""Tests for agent_squeeze/voice.py (plain asserts; pytest-free runner)."""
from agent_squeeze.voice import squeeze_voice_dialogue, deterministic_policy
from agent_squeeze.admit import HoldStore


def test_backchannel_noticed():
    t = {"role": "user", "text": "Mm-hmm.\nYeah, right."}
    d, e = deterministic_policy(t, [])
    assert d == "notice", (d, e)
    assert e == "[backchannel]"


def test_filler_noticed():
    t = {"role": "user", "text": "Um, so, uh, well, you know, like—"}
    d, e = deterministic_policy(t, [])
    assert d == "notice", (d, e)
    assert e == "[filler]"


def test_filler_prefixed_real_turn_kept():
    # Fillers that lead into real intent are kept verbatim — nothing kept
    # is ever rewritten, and stripping prefixes would rewrite the turn.
    t = {"role": "user", "text": "Um, so, uh, I wanted to ask about changing my flight."}
    d, e = deterministic_policy(t, [])
    assert d == "keep_full", (d, e)


def test_interrupted_keeps_only_spoken():
    t = {"role": "agent", "text": "Sure, booking the flight to Austin for Friday morning—",
         "interrupted": True,
         "committed_text": "Sure, booking the flight to Austin for"}
    d, e = deterministic_policy(t, [])
    assert d == "keep_excerpt", (d, e)
    assert "committed_text" not in e  # no raw keys echoed; spoken text only
    assert "for Friday morning" not in e  # unspoken tail must not leak in
    assert e.startswith("[interrupted; spoke only]")


def test_interrupted_nothing_spoken_noticed():
    t = {"role": "agent", "text": "Let me check—", "interrupted": True}
    d, e = deterministic_policy(t, [])
    assert d == "notice", (d, e)
    assert e == "[interrupted; nothing spoken]"


def test_restated_repeat_collapses():
    first = {"role": "user", "text": "I need to change my flight to Thursday morning please."}
    again = {"role": "user", "text": "Can you please move my flight to Thursday morning?"}
    out, _ = squeeze_voice_dialogue([first, again])
    assert out[0]["decision"] == "keep_full", out[0]
    assert out[1]["decision"] == "notice", out[1]
    assert "restated" in out[1]["excerpt"]


def test_long_ramble_restatement_collapses():
    first = {"role": "user", "text": "I need to change my flight to Thursday morning please."}
    ramble = {"role": "user", "text": ("So basically what I'm trying to say is I need "
                                       "to change my flight, I want it on Thursday "
                                       "morning please, can you do that for me?")}
    out, _ = squeeze_voice_dialogue([first, ramble])
    assert out[1]["decision"] == "notice", out[1]
    assert "restated" in out[1]["excerpt"]


def test_correction_is_not_a_restatement():
    first = {"role": "user", "text": "I need to change my flight to Thursday morning please."}
    correction = {"role": "user", "text": "No wait — Thursday, not Friday! Thursday morning."}
    out, _ = squeeze_voice_dialogue([first, correction])
    assert out[1]["decision"] == "keep_full", out[1]


def test_tts_readback_excerpted_to_fact_lines():
    t = {"role": "agent", "text": ("Let me read back the updated itinerary for you.\n"
                                   "Departing San Francisco International at 7:05 AM.\n"
                                   "Arriving Austin Bergstrom at 12:40 PM.\n"
                                   "Boarding begins 30 minutes before departure.")}
    out, _ = squeeze_voice_dialogue([t])
    assert out[0]["decision"] == "keep_excerpt", out[0]["decision"]
    assert "7:05 AM" in out[0]["excerpt"]
    assert "12:40 PM" in out[0]["excerpt"]
    assert "read back the updated itinerary" not in out[0]["excerpt"]
    assert "ref" in out[0]  # full readback held, byte-identical recoverable


def test_commitment_kept_verbatim():
    t = {"role": "agent", "text": "Done — appointment confirmed for Thursday 10:30 AM. Reference number AX-4471."}
    out, _ = squeeze_voice_dialogue([t])
    assert out[0]["decision"] == "keep_full"
    assert "AX-4471" in out[0]["text"]


def test_confirmation_reask_noticed():
    fact = {"role": "user", "text": "My phone number is 415-555-0132, that's the best callback number."}
    reask = {"role": "agent", "text": "Just to confirm, what's the best phone number to reach you?"}
    out, _ = squeeze_voice_dialogue([fact, reask])
    assert out[0]["decision"] == "keep_full", out[0]["decision"]
    assert out[1]["decision"] == "notice", out[1]["decision"]


def test_noisy_fact_turn_excerpted_held_roundtrip():
    noise = "Well, um, let me think about that for a moment. "
    t = {"role": "user", "text": noise * 6 + "The delivery address is 4820 Meridian Ave, call when you're close."}
    store = HoldStore()
    out, stats = squeeze_voice_dialogue([t], store=store)
    assert out[0]["decision"] == "keep_excerpt", out[0]["decision"]
    assert "4820 Meridian Ave" in out[0]["excerpt"]
    ref = out[0]["ref"]
    assert store.readmit(ref) == t["text"]
    assert stats["held"] == 1
