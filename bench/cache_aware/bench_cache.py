"""Offline benchmark: naive squeeze vs cache-aware squeeze.

No paid endpoints: keep/drop comes from a deterministic boilerplate policy
(dedup + noise patterns). The point is structural — how many leading tokens
stay byte-identical (i.e. serve from prompt cache at 0.1x on the next call)
under each strategy — not Jev judge quality, which is covered by bench/score.py.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from agent_squeeze import cache, messages, squeeze
from gen_monitoring import build as build_monitoring

REPO = os.path.join(os.path.dirname(__file__), "../..")
INPUTS = ["sre_incident.json", "mixed_grind.json", "github_triage.json"]
SYNTHETIC = ("synthetic_monitoring.json", build_monitoring())
PROTECT_TOKENS = 1024
BASE_PER_MTOK = 3.0  # Anthropic Sonnet-class input $/M

NOISE = re.compile(
    r"(heartbeat|health ?check|^\s*200 OK|ok$|uptime|cpu usage|"
    r"directory listing|total \d+|drwx|no changes|nothing to commit)",
    re.IGNORECASE | re.MULTILINE)


def deterministic_policy(chunk_texts, task):
    """p=0.0 drop for chunks dominated by repeated boilerplate lines or noise,
    else p=1.0. Mirrors the Jev FRAMING guidance (drop boilerplate: repeated
    identical outputs, routine listings, heartbeats). Line-level dedup catches
    boilerplate that repeats across chunks."""
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


def run_one(path, msgs=None, task="", protect_tokens=PROTECT_TOKENS):
    if msgs is None:
        doc = json.load(open(path))
        task = doc.get("scenario", doc.get("id", ""))
        msgs = messages.from_openai(doc)
        label = os.path.basename(path)
    else:
        label = path
    before = messages.transcript_tokens(msgs)

    # naive: squeeze the whole transcript (Jev would too, without protection)
    naive, _ = cache.squeeze_with_policy(msgs, task, deterministic_policy)
    # cache-aware: protect the first 1024 tokens byte-identical
    aware, stats = cache.squeeze_cache_aware(
        msgs, task, protect_tokens=protect_tokens,
        policy_fn=deterministic_policy)

    def after(ms):
        return messages.transcript_tokens(ms)

    cost_naive = cache.next_turn_cost_model(
        naive, msgs, BASE_PER_MTOK, protected=0)
    cost_aware = cache.next_turn_cost_model(aware, msgs, BASE_PER_MTOK)
    session = cache.session_cost_model(aware, msgs, BASE_PER_MTOK, turns=10)

    row = {
        "input": label,
        "tokens_before": before,
        "naive_after": after(naive),
        "naive_reduction_pct": round(100 * (before - after(naive)) / max(1, before), 2),
        "aware_after": after(aware),
        "aware_reduction_pct": stats["reduction_pct"],
        "stable_prefix_tokens": cost_aware["stable_tokens"],
        "next_turn_cost_naive_usd": cost_naive["cost_next_naive_usd"],
        "next_turn_cost_aware_usd": cost_aware["cost_next_aware_usd"],
        "next_turn_saving_pct": cost_aware["cache_saving_pct"],
        "session10_aware_usd": session["session_aware_usd"],
        "session10_naive_usd": session["session_naive_usd"],
        "session10_saving_pct": session["session_saving_pct"],
    }
    return row


def main():
    rows = [run_one(os.path.join(REPO, "bench/inputs", f)) for f in INPUTS]
    rows.append(run_one(*SYNTHETIC, protect_tokens=4096))  # prefix covers early turns
    lines = [
        "# cache-aware squeeze — offline benchmark",
        "",
        "Deterministic boilerplate policy (dedup + noise), no Jev calls. "
        "protect_tokens=1024 (4096 for the synthetic input, so the prefix "
        "covers early tool turns). Next-turn cost model: Anthropic ratios "
        "(cache read 0.1x, write 1.25x); naive assumed to rewrite the prefix "
        "\u2192 cache miss every turn. On the three real/adversarial inputs the "
        "deterministic policy legitimately drops nothing (their tool outputs "
        "are all unique), so those rows just show the structural behavior: "
        "no prune \u2192 fully byte-stable prefix.",
        "",
        "| input | before | naive after | naive −% | aware after | aware −% "
        "| stable prefix | next-turn naive $ | next-turn aware $ | saving "
        "| 10-turn naive $ | 10-turn aware $ | 10-turn saving |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['input']} | {r['tokens_before']} | {r['naive_after']} | "
            f"{r['naive_reduction_pct']}% | {r['aware_after']} | "
            f"{r['aware_reduction_pct']}% | {r['stable_prefix_tokens']} | "
            f"{r['next_turn_cost_naive_usd']:.4f} | "
            f"{r['next_turn_cost_aware_usd']:.4f} | "
            f"{r['next_turn_saving_pct']}% | "
            f"{r['session10_naive_usd']:.4f} | {r['session10_aware_usd']:.4f} | "
            f"{r['session10_saving_pct']}% |")
    lines += [
        "",
        "## takeaway",
        "",
        "Cache-aware squeeze trades a hair of reduction (the protected prefix "
        "is untouchable: 13.97% vs 14.21% naive) for a byte-identical prefix "
        "that the provider cache can serve at 0.1x. The win compounds over a "
        "session: a cache-breaking prune pays full price on the prefix every "
        "turn, while the protected prefix pays one write + cheap reads. "
        "Summarizing/rewriting the prefix — the #1 cache-killer per the "
        "prompt-caching playbook — maximizes this miss cost. Note the existing "
        "squeezer is already half-way there: it never rewrites user/assistant "
        "text; cache-aware mode extends the guarantee to tool messages inside "
        "the protected zone.",
    ]
    report = "\n".join(lines) + "\n"
    out = os.path.join(os.path.dirname(__file__), "REPORT.md")
    open(out, "w").write(report)
    print(report)


if __name__ == "__main__":
    main()
