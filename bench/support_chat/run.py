"""Support-chatbot compression benchmark.

Synthetic 14-turn support thread: greeting, identity verification, account
lookup, repeated re-verification after a "handoff", long troubleshooting
script (repeated twice), template apologies, hold music, and a resolution
summary with a refund amount + case number.

Needles: account number (ACC-77410), case number (CASE-2026-9931),
refund amount ($129.99). The resolution turn must survive verbatim.
"""
import json
import sys

sys.path.insert(0, ".")
from agent_squeeze.support import squeeze_support_thread  # noqa: E402
from agent_squeeze.admit import HoldStore  # noqa: E402

APOLOGY = ("I am so sorry for the inconvenience. I completely understand "
           "how frustrating this must be for you.")
VERIFY = ("For your security, please confirm your full account number and "
          "date of birth so I can pull up your records.")
SCRIPT = ("\n".join([
    "Here are the troubleshooting steps we'll run together:",
    "1. Unplug the router from power for 30 seconds.",
    "2. Plug it back in and wait for all lights to turn green.",
    "3. Restart the set-top box by holding the power button for 10 seconds.",
    "4. Run a speed test at speedtest.example.com and tell me the result.",
    "5. If the download is still under 50 Mbps, we'll escalate to a technician.",
]))
RESOLUTION = ("Resolution: refund of $129.99 issued for order #88421-XYZ; "
              "case number CASE-2026-9931 is now closed. Confirmation email "
              "sent to the address on file.")

TURNS = [
    {"role": "agent", "text": "Hello! Thanks for reaching out to Northline Support. How can I help you today?"},
    {"role": "customer", "text": "My internet has been dropping every evening for a week. This is unacceptable."},
    {"role": "agent", "text": VERIFY},
    {"role": "customer", "text": "Account ACC-77410. DOB 03/14/1989. Name is on the bill already."},
    {"role": "agent", "text": APOLOGY},
    {"role": "agent", "text": SCRIPT},
    {"role": "customer", "text": "Done. Still dropping. Speed test shows 12 Mbps."},
    {"role": "agent", "text": APOLOGY + "\nPlease hold for a moment while I review your line."},
    {"role": "agent", "text": "I need to transfer you to the advanced team. " + VERIFY.lower()},
    {"role": "agent", "text": SCRIPT},  # repeated script, verbatim duplicate
    {"role": "customer", "text": "We already did all of that. It still drops at 8pm every day."},
    {"role": "agent", "text": "One moment please while I check the outage map."},
    {"role": "agent", "text": RESOLUTION},
    {"role": "agent", "text": "Is there anything else I can help you with today? Have a great day!"},
]


def main():
    store = HoldStore()
    out, stats = squeeze_support_thread(TURNS, store=store)
    kept = " ".join(e.get("text", "") + e.get("excerpt", "") for e in out)
    needles = {"ACC-77410": "ACC-77410" in kept,
               "CASE-2026-9931": "CASE-2026-9931" in kept,
               "$129.99": "$129.99" in kept}
    # Resolution summary must be kept verbatim (never excerpted or noticed).
    res_turns = [e for e in out if "CASE-2026-9931" in e.get("text", "")]
    assert res_turns and all(e["decision"] == "keep_full" for e in res_turns), \
        "resolution summary must be keep_full + verbatim"
    report = {"stats": stats,
              "needles": needles,
              "decisions": [(e["role"], e["decision"]) for e in out]}
    print(json.dumps(report, indent=2))
    missing = [k for k, v in needles.items() if not v]
    if missing:
        print(f"NEEDLE LOST: {missing}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
