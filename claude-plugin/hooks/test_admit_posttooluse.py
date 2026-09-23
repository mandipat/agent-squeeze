"""Tests for claude-plugin/hooks/admit-posttooluse.py.

Run: python3 test_admit_posttooluse.py   (no service, no paid calls —
the deterministic gate is used, never Jev)
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "admit-posttooluse.py")

BOILERPLATE = "\n".join(f"heartbeat ok {i % 5}" for i in range(800))  # > budget, repetitive
ERROR_TEXT = "Traceback (most recent call last):\n  File \"app.py\", line 42, in main\n    run()\nValueError: invalid literal for int(): 'abc'\n" * 60  # long but signal


def run_hook(tool_name, tool_response, session_id="sess-test"):
    env = dict(os.environ)
    tmp = tempfile.mkdtemp(prefix="admit-hook-test-")
    env["AGENT_SQUEEZE_HOLD_DIR"] = tmp
    env.pop("AGENT_SQUEEZE_ADMIT_JEV", None)  # force the free heuristic
    payload = {"hook_event_name": "PostToolUse", "tool_name": tool_name,
               "tool_response": tool_response, "session_id": session_id,
               "cwd": "/tmp"}
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=30)
    assert p.returncode == 0, f"hook must never fail: {p.stderr}"
    return p, tmp


def test_boilerplate_gated_and_annotated():
    p, tmp = run_hook("Bash", BOILERPLATE)
    assert p.stdout.strip(), "expected additionalContext for a notice"
    out = json.loads(p.stdout)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "notice" in ctx and "⟦held:Bash/" in ctx, ctx[:200]
    assert "heartbeat ok" not in ctx, "full boilerplate must not be re-emitted"
    # annotation logged for the squeezer
    log = os.path.join(tmp, "admit-annotations.jsonl")
    recs = [json.loads(l) for l in open(log)]
    assert len(recs) == 1 and recs[0]["decision"] == "notice"
    assert recs[0]["in_chars"] == len(BOILERPLATE)
    assert recs[0]["session_id"] == "sess-test"
    # hold ref resolves byte-identically through the same store
    sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
    from agent_squeeze.admit import PersistentHoldStore
    os.environ["AGENT_SQUEEZE_HOLD_DIR"] = tmp
    store = PersistentHoldStore()
    assert store.readmit(recs[0]["ref"]) == BOILERPLATE
    print("PASS boilerplate -> notice + annotation + byte-identical hold")


def test_error_stays_silent():
    p, tmp = run_hook("Bash", ERROR_TEXT)
    assert not p.stdout.strip(), "errors are keep_full -> hook stays silent"
    log = os.path.join(tmp, "admit-annotations.jsonl")
    recs = [json.loads(l) for l in open(log)]
    assert recs[0]["decision"] == "keep_full"
    print("PASS error -> silent keep_full, still annotated")


def test_small_result_silent():
    p, tmp = run_hook("Read", "42")
    assert not p.stdout.strip()
    print("PASS small result -> silent")


def test_dict_tool_response():
    p, tmp = run_hook("Bash", {"output": BOILERPLATE, "exit_code": 0})
    assert "notice" in p.stdout
    print("PASS dict tool_response extracted")


def test_bad_input_never_breaks():
    p = subprocess.run([sys.executable, HOOK], input="not json {{{",
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0 and not p.stdout.strip()
    p = subprocess.run([sys.executable, HOOK], input=json.dumps({}),
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0 and not p.stdout.strip()
    print("PASS malformed input -> exit 0 silent")


def test_annotation_priors_drive_squeezer():
    # write-time judgments become keep/drop priors for squeeze_with_policy
    sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
    from agent_squeeze.admit import (annotation_probs, load_annotations,
                                     log_annotation)
    from agent_squeeze.cache import squeeze_with_policy
    tmp = tempfile.mkdtemp(prefix="annot-prior-test-")
    os.environ["AGENT_SQUEEZE_HOLD_DIR"] = tmp
    big = "\n".join(f"tick {i % 3}" for i in range(1500))
    rec = log_annotation("Bash", big, "notice", "⟦held:Bash/0001⟧",
                         session_id="s")
    msgs = [{"role": "user", "name": "", "content": "watch the logs"},
            {"role": "tool", "name": "Bash", "content": big}]
    anns = load_annotations(session_id="s")
    assert anns and anns[0]["ref"] == rec["ref"]
    probs, cost = annotation_probs(msgs, "watch the logs", anns)
    assert all(p == 0.0 for p in probs), probs[:5]
    assert cost == 0.0
    # unmatched message with no base policy -> neutral 0.5
    probs2, _ = annotation_probs(
        [{"role": "tool", "name": "Read", "content": "x" * 3000}],
        "t", anns)
    assert all(p == 0.5 for p in probs2)
    # and the priors plug straight into squeeze_with_policy
    pol = lambda chunks, task: annotation_probs(msgs, task, anns)
    out, stats = squeeze_with_policy(msgs, "watch the logs", pol)
    assert stats["chunks_kept"] < stats["chunks_total"], stats
    print(f"PASS annotations -> priors (notice drops to "
          f"{stats['chunks_kept']}/{stats['chunks_total']} chunks kept)")


if __name__ == "__main__":
    test_boilerplate_gated_and_annotated()
    test_error_stays_silent()
    test_small_result_silent()
    test_dict_tool_response()
    test_bad_input_never_breaks()
    test_annotation_priors_drive_squeezer()
    print("ALL PASS")
