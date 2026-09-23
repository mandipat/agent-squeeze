"""Public benchmark harness: one command replays the offline paper.

Every bench runs as a subprocess from the repo root with PYTHONPATH=<root>,
so ``agent-squeeze bench --all`` works from any cwd. Nothing in the default
set touches a paid endpoint — all benches use deterministic stub judges
(see bench/overnight/PROGRESS.md for the published numbers). The keyed
bench (live Jev prune) is registered but skipped unless ``--include-keyed``
is passed explicitly, so an accidental full-matrix run can never burn API
budget.
"""
import json
import os
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TAIL_LINES = 6

# name -> (script relative to REPO_ROOT, extra argv, requires_key)
BENCHES = {
    "admit_time": ("bench/admit_time/run.py", [], False),
    "pipeline": ("bench/pipeline/run.py", [], False),
    "research": ("bench/research/run.py", [], False),
    "support_chat": ("bench/support_chat/run.py", [], False),
    "ttl_tuning": ("bench/ttl_tuning/run.py", [], False),
    "voice_dialogue": ("bench/voice_dialogue/run.py", [], False),
    "chunk_tiers": ("bench/chunk_tiers/bench_chunk_tiers.py", [], False),
    "chunk_fixtures": ("bench/chunk_tiers/bench_fixtures.py", [], False),
    "chunk_cache_aware": ("bench/chunk_tiers/bench_cache_aware_tiers.py", [], False),
    "chunk_overlap": ("bench/chunk_tiers/bench_overlap.py", [], False),
    "chunk_boundary_split": ("bench/chunk_tiers/bench_boundary_split.py", [], False),
    "cache_aware": ("bench/cache_aware/bench_cache.py", [], False),
    "quickstart": ("quickstart/run.py", [], False),
    "tooldef": ("bench/tooldef/run.py", [], False),
    # Live-Jev prune: real TypeSafe decisions via OpenRouter. Opt-in only.
    "live_jev_prune": (
        "bench/pruners/jev_prune.py",
        ["bench/inputs/sre_incident.json",
         os.path.join(REPO_ROOT, "bench", "outputs", "sre_incident_jev_live.json")],
        True,
    ),
}


def list_benches():
    return sorted(BENCHES)


def run_bench(name, entry=None, timeout_s=300, include_keyed=False):
    """Run one registered bench as a subprocess. Returns a result dict."""
    entry = entry or BENCHES[name]
    script, argv, requires_key = entry
    if requires_key and not include_keyed:
        return {"name": name, "status": "skipped",
                "reason": "requires OPENROUTER_API_KEY; pass --include-keyed"}
    if requires_key and not os.environ.get("OPENROUTER_API_KEY"):
        return {"name": name, "status": "skipped",
                "reason": "OPENROUTER_API_KEY not set"}
    script_path = script if os.path.isabs(script) else os.path.join(REPO_ROOT, script)
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    start = time.time()
    try:
        proc = subprocess.run([sys.executable, script_path] + argv,
                              cwd=REPO_ROOT, env=env, capture_output=True,
                              text=True, timeout=timeout_s)
        elapsed = time.time() - start
        out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.returncode else "")
        tail = [ln for ln in out.strip().splitlines() if ln.strip()][-TAIL_LINES:]
        return {"name": name, "status": "pass" if proc.returncode == 0 else "fail",
                "returncode": proc.returncode, "seconds": round(elapsed, 1),
                "tail": tail}
    except subprocess.TimeoutExpired:
        return {"name": name, "status": "timeout",
                "reason": f"exceeded {timeout_s}s", "seconds": timeout_s}


def run_all(names=None, timeout_s=300, include_keyed=False):
    names = names or list_benches()
    return [run_bench(n, timeout_s=timeout_s, include_keyed=include_keyed)
            for n in names]
