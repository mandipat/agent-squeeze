"""Admit-time tool-result gate: judge a tool result BEFORE it enters context.

Why: retroactive pruning of old history is risky — raw Jev dropped 73% of
items later needed on real sessions (pi-jev-context; 21% even with
deterministic source protection), so that project's shadow pruning was never
applied. Judging a result *at write time*, when its relevance to the task is
fresh, is far safer: relevance questions are answered against the just-issued
tool call, not against a week-old state.

Every non-full admission keeps the full result verbatim in a HoldStore and
emits a deterministic ref (e.g. `⟦held:bash/0003⟧`). Nothing is ever lost:
`readmit(ref)` returns byte-identical text, and `readmit_if_mentioned`
re-admits any held result the agent later references by ref. This mirrors the
LiteLLM Jev-compaction pattern (dropped tool results replaced with a short
notice, tool call + ids intact) and the winnow admission gate.

Decisions:
  keep_full — short results, errors, high-signal results: admitted verbatim.
  trim      — verbatim head+tail lines; middle held under ref (pi-jev-context
              style lossless trim; key lines survive byte for byte).
  notice    — repetitive boilerplate: replaced with a one-line notice like
              LiteLLM's "[Tool result removed by TypeSafe compaction ...]";
              full text held under ref.
  hold      — admitted as ref-only line; nothing of the payload enters context.

Policy: `policy_fn(result) -> (decision, confidence)` is injected for offline
use; defaults to `jev_admit` (TypeSafe Jev, two noul questions batched in one
call: relevance + excerpt-sufficiency), falling back to the deterministic
heuristic below when no policy is available / for tests.
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field

from . import jev

KEEP_FULL = "keep_full"
TRIM = "trim"
NOTICE = "notice"
HOLD = "hold"
DECISIONS = (KEEP_FULL, TRIM, NOTICE, HOLD)

# Results at/below this size are never gated — cheap to keep, risky to trim.
FULL_CHAR_BUDGET = 2000
# A result this repetitive (unique lines / total lines) is boilerplate.
BOILERPLATE_RATIO = 0.5

SIGNAL_RE = re.compile(
    r"error|failed|failure|traceback|exception|denied|timeout|panic|fatal|"
    r"warning|assert|mismatch|invalid|not found|econn|enoent",
    re.IGNORECASE,
)
REF_RE = re.compile(r"⟦held:([a-zA-Z0-9_.\-]+)/(\d{4})⟧")


@dataclass
class Admission:
    decision: str
    text: str            # what enters context
    ref: str | None      # hold-store ref, if anything was held
    held_chars: int      # chars of payload NOT admitted


class HoldStore:
    """Off-context verbatim storage for trimmed/noticed/held payloads."""

    def __init__(self):
        self._blobs: dict[str, str] = {}
        self._counter: dict[str, int] = {}

    def hold(self, name, text):
        n = self._counter.get(name, 0) + 1
        self._counter[name] = n
        ref = f"⟦held:{name}/{n:04d}⟧"
        self._blobs[ref] = text
        return ref

    def readmit(self, ref):
        """Byte-identical full payload for a ref."""
        return self._blobs[ref]

    def readmit_if_mentioned(self, followup_text):
        """Scan an agent follow-up for hold refs; return {ref: full text}."""
        found = {}
        for m in REF_RE.finditer(followup_text or ""):
            ref = m.group(0)
            if ref in self._blobs:
                found[ref] = self._blobs[ref]
        return found

    def held_refs(self):
        return list(self._blobs)

    def held_chars(self):
        return sum(len(t) for t in self._blobs.values())


# Default hold dir for PersistentHoldStore (resolved lazily at
# construction so AGENT_SQUEEZE_HOLD_DIR can be set after import).


class PersistentHoldStore(HoldStore):
    """HoldStore that survives restarts: blobs persist to holds.json.

    The server shares one of these across requests (via `_store()` in
    server.py, which reads AGENT_SQUEEZE_HOLD_DIR at request time so tests
    can point it at a temp dir), so a hold ref issued to an agent can be
    resolved by a later call to /v1/readmit.
    """

    FILENAME = "holds.json"

    def __init__(self, path=None):
        super().__init__()
        # resolve the hold dir lazily (not at import time) so tests and
        # harnesses can redirect via AGENT_SQUEEZE_HOLD_DIR before use.
        hold_dir = os.path.expanduser(
            os.environ.get("AGENT_SQUEEZE_HOLD_DIR", "~/.agent_squeeze"))
        self._path = path or os.path.join(hold_dir, self.FILENAME)
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        try:
            with open(self._path) as f:
                doc = json.load(f)
        except (OSError, ValueError):
            doc = {}
        self._blobs = dict(doc.get("blobs", {}))
        self._counter = {k: int(v) for k, v in doc.get("counter", {}).items()}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"blobs": self._blobs, "counter": self._counter}, f)
            os.replace(tmp, self._path)
        except OSError:
            pass  # holding still works in-memory; persistence best-effort

    def hold(self, name, text):
        with self._lock:
            self._load()  # pick up holds written by sibling requests/processes
            ref = super().hold(name, text)
            self._save()
        return ref


def _line_stats(text):
    lines = text.splitlines()
    uniq = len({ln.strip() for ln in lines if ln.strip()})
    return len(lines), uniq / max(1, len([ln for ln in lines if ln.strip()]))


def deterministic_policy(result_text, is_error=False):
    """Offline heuristic mirroring what Jev is asked to judge.

    Returns (decision, confidence). Never touches the network.
    """
    if is_error or len(result_text) <= FULL_CHAR_BUDGET:
        return KEEP_FULL, 1.0
    if SIGNAL_RE.search(result_text):
        # high-signal but long: keep verbatim excerpt, hold the rest
        return TRIM, 0.9
    n_lines, uniq_ratio = _line_stats(result_text)
    if uniq_ratio < BOILERPLATE_RATIO:
        return NOTICE, 0.85
    return TRIM, 0.8


def jev_admit(result_text, task, framing=None):
    """Two noul questions in one Jev call: relevance + excerpt-sufficiency.

    Returns (decision, confidence, cost_usd). Requires OPENROUTER_API_KEY.
    """
    framing = framing or jev.FRAMING
    state = (framing + "\n\nAgent task: " + task +
             "\n\nJust-issued tool call produced this result:\n" +
             result_text[:8000])
    questions = {
        "relevance": {
            "type": "noul",
            "instructions": ("Is this tool result needed for the agent to "
                              "complete its task? Answer true or false.")},
        "excerpt_sufficient": {
            "type": "noul",
            "instructions": ("Would the first and last ~10 lines of this "
                              "result preserve everything the agent needs? "
                              "Answer true or false.")},
    }
    resp = jev.decide(state, questions)
    rel = resp["answers"]["relevance"]["noul"]
    exc = resp["answers"]["excerpt_sufficient"]["noul"]
    cost = (resp.get("usage") or {}).get("cost", 0.0)
    if rel < 0.2:
        decision = HOLD
    elif rel < 0.5:
        decision = NOTICE
    elif exc >= 0.6:
        decision = TRIM
    else:
        decision = KEEP_FULL
    return decision, min(rel, 1.0), cost


def _trim_verbatim(text, head_lines, tail_lines, ref):
    lines = text.splitlines()
    head = lines[:head_lines]
    tail = lines[-tail_lines:] if len(lines) > head_lines + tail_lines else []
    marker = (f"[... {len(lines) - len(head) - len(tail)} lines held verbatim "
              f"under {ref} ...]")
    return "\n".join(head + [marker] + tail)


def admit_tool_result(name, result_text, task="", store=None,
                      policy_fn=None, head_lines=8, tail_lines=8,
                      cost_tracker=None):
    """Gate one tool result before it enters context.

    Returns (Admission, cost_usd). `policy_fn(text, is_error) -> (decision,
    confidence)`; defaults to `jev_admit` when a key is available, else the
    deterministic heuristic. Errors always keep_full regardless of policy.
    """
    store = store or HoldStore()
    text = result_text or ""
    is_error = "error" in (name or "").lower() or bool(SIGNAL_RE.search(
        text[:4000]))
    cost = 0.0

    if is_error and len(text) <= FULL_CHAR_BUDGET * 4:
        decision, conf = KEEP_FULL, 1.0
    elif len(text) <= FULL_CHAR_BUDGET:
        decision, conf = KEEP_FULL, 1.0
    elif policy_fn is not None:
        decision, conf = policy_fn(text, is_error)
        if decision not in DECISIONS:
            decision, conf = TRIM, 0.5
    else:
        decision, conf = deterministic_policy(text, is_error)

    if decision == KEEP_FULL:
        return Admission(KEEP_FULL, text, None, 0), cost

    ref = store.hold(name or "tool", text)
    if decision == TRIM:
        admitted = _trim_verbatim(text, head_lines, tail_lines, ref)
    elif decision == NOTICE:
        admitted = (f"[tool result held by admit-time gate ({len(text)} chars, "
                    f"judged boilerplate): full text at {ref}]")
    else:  # HOLD
        admitted = f"[tool result held off-context: {ref}]"
    return Admission(decision, admitted, ref, len(text) - len(admitted)), cost


def admit_session(tool_results, task="", store=None, policy_fn=None):
    """Gate a batch of (name, text) tool results. Returns (admissions, stats).

    `tool_results`: list of (name, text, is_error?) tuples or dicts.
    """
    store = store or HoldStore()
    admissions, total_cost = [], 0.0
    in_chars, out_chars = 0, 0
    for item in tool_results:
        if isinstance(item, dict):
            name, text = item.get("name", "tool"), item.get("text", "")
        else:
            name, text = item[0], item[1]
        in_chars += len(text or "")
        adm, cost = admit_tool_result(name, text, task, store,
                                      policy_fn=policy_fn)
        total_cost += cost
        out_chars += len(adm.text)
        admissions.append(adm)
    counts = {d: sum(1 for a in admissions if a.decision == d)
              for d in DECISIONS}
    stats = {
        "results": len(admissions),
        "input_chars": in_chars,
        "admitted_chars": out_chars,
        "reduction_pct": round(100 * (1 - out_chars / in_chars), 2)
        if in_chars else 0.0,
        "held_chars": store.held_chars(),
        "decisions": counts,
        "jev_cost_usd": round(total_cost, 6),
    }
    return admissions, stats
