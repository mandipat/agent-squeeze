"""agent_squeeze quickstart: squeeze a 2-agent fleet with NO API key.

The free deterministic policy below stands in for TypeSafe Jev (it drops
chunks dominated by repeated boilerplate / noise, exactly what the Jev
FRAMING guidance asks for). With a real OPENROUTER_API_KEY, swap in the
Jev judge — nothing else changes.

    python quickstart/run.py
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_squeeze.squeeze as squeeze_mod
from agent_squeeze.fleet import squeeze_fleet

NOISE = re.compile(
    r"(heartbeat ok|npm notice|added package-|^# Query_time: 0\.00|^total \d+|^drwx)",
    re.IGNORECASE | re.MULTILINE)


def free_policy(chunk_texts, task):
    """p=0.0 for boilerplate/noise-dominated chunks, else p=1.0. $0 cost."""
    seen_lines = set()
    probs = []
    for c in chunk_texts:
        lines = [ln.strip() for ln in c.split("\n") if ln.strip()]
        if not lines:
            probs.append(0.0)
            continue
        dup = sum(1 for ln in lines if ln in seen_lines)
        noise = sum(1 for ln in lines if NOISE.search(ln) and len(ln) < 200)
        frac = (dup + noise) / len(lines)
        probs.append(0.0 if frac >= 0.6 else 1.0)
        for ln in lines:
            seen_lines.add(ln)
    return probs, 0.0


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    doc = json.load(open(os.path.join(here, "fleet.json")))
    transcripts = doc["transcripts"]

    # Free path: swap the Jev judge for the deterministic policy.
    squeeze_mod.jev.score_chunks = free_policy
    squeezed, report = squeeze_fleet(transcripts)

    print("=== agent_squeeze quickstart (free policy, no API key) ===")
    for agent, st in report["agents"].items():
        print(f"  {agent:9s} {st['tokens_before']:6d} -> {st['tokens_after']:6d} "
              f"tokens ({st['reduction_pct']:5.1f}%), "
              f"${st['cost_usd']:.6f} in {st['latency_s']}s")
    print(f"  fleet     {report['fleet_tokens_before']:6d} -> "
          f"{report['fleet_tokens_after']:6d} tokens "
          f"({report['fleet_reduction_pct']:.1f}% reduction), "
          f"{report['global_exact_duplicates']} cross-agent duplicates, "
          f"${report['total_cost_usd']:.6f} total")

    needles = json.load(open(os.path.join(here, "needles.json")))
    print("\nneedle check (must all survive):")
    ok_all = True
    for agent, msgs in squeezed.items():
        blob = json.dumps(msgs).lower()
        lost = [n for n in needles.get(agent, []) if n.lower() not in blob]
        ok = not lost
        ok_all &= ok
        print(f"  {agent:9s} {'OK' if ok else 'LOST: ' + ', '.join(lost)}")
    if not ok_all:
        sys.exit(1)
    print("\nNext: set OPENROUTER_API_KEY and re-run with the real Jev judge,")
    print("or start the service:  agent-squeeze-serve --port 8765")


if __name__ == "__main__":
    main()
