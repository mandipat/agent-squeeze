"""CLI: squeeze a single transcript or a whole fleet of agents.

Headless by design (for autonomous runs): key comes from OPENROUTER_API_KEY.

  python -m agent_squeeze.cli squeeze agent.jsonl --task "migrate auth to JWT" -o out.json
  python -m agent_squeeze.cli fleet fe.jsonl be.jsonl infra.jsonl \\
      --names frontend,backend,infra -o fleet_out.json
      # --task defaults to the first user message (the agent's objective).
      # Only override to describe the OBJECTIVE, never the compression job:
      # the task defines what "needed" means to the judge.
  python -m agent_squeeze.cli fleet ... --needles needles.txt   # verify survival
"""
import argparse
import json
import os
import sys

from .messages import infer_task, load_any, transcript_tokens
from .squeeze import squeeze_transcript
from .cache import squeeze_cache_aware
from .fleet import squeeze_fleet
from .admit import admit_session, PersistentHoldStore


def _save(path, payload):
    with open(path, "w") as f:
        json.dump(payload, f, indent=1)
    print(f"wrote {path}")


def _check_needles(messages, needles_path):
    needles = [l.strip() for l in open(needles_path) if l.strip()]
    blob = json.dumps(messages).lower()
    lost = [n for n in needles if n.lower() not in blob]
    print(f"needles: {len(needles) - len(lost)}/{len(needles)} survived")
    for n in lost:
        print(f"  LOST: {n}")
    return not lost


def cmd_squeeze(args):
    messages = load_any(args.input)
    task = args.task or infer_task(messages)
    print(f"task: {task[:120]}{'...' if len(task) > 120 else ''}")
    print(f"loaded {len(messages)} messages, ~{transcript_tokens(messages)} tokens")
    if args.protect_prefix > 0:
        out, stats = squeeze_cache_aware(
            messages, task, protect_tokens=args.protect_prefix,
            threshold=args.threshold)
        print(f"cache-aware: prefix of ~{stats['protected_tokens']} tokens kept "
              f"byte-identical (cache-safe); dynamic tail "
              f"{stats['tokens_before'] - stats['protected_tokens']} -> "
              f"{stats['dynamic_tokens_after']} tokens; "
              f"reduction {stats['reduction_pct']}%")
    else:
        out, stats = squeeze_transcript(messages, task, args.threshold)
        print(f"reduction: {stats['reduction_pct']}% "
              f"({stats['tokens_before']} -> {stats['tokens_after']} tokens), "
              f"${stats['cost_usd']:.6f} in {stats['latency_s']}s")
    _save(args.output, {"messages": out, "stats": stats})
    if args.needles:
        ok = _check_needles(out, args.needles)
        sys.exit(0 if ok else 1)


def cmd_admit_readmit(args):
    # Resolve a hold ref (e.g. ⟦held:bash/0003⟧) issued by the admit gate,
    # the admit-time hook, or /v1/admit-batch. Byte-identical to the held
    # payload; unknown refs print an error and exit 1.
    store = PersistentHoldStore()
    try:
        text = store.readmit(args.ref)
    except KeyError:
        print(f"admit-readmit: unknown ref {args.ref}", file=sys.stderr)
        sys.exit(1)
    sys.stdout.write(text)


def cmd_admit(args):
    # Input: JSONL, one {"name": ..., "text": ...} per line (a tool-result
    # log). Each result is judged before context entry; trimmed/noticed/held
    # payloads persist verbatim in the hold store for /v1/readmit.
    results = []
    with open(args.input) as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                results.append({"name": r.get("name") or "tool",
                                "text": r.get("text") or ""})
    store = PersistentHoldStore()
    admissions, stats = admit_session(results, args.task or "", store=store)
    print(f"admit: {stats['results']} results, "
          f"{stats['input_chars']} -> {stats['admitted_chars']} chars "
          f"({stats['reduction_pct']}% reduction), "
          f"{stats['held_chars']} chars held, decisions={stats['decisions']}")
    out = {"admissions": [
        {"name": r["name"], "decision": a.decision,
         "admitted_text": a.text, "ref": a.ref, "held_chars": a.held_chars}
        for r, a in zip(results, admissions)], "stats": stats}
    _save(args.output, out)
    if args.needles:
        blob = json.dumps(out["admissions"]).lower()
        needles = [l.strip() for l in open(args.needles) if l.strip()]
        lost = [n for n in needles if n.lower() not in blob]
        print(f"needles: {len(needles) - len(lost)}/{len(needles)} survived")
        sys.exit(0 if not lost else 1)


def cmd_fleet(args):
    names = args.names.split(",") if args.names else \
        [os.path.splitext(os.path.basename(p))[0] for p in args.inputs]
    transcripts = {n: load_any(p) for n, p in zip(names, args.inputs)}
    for n, m in transcripts.items():
        print(f"agent '{n}': {len(m)} messages, ~{transcript_tokens(m)} tokens")
    task = args.task or None  # None -> per-agent task inferred from each
    # agent's own first user message (the autonomous-fleet default)
    if task:
        print(f"task: {task[:120]}{'...' if len(task) > 120 else ''}")
    else:
        print("task: per-agent (inferred from each agent's first user message)")
    squeezed, report = squeeze_fleet(transcripts, task, args.threshold,
                                     protect_tokens=args.protect_prefix)
    r = report
    print(f"fleet: {r['fleet_tokens_before']} -> {r['fleet_tokens_after']} tokens "
          f"({r['fleet_reduction_pct']}% reduction), "
          f"{r['global_exact_duplicates']} cross-agent duplicates, "
          f"${r['total_cost_usd']:.6f} in {r['latency_s']}s")
    _save(args.output, {"transcripts": squeezed, "report": report})
    if args.needles:
        # Fleet-level check: each needle must survive SOMEWHERE in the fleet.
        # (Per-agent checks are wrong — a needle planted in backend's transcript
        # will never be in frontend's.)
        ok = _check_needles(
            [m for msgs in squeezed.values() for m in msgs], args.needles)
        sys.exit(0 if ok else 1)


def cmd_ttl(args):
    """Recommend a prompt-cache TTL from a session's inter-turn gap pattern."""
    from agent_squeeze import ttl as ttl_mod
    gaps = [float(g.strip()) for g in args.gaps.split(",") if g.strip()]
    if not gaps:
        sys.exit("error: --gaps must be a non-empty comma-separated list of seconds")
    if args.prefix < 0 or args.dynamic < 0 or args.price <= 0:
        sys.exit("error: --prefix/--dynamic must be >= 0 and --price > 0")
    rec = ttl_mod.recommend_ttl(gaps, args.prefix, args.dynamic, args.price)
    verdict = (f"recommended TTL: {rec['recommended']} "
               f"(5min ${rec['cost_5min_usd']:.4f} vs "
               f"1hour ${rec['cost_1hour_usd']:.4f}; "
               f"saves {rec['saving_pct']}%, "
               f"hit rates {rec['hit_rate_5min']}/{rec['hit_rate_1hour']})")
    if args.output:
        json.dump({"gaps_sec": gaps, "prefix_tokens": args.prefix,
                   "dynamic_tokens": args.dynamic,
                   "base_per_mtok": args.price,
                   "recommendation": rec, "verdict": verdict},
                  open(args.output, "w"), indent=2)
    print(verdict)


def main():
    ap = argparse.ArgumentParser(prog="agent_squeeze")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("squeeze", help="compress one agent transcript")
    s.add_argument("input"); s.add_argument("-o", "--output", required=True)
    s.add_argument("--task", required=False, default=None,
                   help="the agent's OBJECTIVE (not a compression instruction). "
                        "Defaults to the transcript's first user message.")
    s.add_argument("--threshold", type=float, default=0.5)
    s.add_argument("--protect-prefix", type=int, default=0,
                   help="keep the first N tokens byte-identical (prompt-cache "
                        "safe); only the tail is squeezed. 0 = off.")
    s.add_argument("--needles", default=None)
    f = sub.add_parser("fleet", help="compress N agents running simultaneously")
    f.add_argument("inputs", nargs="+"); f.add_argument("-o", "--output", required=True)
    f.add_argument("--task", required=False, default=None,
                   help="the agents' OBJECTIVE. Defaults to the first user message.")
    f.add_argument("--names", default=None)
    f.add_argument("--threshold", type=float, default=0.5)
    f.add_argument("--protect-prefix", type=int, default=0,
                   help="per agent: keep the first N tokens byte-identical "
                        "(prompt-cache safe); only each tail is squeezed. "
                        "0 = off.")
    f.add_argument("--needles", default=None)
    a = sub.add_parser("admit", help="gate tool results before context entry")
    a.add_argument("input", help="JSONL: one {\"name\": ..., \"text\": ...} per line")
    a.add_argument("-o", "--output", required=True)
    a.add_argument("--task", required=False, default=None,
                   help="the agent's OBJECTIVE (what the results are judged against).")
    a.add_argument("--needles", default=None)
    r = sub.add_parser("admit-readmit",
                       help="resolve a hold ref issued by the admit gate")
    r.add_argument("ref", help="e.g. ⟦held:bash/0003⟧")
    t = sub.add_parser("squeeze-ttl",
                       help="recommend 5-min vs 1-hour prompt-cache TTL "
                            "from a session's inter-turn gap pattern")
    t.add_argument("--gaps", required=True,
                   help="comma-separated inter-turn gaps in seconds, e.g. "
                        "'30,45,1200' (one value per turn)")
    t.add_argument("--prefix", type=int, required=True,
                   help="protected prefix tokens (the cache entry)")
    t.add_argument("--dynamic", type=int, default=0,
                   help="dynamic tail tokens per turn (always full price)")
    t.add_argument("--price", type=float, default=3.0,
                   help="base model price $/MTok input (Sonnet-class: 3.0)")
    t.add_argument("-o", "--output", required=False, default=None,
                   help="write the full recommendation JSON here")
    args = ap.parse_args()
    {"squeeze": cmd_squeeze, "fleet": cmd_fleet, "admit": cmd_admit,
     "admit-readmit": cmd_admit_readmit, "squeeze-ttl": cmd_ttl}[args.cmd](args)


if __name__ == "__main__":
    main()
