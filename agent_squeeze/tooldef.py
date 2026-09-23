"""Tool-definition pruning: per-task keep/prune of the agent's tool list.

Coding agents carry 50-200 tool definitions into every turn (each with a
JSON schema), and the list is rarely task-specific: the file-writing agent
pays for the image-generation schema on every call. This module prunes the
list to the task-relevant subset before the turn starts.

Decisions per tool: "keep" (relevant to the task, recently called, or the
task gives no signal — fail-safe keeps it verbatim), "prune" (off-context,
held via HoldStore, byte-identical recall by tool name). Nothing kept is
ever rewritten — the same rule the admit-time gate uses.

Priority of signals (mirrors jev-tool-permissions practice):
  1. Recently called tools are sacred — the agent already demonstrated need.
  2. Task vocabulary overlap with name + description (+ schema property
     names) — a task about "pytest failures" keeps bash/grep, drops
     the calendar schema.
  3. Fail-safe: an empty/nonsense task or zero hits keeps EVERYTHING. An
     agent that needs an unpruned tool but lost its schema is a hard
     failure; a slightly large tool list is just cost.

Real-Jev production shape (not called by the free default): for each tool,
one noul question — "Will the agent need the {name} tool ({description})
to accomplish this task?" — all questions in ONE decisions call
(map-reduce, per Run 19), keep iff p >= 0.5, recently-called tools still
sacred. Inject via policy_fn.
"""
import json
import re

from .admit import HoldStore
from .messages import estimate_tokens

KEEP_THRESHOLD = 2  # deterministic keyword hits needed to keep on vocabulary alone


# Task verbs imply tool vocabulary a naive keyword match misses ("debug a
# failing suite" -> file tools, grep, git). Production Jev reasons this
# directly; the free judge carries a small explicit alias table instead.
# Keys are task keywords; values are tool-vocabulary words that count as hits.
RELATED = {
    "debug": ["file", "read", "write", "edit", "command", "shell",
              "grep", "search", "test", "pytest", "log"],
    "failing": ["file", "read", "command", "shell", "grep", "test",
                "pytest", "log"],
    "fix": ["file", "read", "write", "edit", "command", "shell", "grep"],
    "suite": ["test", "pytest"],
    "pull": ["git", "github", "gh"],
    "request": ["git", "github", "gh"],
    "pr": ["git", "github", "gh", "pull"],
}


# Stopwords that would otherwise count as "task vocabulary" ("edit an image
# with a mask" must not match on "with").
STOP = {"with", "from", "that", "this", "these", "those", "and", "for",
        "are", "have", "will", "shall", "into", "your", "you"}


def _task_keywords(task):
    base = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9_\-]{3,}", task or "")}
    base -= STOP
    acronyms = {a.lower() for a in re.findall(r"\b[A-Z]{2,3}\b", task or "")}
    words = base | acronyms
    for k in list(words):
        words |= {r for r in RELATED.get(k, ())}
    return words


def _tool_text(tool):
    """The searchable + billable surface of a tool definition."""
    name = tool.get("name", "") or ""
    desc = tool.get("description", "") or ""
    schema = tool.get("input_schema") or {}
    try:
        schema_str = json.dumps(schema, sort_keys=True)
    except (TypeError, ValueError):
        schema_str = str(schema)
    props = " ".join(schema.get("properties", {}).keys()) if isinstance(schema, dict) else ""
    return name, desc, schema_str, props


def tool_chars(tool):
    name, desc, schema_str, _ = _tool_text(tool)
    return name + "\n" + desc + "\n" + schema_str


def deterministic_policy(tool, task, called):
    """Free offline judge. Returns (decision, score, reason)."""
    name = (tool.get("name") or "").strip()
    if called and name in called:
        return "keep", 1.0, "recently called — sacred"
    _, desc, _, props = _tool_text(tool)
    hay = (name.replace("_", " ") + " " + desc + " " + props.replace("_", " ")).lower()
    keywords = _task_keywords(task)
    hits = sorted({k for k in keywords if re.search(r"\b" + re.escape(k) + r"\b", hay)})
    score = min(1.0, len(hits) / 4.0)
    if len(hits) >= KEEP_THRESHOLD:
        return "keep", score, "keyword hits: " + ", ".join(hits[:6])
    return "prune", score, "no task signal (%d keyword hits)" % len(hits)


def prune_tool_definitions(tools, task, called=None, policy_fn=None, store=None):
    """Prune the tool list to the task-relevant subset.

    Returns (kept_tools, ledger, stats). kept_tools are verbatim copies of
    the input definitions (never rewritten). Pruned tools are held via
    `store` (HoldStore, default a new ephemeral one) under their name and
    recalled byte-identically by `readmit_tool(name, store)`.
    """
    tools = list(tools or [])
    called = set(called or [])
    store = store or HoldStore()
    policy = policy_fn or deterministic_policy

    ledger = []
    kept = []
    any_keep_signal = False
    provisional = []  # (tool, decision, score, reason)

    for tool in tools:
        name = (tool.get("name") or "").strip()
        decision, score, reason = policy(tool, task, called)
        if decision == "keep":
            any_keep_signal = True
        provisional.append((tool, name, decision, score, reason))

    for tool, name, decision, score, reason in provisional:
        # Fail-safe: with no task signal at all, keep everything. Losing a
        # needed tool's schema is a hard failure; a fat list is only cost.
        if decision == "keep" or not any_keep_signal:
            kept.append(tool)
            ledger.append({"name": name, "decision": "keep",
                           "score": round(score, 3),
                           "reason": "fail-safe: no task signal" if not any_keep_signal
                           else reason})
        else:
            ref = store.hold(name or "unnamed", tool_chars(tool))
            ledger.append({"name": name, "decision": "prune",
                           "score": round(score, 3), "reason": reason,
                           "ref": ref})

    before = sum(estimate_tokens(tool_chars(t)) for t in tools)
    after = sum(estimate_tokens(tool_chars(t)) for t in kept)
    stats = {"tools_before": len(tools), "tools_after": len(kept),
             "tokens_before": before, "tokens_after": after,
             "reduction_pct": round(100.0 * (before - after) / before, 2) if before else 0.0,
             "kept": sum(1 for l in ledger if l["decision"] == "keep"),
             "pruned": sum(1 for l in ledger if l["decision"] == "prune")}
    return kept, ledger, stats


def readmit_tool(name, store, ledger=None):
    """Recall a pruned tool definition byte-identically by tool name.

    With a ledger (as returned by prune_tool_definitions) the tool's own
    ref is resolved; otherwise the latest ref held under that name wins.
    """
    ref = None
    if ledger:
        for entry in ledger:
            if entry.get("name") == name and entry.get("decision") == "prune":
                ref = entry.get("ref")
    if ref is None:
        candidates = [r for r in store.held_refs()
                      if r.startswith("⟦held:%s/" % (name or "unnamed"))]
        if not candidates:
            raise KeyError(name)
        ref = sorted(candidates)[-1]
    return store.readmit(ref)


def readmit_by_ref(ref, store):
    return store.readmit(ref)
