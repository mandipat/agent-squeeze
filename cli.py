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
from .fleet import squeeze_fleet


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
    out, stats = squeeze_transcript(messages, task, args.threshold)
    print(f"reduction: {stats['reduction_pct']}% "
          f"({stats['tokens_before']} -> {stats['tokens_after']} tokens), "
          f"${stats['cost_usd']:.6f} in {stats['latency_s']}s")
    _save(args.output, {"messages": out, "stats": stats})
    if args.needles:
        ok = _check_needles(out, args.needles)
        sys.exit(0 if ok else 1)


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
    squeezed, report = squeeze_fleet(transcripts, task, args.threshold)
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


def main():
    ap = argparse.ArgumentParser(prog="agent_squeeze")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("squeeze", help="compress one agent transcript")
    s.add_argument("input"); s.add_argument("-o", "--output", required=True)
    s.add_argument("--task", required=False, default=None,
                   help="the agent's OBJECTIVE (not a compression instruction). "
                        "Defaults to the transcript's first user message.")
    s.add_argument("--threshold", type=float, default=0.5)
    s.add_argument("--needles", default=None)
    f = sub.add_parser("fleet", help="compress N agents running simultaneously")
    f.add_argument("inputs", nargs="+"); f.add_argument("-o", "--output", required=True)
    f.add_argument("--task", required=False, default=None,
                   help="the agents' OBJECTIVE. Defaults to the first user message.")
    f.add_argument("--names", default=None)
    f.add_argument("--threshold", type=float, default=0.5)
    f.add_argument("--needles", default=None)
    args = ap.parse_args()
    {"squeeze": cmd_squeeze, "fleet": cmd_fleet}[args.cmd](args)


if __name__ == "__main__":
    main()
