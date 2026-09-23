"""Admit-time gate benchmark: synthetic tool results gated before context entry.

Offline, deterministic (no Jev calls, no paid endpoints). Measures:
  - admitted chars vs input chars (the write-time reduction)
  - needle recall: every needle line is present in admitted text OR
    recoverable byte-identically via the hold store
  - error retention: all error lines kept verbatim in admitted text
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from agent_squeeze.admit import HoldStore, admit_session  # noqa: E402


def build_results():
    """(name, text) tool results; needles embedded at known positions."""
    needles = {
        "poll_top": "NEEDLE-A1: deploy token ghp_rotated_2026Q3",
        "poll_mid": "NEEDLE-A2: rollback commit abc123def456",
        "poll_bot": "NEEDLE-A3: feature flag ff_killswitch=true",
    }
    poll = []
    for i in range(60):
        poll.append(f"poll round {i}: 200 OK, 14 services healthy, p99=41ms")
    poll.insert(2, needles["poll_top"])
    poll.insert(30, "ERROR: payment-worker OOMKilled, restart_count=3")
    poll.insert(31, needles["poll_mid"])
    poll.insert(58, needles["poll_bot"])
    poll_result = "\n".join(poll)

    records = "\n".join(
        json.dumps({"id": i, "status": "ok", "region": "us-west-2",
                    "latency_ms": 20 + i % 40}) for i in range(2000))
    records += "\nNEEDLE-B1: failed record id=1337 reason=schema_v9_mismatch"

    heartbeat = "\n".join("heartbeat ok" for _ in range(800))

    error = ("Traceback (most recent call last):\n"
             '  File "deploy.py", line 88, in main\n'
             "    push(image)\n"
             "ConnectionError: registry timeout after 30s\n") + "x\n" * 500

    return [
        ("poll", poll_result),
        ("query", records),
        ("watch", heartbeat),
        ("ls", "app.py\ndeploy.py\nREADME.md\n"),
        ("deploy", error),
    ], needles, ["OOMKilled", "schema_v9_mismatch", "registry timeout"]


def main():
    results, needles, error_bits = build_results()
    store = HoldStore()
    admissions, stats = admit_session(
        results, task="deploy the service and verify health", store=store)

    print(f"results={stats['results']} input_chars={stats['input_chars']:,} "
          f"admitted_chars={stats['admitted_chars']:,} "
          f"reduction={stats['reduction_pct']}%")
    print("decisions:", stats["decisions"])
    print(f"held_chars={stats['held_chars']:,} refs={len(store.held_refs())}")

    admitted_text = "\n".join(a.text for a in admissions)
    # needle recall: present in admitted text OR recoverable via hold
    missed = []
    for key, needle in needles.items():
        if needle in admitted_text:
            continue
        recovered = any(needle in store.readmit(r)
                        for r in store.held_refs())
        if not recovered:
            missed.append(key)
    print(f"needles: {len(needles) - len(missed)}/{len(needles)} recoverable"
          + ("" if not missed else f" MISSED: {missed}"))

    # error retention: all error bits must be in admitted text verbatim
    lost_err = [b for b in error_bits if b not in admitted_text]
    print(f"errors kept verbatim: {len(error_bits) - len(lost_err)}/"
          f"{len(error_bits)}" + ("" if not lost_err else f" LOST: {lost_err}"))

    # hold roundtrip integrity: each held blob byte-identical to original
    by_name = {name: text for name, text in results}
    bad = 0
    for a in admissions:
        if a.ref:
            name = a.ref[6:].split("/")[0]
            if store.readmit(a.ref) != by_name[name]:
                bad += 1
    print(f"hold roundtrips byte-identical: "
          f"{len(store.held_refs()) - bad}/{len(store.held_refs())}")
    assert bad == 0, f"{bad} hold blobs corrupted"

    assert not missed, f"needles lost: {missed}"
    assert not lost_err, f"errors lost: {lost_err}"
    print("OK")


if __name__ == "__main__":
    main()
