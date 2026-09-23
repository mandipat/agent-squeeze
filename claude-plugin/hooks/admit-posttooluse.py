#!/usr/bin/env python3
"""Claude Code PostToolUse hook: admit-time gate inside the tool loop.

Why this exists: relevance is judged best *when it is fresh* — right after
the tool call that produced the result, not minutes later against cold
history (pi-jev-context: retroactive pruning dropped 73% of items later
needed on real sessions, while write-time verbatim trimming saved 31-53%
with 0 key lines lost). This hook runs the agent-squeeze admit gate on every
tool result before the next agent turn:

  1. Judges the just-produced result with the deterministic gate (free,
     local, no network) — or TypeSafe Jev when AGENT_SQUEEZE_ADMIT_JEV=1.
  2. Appends the judgment to ~/.agent_squeeze/admit-annotations.jsonl.
     The retroactive squeezer reads this log via
     agent_squeeze.admit.annotation_probs() and uses the recorded decisions
     as keep/drop priors — write-time judgments anchor later pruning.
  3. For trim/notice/hold decisions, injects the *admitted* text (verbatim
     excerpt + hold ref) as additionalContext so the model acts on the
     excerpt first and knows the full result is recoverable. keep_full
     decisions stay silent — no point re-emitting what is already there.

Honest limits (read before relying on this):
  - PostToolUse cannot rewrite the tool result already in the transcript;
    the raw result stays in history. The hook steers the *model* to the
    gated excerpt and records the judgment for the next squeeze pass, which
    is where the token savings land.
  - The deterministic gate is a heuristic; for judgment calls set
    AGENT_SQUEEZE_ADMIT_JEV=1 (uses the OpenRouter key; each call is one
    small Jev request — keep an eye on the monthly cap).

Trimmed/noticed/held payloads persist verbatim in the hold store, so refs
resolve later via `agent-squeeze admit-readmit <ref>` or the service's
POST /v1/readmit and /v1/readmit-if-mentioned.

Env:
  AGENT_SQUEEZE_ADMIT_JEV      1 = use Jev judgments (default 0 = heuristic)
  AGENT_SQUEEZE_HOLD_DIR       hold store + annotation log dir
                               (default ~/.agent_squeeze)
  AGENT_SQUEEZE_ADMIT_TIMEOUT  seconds; the hook aborts silently past this
                               (default 10 — the tool loop must never stall)

Install (settings.json):
  {"hooks": {"PostToolUse": [{"matcher": "*",
    "hooks": [{"type": "command",
      "command": "<repo>/claude-plugin/hooks/admit-posttooluse.py"}]}]}}

Stdlib only. Any failure exits 0 silently — a broken hook must never break
the user's tool loop.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


def _fail_silent():
    sys.exit(0)


def _extract_text(resp):
    """Best-effort text extraction from a PostToolUse tool_response."""
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, (int, float, bool)):
        return str(resp)
    if isinstance(resp, dict):
        for key in ("output", "text", "result", "stdout", "content"):
            if isinstance(resp.get(key), str) and resp[key]:
                return resp[key]
        parts = [v for v in resp.values() if isinstance(v, str)]
        return "\n".join(parts)
    if isinstance(resp, list):
        parts = []
        for item in resp:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        _fail_silent()

    try:
        from agent_squeeze.admit import (PersistentHoldStore, admit_tool_result,
                                         deterministic_policy, jev_admit,
                                         log_annotation)
        tool_name = payload.get("tool_name") or payload.get("toolName") or "tool"
        text = _extract_text(payload.get("tool_response"))
        session_id = payload.get("session_id") or ""
        if not text:
            _fail_silent()

        if os.environ.get("AGENT_SQUEEZE_ADMIT_JEV") == "1":
            policy = jev_admit
        else:
            policy = deterministic_policy

        store = PersistentHoldStore()
        admission, cost = admit_tool_result(tool_name, text, policy_fn=policy,
                                            store=store)
        log_annotation(tool_name, text, admission.decision, admission.ref,
                       session_id=session_id, cost_usd=cost)

        if admission.decision == "keep_full":
            _fail_silent()  # nothing to steer; stay silent

        guidance = (
            "[admit-time gate] The result above was judged at write time as "
            f"{admission.decision}; act on the excerpt below, not the full "
            "result. The full result is held verbatim and recoverable — "
            f"re-read it via `agent-squeeze admit-readmit {admission.ref}` "
            "or POST /v1/readmit only if you actually need more.\n\n"
            + admission.text)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": guidance}}))
    except Exception:
        _fail_silent()


if __name__ == "__main__":
    main()
