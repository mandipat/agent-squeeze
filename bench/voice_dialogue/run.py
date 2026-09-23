"""Voice-agent dialogue compression benchmark.

Synthetic 16-turn spoken dialogue: booking intent, fillers, backchannels,
a barge-in fragment (agent cut off mid-sentence — only the committed/spoken
fragment may enter context), a restated repeat, a confirmation re-ask, a
noisy fact turn (delivery address buried in hesitation), and a commitment.

Needles: reference AX-4471, 4820 Meridian Ave, Thursday 10:30 AM.
The barge-in honesty check: the unspoken tail ("for Friday morning") must
NOT appear in the output — only what the agent actually spoke.
"""
import sys

sys.path.insert(0, ".")
from agent_squeeze.voice import squeeze_voice_dialogue  # noqa: E402
from agent_squeeze.admit import HoldStore  # noqa: E402

TURNS = [
    {"role": "agent", "text": "Hi, thanks for calling Northline Travel. How can I help?"},
    {"role": "user", "text": "Um, uh, hi, I need to change my flight."},
    {"role": "agent", "text": "Of course, I can help with that."},
    {"role": "user", "text": "I need to change my flight to Thursday morning please."},
    {"role": "agent", "text": "Mm-hmm, Thursday morning, got it."},
    {"role": "user", "text": "Yeah, right, Thursday morning would be best."},
    {"role": "agent", "text": "Sure, booking the flight to Austin for Friday morning—",
     "interrupted": True,
     "committed_text": "Sure, booking the flight to Austin for"},
    {"role": "user", "text": "No wait — Thursday, not Friday! Thursday morning."},
    {"role": "agent", "text": "Just to confirm, which day did you say for the flight?"},
    {"role": "agent", "text": ("Just to confirm before I proceed — you said Thursday "
                               "morning, is that right? I want to make sure I have "
                               "the correct day and time on the booking before I "
                               "finalize the change.")},
    {"role": "user", "text": "Can you please move my flight to Thursday morning?"},
    {"role": "user", "text": ("So basically what I'm trying to say is I need to "
                              "change my flight, I want it on Thursday morning "
                              "please, can you do that for me?")},
    {"role": "user", "text": ("Well, um, let me think about that for a moment. "
                              "Hmm, so, the delivery address is 4820 Meridian Ave, "
                              "call when you're close. Yeah.")},
    {"role": "agent", "text": "Got it."},
    {"role": "user", "text": ""},
    {"role": "agent", "text": "One moment while I check availability."},
    {"role": "agent", "text": ("Let me read back the updated itinerary for you.\n"
                               "Departing San Francisco International at 7:05 AM.\n"
                               "Arriving Austin Bergstrom at 12:40 PM.\n"
                               "Flight 2214, seat 14A, one checked bag included.\n"
                               "Boarding begins 30 minutes before departure, and your "
                               "confirmation email is on its way.")},
    {"role": "agent", "text": ("Done — flight changed to Thursday 10:30 AM. "
                               "Reference number AX-4471.")},
    {"role": "agent", "text": "Is there anything else? Have a great day!"},
]


def main():
    store = HoldStore()
    out, stats = squeeze_voice_dialogue(TURNS, store=store)
    kept = " ".join(e.get("text", "") + e.get("excerpt", "") for e in out)
    held = " ".join(store.readmit(e["ref"]) for e in out if "ref" in e)
    all_text = kept + " " + held

    needles = {"AX-4471": "AX-4471" in all_text,
               "4820 Meridian Ave": "4820 Meridian Ave" in all_text,
               "Thursday 10:30 AM": "Thursday 10:30 AM" in all_text}
    barge_honest = "for Friday morning" not in kept  # unspoken tail stays out
    by_decision = {}
    for e in out:
        by_decision[e["decision"]] = by_decision.get(e["decision"], 0) + 1

    print(f"tokens: {stats['tokens_in']} -> {stats['tokens_out']} "
          f"(-{stats['reduction_pct']}%)")
    print(f"decisions: {by_decision}, held: {stats['held']}")
    print(f"needles: {needles}")
    print(f"barge-in honest (unspoken tail absent): {barge_honest}")

    assert all(needles.values()), f"needle lost: {needles}"
    assert barge_honest, "unspoken barge-in tail leaked into context"
    # Commitment kept verbatim, never excerpted.
    commit = [e for e in out if "AX-4471" in e.get("text", "")]
    assert commit and commit[0]["decision"] == "keep_full", "commitment not verbatim"
    print("PASS: voice dialogue benchmark")


if __name__ == "__main__":
    main()
