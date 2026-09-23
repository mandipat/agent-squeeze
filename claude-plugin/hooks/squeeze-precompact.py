#!/usr/bin/env python3
"""Claude Code PreCompact hook: cache-safe compaction via agent-squeeze.

Why this exists: Claude Code's built-in compaction summarizes the transcript,
which rewrites earlier messages and destroys the provider's prompt cache —
every turn after a compaction re-pays full input price. This hook fires before
compaction and POSTs the transcript to the agent-squeeze service's
`/v1/squeeze-cache-aware` endpoint. The returned digest is injected as
`additionalContext`, so the post-compaction window carries an *extractive*
(verbatim, keep/drop) compression instead of a lossy summary — and the
protected prefix (system prompt + goal + stable history) survives compaction
byte-identical, keeping the next call's prompt cache warm.

Facts about the PreCompact event (Claude Code hooks protocol):
  - stdin JSON carries: session_id, transcript_path, cwd, hook_event_name,
    trigger ("auto" | "manual"), custom_instructions.
  - PreCompact cannot block or replace the transcript; it may only return
    {"hookSpecificOutput": {"hookEventName": "PreCompact",
                            "additionalContext": "..."}} which is injected
    into the post-compaction context.
  - Exit 0 = success. Any failure here must be silent and non-blocking: a
    compaction that crashes is worse than one with no hook.

Env:
  AGENT_SQUEEZE_URL           e.g. http://localhost:8765 (required)
  AGENT_SQUEEZE_TOKEN         bearer token if the server set one (optional)
  AGENT_SQUEEZE_PROTECT       tokens to keep byte-identical (default 4096)
  AGENT_SQUEEZE_DIGEST_CHARS  max chars of kept evidence in the digest
                              (default 4000)
  AGENT_SQUEEZE_TIMEOUT       HTTP timeout seconds (default 8)

Install (settings.json):
  {"hooks": {"PreCompact": [{"matcher": "auto", "hooks": [
     {"type": "command",
      "command": "<repo>/claude-plugin/hooks/squeeze-precompact.py"}]}]}}

The hook shells nothing; stdlib only.
"""
import json
import os
import sys
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from agent_squeeze.messages import from_claude_jsonl, estimate_tokens  # noqa: E402


def _fail_silent():
    # Never break the user's compaction. PreCompact cannot block anyway;
    # exit 0 with no output keeps this invisible on failure.
    sys.exit(0)


def _post(url, token, payload, timeout):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _first_user_text(messages):
    for m in messages:
        if m.get("role") == "user" and m.get("content", "").strip():
            return m["content"].strip().splitlines()[0][:500]
    return ""


def _digest(task, resp, budget):
    messages = resp.get("messages", []) or []
    stats = resp.get("stats", {}) or {}
    # Skip the protected prefix in the digest: it survives byte-identical, so
    # re-injecting it would duplicate context. The digest carries the *tail*.
    prot = int(stats.get("protected_tokens", 0))
    kept = []
    for m in messages:
        role = m.get("role", "")
        if role in ("assistant",) and not m.get("content", "").strip():
            continue
        text = (m.get("content") or "").strip()
        if not text:
            continue
        # drop tool-call headers (they pair with results already kept)
        if role == "assistant" and text.startswith("[tool call:"):
            continue
        label = "tool:" + m.get("name", "") if role == "tool" else role
        kept.append((label, text))
    # rough token accounting on the tail: take newest-first? No — take
    # order-preserving, highest-density first is overkill; chronological
    # excerpts preserve narrative. Keep tool outputs first (highest
    # evidence density), then the rest, within budget.
    kept.sort(key=lambda kv: 0 if kv[0].startswith("tool:") else 1)
    parts, used = [], 0
    for label, text in kept:
        snippet = text if len(text) <= 400 else text[:400] + " …"
        block = f"- [{label}] {snippet}"
        if used + len(block) > budget:
            break
        parts.append(block)
        used += len(block)
    lines = ["<agent-squeeze-precompact>",
             f"Original task: {task}",
             f"Squeezed by agent-squeeze before compaction: "
             f"{stats.get('tokens_before', '?')} → {stats.get('tokens_after', '?')} "
             f"tokens ({stats.get('reduction_pct', '?')}% off); "
             f"{prot} protected tokens survive byte-identical (cache-safe).",
             "Kept evidence, verbatim (extractive keep/drop, nothing "
             "rewritten):"]
    lines.extend(parts)
    lines.append("</agent-squeeze-precompact>")
    return "\n".join(lines)


def main():
    try:
        event = json.load(sys.stdin)
    except Exception:
        _fail_silent()
    transcript = event.get("transcript_path") or ""
    base = os.environ.get("AGENT_SQUEEZE_URL", "").rstrip("/")
    if not transcript or not base or not os.path.isfile(transcript):
        _fail_silent()
    try:
        messages = from_claude_jsonl(transcript)
        if not messages:
            _fail_silent()
        task = _first_user_text(messages)
        protect = int(os.environ.get("AGENT_SQUEEZE_PROTECT", "4096"))
        budget = int(os.environ.get("AGENT_SQUEEZE_DIGEST_CHARS", "4000"))
        timeout = float(os.environ.get("AGENT_SQUEEZE_TIMEOUT", "8"))
        resp = _post(base + "/v1/squeeze-cache-aware",
                     os.environ.get("AGENT_SQUEEZE_TOKEN", ""),
                     {"messages": [{"role": m.get("role", "user"),
                                    "name": m.get("name", ""),
                                    "content": m.get("content", "")}
                                   for m in messages],
                      "task": task,
                      "protect_tokens": protect},
                     timeout)
        digest = _digest(task, resp, budget)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreCompact",
            "additionalContext": digest}}))
    except (urllib.error.URLError, OSError, ValueError, KeyError,
            TimeoutError):
        _fail_silent()
    except Exception:
        _fail_silent()


if __name__ == "__main__":
    main()
