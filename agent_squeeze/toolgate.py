"""toolgate: per-turn tool/skill selection with Jev.

Every Claude API call carries the FULL tool catalog (built-in tools + MCP +
skills) in the request -- often 10-30k tokens of name/description/schema --
but a given turn only needs 1-3 of them. This module asks Jev, once per
assistant turn, which tools the agent will plausibly need for its NEXT
response, and reconstructs the smaller `tools` array that request would have
carried.

Two deliberate approximations (documented, not hidden):
1. The catalog below is modeled on Claude Code's real tool set (names and
   descriptions are close to the real ones; schemas are compact
   reconstructions, not byte copies of Anthropic's). The token numbers are
   therefore an approximation of the real system-prompt cost, good for
   order-of-magnitude analysis.
2. Skills in Claude Code actually ship in the system prompt, not the `tools`
   array. We model them as catalog entries anyway: the question is the same
   ("which capabilities does this turn need?") and the token math is the
   same shape.

Selection uses name + one-line description only; the wire cost measured is
the full name + description + input_schema JSON, which is the point: schema
tokens are pure waste for the selection decision.

Usage:
    python -m agent_squeeze.toolgate --inputs bench/inputs/real_task1.json ...
    (or run analyze_all() from analyze_toolgate.py)

Jev calls go through ~/workspace/skills/openrouter/bin/or_decide.py
(model typesafe/jev-1.13, /api/alpha/decisions, noul questions, p>=0.5 keep),
batched: one Jev call per assistant turn, one question per catalog entry.
Decisions are cached in ~/.agent_squeeze/decisions.sqlite (table `toolgate`,
key = sha256(prompt_version + state + tool name)) so reruns are free.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from context import parse, task_intent, turn_text_for_judge  # noqa: E402

PROMPT_VERSION = "toolgate-v1"
API_MODEL = "typesafe/jev-1.13"
OR_DECIDES_SKILL = os.path.expanduser(
    "~/workspace/skills/openrouter/bin/or_decide.py")
CACHE_DB = os.path.expanduser("~/.agent_squeeze/decisions.sqlite")
KEEP_P = 0.5

# Assumed pricing for the analysis. Stated in every report: these are the
# public Sonnet list prices at time of writing, input side only (the tools
# array is billed as input tokens).
SONNET_INPUT_USD_PER_MTOK = 3.00

FRAMING = (
    "You are a precise tool-needs judge for an AI coding agent (Claude Code). "
    "The agent's API requests carry a large catalog of tool definitions; "
    "your job is to decide which tools the agent will plausibly need to "
    "produce its NEXT response, given the task and the conversation so far. "
    "Select a tool only if the next response is likely to call it. "
    "File reads/writes cluster: needing Read once usually means needing it "
    "again. Terminal work needs Bash. Answering a question from context "
    "already in hand needs NO tools. When in doubt about a rarely-used "
    "tool, say false."
)


# ---------------------------------------------------------------------------
# tool catalog (modeled on Claude Code's real tool set)
# ---------------------------------------------------------------------------

def _tool(name, desc, schema, kind="tool", when=""):
    return {"name": name, "description": desc, "input_schema": schema,
            "kind": kind, "when": when}


def build_catalog():
    """~17 tools + 3 skills, shaped like Claude Code's real catalog."""
    obj = lambda props, req: {"type": "object", "properties": props,
                              "required": req}  # noqa: E731
    s = lambda d: {"type": "string", "description": d}  # noqa: E731
    return [
        _tool("Read", "Read a file from the local filesystem.",
              obj({"file_path": s("Absolute path of the file to read."),
                   "offset": {"type": "integer",
                              "description": "Line number to start from."},
                   "limit": {"type": "integer",
                             "description": "Number of lines to read."}},
                  ["file_path"])),
        _tool("Write", "Write a file to the local filesystem, creating it "
              "or overwriting it entirely.",
              obj({"file_path": s("Absolute path of the file to write."),
                   "content": s("Full content to write to the file.")},
                  ["file_path", "content"])),
        _tool("Edit", "Make a targeted edit to a file by replacing "
              "old_string with new_string.",
              obj({"file_path": s("Absolute path of the file to edit."),
                   "old_string": s("Exact text to replace."),
                   "new_string": s("Replacement text.")},
                  ["file_path", "old_string", "new_string"])),
        _tool("Bash", "Execute a shell command in the project environment.",
              obj({"command": s("The shell command to execute."),
                   "description": s("Short description of what it does."),
                   "timeout": {"type": "integer",
                               "description": "Timeout in milliseconds."},
                   "run_in_background": {"type": "boolean",
                                         "description": "Run async."}},
                  ["command"])),
        _tool("BashOutput", "Read the output of a background shell started "
              "with run_in_background.",
              obj({"bash_id": s("The background shell id.")}, ["bash_id"])),
        _tool("KillShell", "Kill a running background shell.",
              obj({"shell_id": s("The background shell id.")}, ["shell_id"])),
        _tool("Glob", "Find files matching a glob pattern.",
              obj({"pattern": s("Glob pattern, e.g. '**/*.py'."),
                   "path": s("Directory to search under.")}, ["pattern"])),
        _tool("Grep", "Search file contents with a regular expression.",
              obj({"pattern": s("Regex to search for."),
                   "path": s("File or directory to search."),
                   "include": s("Filename glob filter."),
                   "output_mode": s("'content', 'files_with_matches' or "
                                    "'count'.")}, ["pattern"])),
        _tool("LS", "List the contents of a directory.",
              obj({"path": s("Directory to list."),
                   "ignore": {"type": "array", "items": s("glob"),
                              "description": "Patterns to ignore."}},
                  ["path"])),
        _tool("TodoWrite", "Create and manage a structured task list for "
              "the current work.",
              obj({"todos": {"type": "array", "items": obj(
                  {"content": s("Task description."),
                   "status": s("pending/in_progress/completed."),
                   "activeForm": s("Present-continuous form.")}, ["content",
                   "status", "activeForm"])}}, ["todos"])),
        _tool("WebFetch", "Fetch a URL and extract its content as text.",
              obj({"url": s("The URL to fetch."),
                   "prompt": s("What to extract from the page.")}, ["url"])),
        _tool("WebSearch", "Search the web for current information.",
              obj({"query": s("The search query.")}, ["query"])),
        _tool("Task", "Launch a subagent to handle an independent subtask. "
              "(Older transcripts call this tool 'Agent'.)",
              obj({"description": s("Short description of the subtask."),
                   "prompt": s("Full instructions for the subagent."),
                   "subagent_type": s("The subagent type to launch.")},
                  ["description", "prompt"])),
        _tool("NotebookRead", "Read a Jupyter notebook file.",
              obj({"notebook_path": s("Absolute path of the notebook.")},
                  ["notebook_path"])),
        _tool("NotebookEdit", "Edit a cell of a Jupyter notebook.",
              obj({"notebook_path": s("Absolute path of the notebook."),
                   "cell_id": s("Id of the cell to edit."),
                   "new_source": s("New cell source.")},
                  ["notebook_path", "cell_id", "new_source"])),
        _tool("mcp__github__create_issue", "Create a GitHub issue via the "
              "GitHub MCP server.",
              obj({"repo": s("owner/repo."),
                   "title": s("Issue title."),
                   "body": s("Issue body.")}, ["repo", "title"])),
        _tool("mcp__filesystem__search", "Search files via the filesystem "
              "MCP server.",
              obj({"query": s("Search query."),
                   "root": s("Root directory.")}, ["query"])),
        # -- skills (modeled as catalog entries; see module docstring) --
        _tool("skill:code-review", "Reviews code changes for bugs, style "
              "and security issues.",
              obj({"target": s("What to review: file, diff or PR.")},
                  ["target"]),
              kind="skill",
              when="Invoke when the user asks for a review, or before "
                   "finalizing a multi-file change."),
        _tool("skill:test-runner", "Runs the project's test suite and "
              "interprets failures.",
              obj({"scope": s("What to run: file, directory or 'all'.")},
                  ["scope"]),
              kind="skill",
              when="Invoke when tests need running or test output needs "
                   "interpreting."),
        _tool("skill:doc-writer", "Writes or updates documentation: READMEs, "
              "docstrings, changelogs.",
              obj({"target": s("What to document."),
                   "format": s("'readme', 'docstring' or 'changelog'.")},
                  ["target"]),
              kind="skill",
              when="Invoke when the user asks for docs, or when a change "
                   "needs documenting."),
    ]


def catalog_compact_listing(catalog):
    """One line per entry: what the Jev judge sees."""
    lines = []
    for t in catalog:
        line = f"- {t['name']}: {t['description']}"
        if t.get("when"):
            line += f" When to use: {t['when']}"
        lines.append(line)
    return "\n".join(lines)


def tools_array(catalog, selected=None):
    """Render the wire-format Anthropic `tools` array (name/description/
    input_schema only -- skills included as entries for the analysis)."""
    return [{"name": t["name"], "description": t["description"],
             "input_schema": t["input_schema"]}
            for t in catalog
            if selected is None or t["name"] in selected]


# ---------------------------------------------------------------------------
# token counting (tiktoken cl100k_base as the proxy; chars/4 fallback)
# ---------------------------------------------------------------------------

_enc = None


def count_tokens(text):
    global _enc
    if _enc is None:
        try:
            import tiktoken
            _enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _enc = False
    if _enc:
        return len(_enc.encode(text))
    return len(text) // 4


def catalog_tokens(catalog, selected=None):
    return count_tokens(json.dumps(tools_array(catalog, selected),
                                   separators=(",", ":")))


# ---------------------------------------------------------------------------
# sqlite cache (own table, same DB as the v2 pruner)
# ---------------------------------------------------------------------------

def _cache_conn():
    os.makedirs(os.path.dirname(CACHE_DB), exist_ok=True)
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("CREATE TABLE IF NOT EXISTS toolgate "
                 "(key TEXT PRIMARY KEY, selected INTEGER, p REAL, ts REAL)")
    return conn


class ToolgateCache:
    def __init__(self):
        self.conn = _cache_conn()
        self.hits = 0
        self.misses = 0

    def get(self, key):
        row = self.conn.execute(
            "SELECT selected, p FROM toolgate WHERE key=?", (key,)).fetchone()
        if row:
            self.hits += 1
            return bool(row[0]), row[1]
        self.misses += 1
        return None

    def put(self, key, selected, p):
        self.conn.execute(
            "INSERT OR REPLACE INTO toolgate VALUES (?,?,?,?)",
            (key, int(selected), p, time.time()))
        self.conn.commit()

    def close(self):
        self.conn.close()


# ---------------------------------------------------------------------------
# Jev batched call (one call per turn, one question per tool)
# ---------------------------------------------------------------------------

def jev_batch(state, questions):
    with tempfile.NamedTemporaryFile("w", suffix=".json",
                                     delete=False) as f:
        json.dump(questions, f)
        qpath = f.name
    try:
        out = subprocess.run(
            [sys.executable, OR_DECIDES_SKILL, "--model", API_MODEL,
             "--state", state, "--questions", qpath],
            capture_output=True, text=True, timeout=300)
    finally:
        os.unlink(qpath)
    if out.returncode:
        raise RuntimeError(f"or_decide.py failed: {out.stderr[-300:]}")
    resp = json.loads(out.stdout)
    cost = (resp.get("usage") or {}).get("cost", 0.0)
    return resp["answers"], cost


# ---------------------------------------------------------------------------
# per-turn gating
# ---------------------------------------------------------------------------

def _ledger_line(turn):
    """One-line summary of a past turn for the rolling ledger."""
    txt = turn_text_for_judge(turn).replace("\n", " ")
    tools = sorted({p.name for p in turn.tool_pairs if p.name})
    prefix = f"[t{turn.index} {turn.role}"
    if tools:
        prefix += " tools=" + ",".join(tools)
    prefix += "]"
    return (prefix + " " + txt[:160]).strip()


def state_for_turn(turns, idx, intent, catalog):
    """State for gating assistant turn `idx`: everything BEFORE it.

    Never includes the turn's own content (that would leak the tool calls
    we are trying to predict). Rolling ledger of prior turns + the most
    recent turn in fuller form (the immediate trigger for the next response).
    """
    prior = turns[:idx]
    ledger = "\n".join(_ledger_line(t) for t in prior)
    latest = ""
    if prior:
        latest = turn_text_for_judge(prior[-1])[:1500]
    return (
        f"{FRAMING}\n\n"
        f"Task: {intent}\n\n"
        f"Available tools:\n{catalog_compact_listing(catalog)}\n\n"
        f"Conversation so far:\n{ledger if ledger else '(nothing yet)'}\n\n"
        f"Latest turn (what the agent is responding to):\n{latest}"
    )


def _state_key(state, tool_name):
    h = hashlib.sha256()
    h.update(PROMPT_VERSION.encode())
    h.update(b"\n")
    h.update(state.encode())
    h.update(b"\n")
    h.update(tool_name.encode())
    return h.hexdigest()


def gate_turn(turns, idx, intent, catalog, cache):
    """Select the tool subset for assistant turn `idx`.

    Returns (selected_names, per_tool_p, cost_usd, latency_s, cache_hit).
    """
    state = state_for_turn(turns, idx, intent, catalog)
    questions, pending, per_tool_p, selected = {}, [], {}, []
    all_cached = True
    for t in catalog:
        key = _state_key(state, t["name"])
        hit = cache.get(key)
        if hit is not None:
            sel, p = hit
            per_tool_p[t["name"]] = p
            if sel:
                selected.append(t["name"])
        else:
            all_cached = False
            pending.append(t)
            questions[t["name"]] = {
                "type": "noul",
                "instructions": (
                    f"Will the agent need the '{t['name']}' tool to produce "
                    f"its next response? Answer true or false."
                ),
            }
    cost, latency = 0.0, 0.0
    if pending:
        t0 = time.time()
        try:
            answers, cost = jev_batch(state, questions)
        except Exception as e:
            # Fail-open on Jev errors: keep everything (never break the run).
            print(f"  [toolgate] Jev call failed ({e}); fail-open: all tools",
                  file=sys.stderr)
            return ([t["name"] for t in catalog],
                    {t["name"]: 1.0 for t in catalog}, 0.0, 0.0, False)
        latency = time.time() - t0
        for t in pending:
            p = answers[t["name"]]["noul"]
            per_tool_p[t["name"]] = p
            sel = p >= KEEP_P
            if sel:
                selected.append(t["name"])
            cache.put(_state_key(state, t["name"]), sel, p)
    return selected, per_tool_p, cost, latency, all_cached


# name normalisation: older transcripts call the Task tool "Agent"
NAME_ALIASES = {"Agent": "Task"}


def used_tools(turn):
    return {NAME_ALIASES.get(p.name, p.name)
            for p in turn.tool_pairs if p.name}


# ---------------------------------------------------------------------------
# offline replay analysis
# ---------------------------------------------------------------------------

def analyze_session(input_path, catalog, cache, verbose=True):
    doc = json.load(open(input_path))
    turns = parse(doc["messages"])
    intent = task_intent(turns) or doc.get("question", "")
    full_tok = catalog_tokens(catalog)

    per_turn = []
    total_before = total_after = 0
    gate_cost = gate_lat = 0.0
    for t in turns:
        if t.role != "assistant":
            continue
        selected, probs, cost, lat, cached = gate_turn(
            turns, t.index, intent, catalog, cache)
        gate_cost += cost
        gate_lat += lat
        after_tok = catalog_tokens(catalog, selected)
        total_before += full_tok
        total_after += after_tok
        used = used_tools(t)
        # recall on tool-using turns only is the meaningful number;
        # turns with no calls are vacuously satisfied
        rec = used <= set(selected)
        per_turn.append({
            "turn": t.index,
            "selected": selected,
            "n_selected": len(selected),
            "used": sorted(used),
            "n_used": len(used),
            "recall": rec,
            "tokens_before": full_tok,
            "tokens_after": after_tok,
            "gate_cost_usd": cost,
            "gate_latency_s": round(lat, 2),
            "cached": cached,
        })
        if verbose:
            flag = "OK " if rec else "MISS"
            print(f"  t{t.index:2d} [{flag}] used={sorted(used) or '-'} "
                  f"sel={len(selected):2d} tok {full_tok}->{after_tok} "
                  f"${cost:.6f}{' (cache)' if cached else ''}")
    tool_turns = [r for r in per_turn if r["n_used"] > 0]
    return {
        "id": doc["id"],
        "scenario": doc.get("scenario", ""),
        "n_assistant_turns": len(per_turn),
        "n_tool_using_turns": len(tool_turns),
        "recall_all": (sum(r["recall"] for r in per_turn) / len(per_turn)
                       if per_turn else 1.0),
        "recall_tool_turns": (sum(r["recall"] for r in tool_turns)
                              / len(tool_turns) if tool_turns else 1.0),
        "avg_selected": sum(r["n_selected"] for r in per_turn) / len(per_turn)
        if per_turn else 0,
        "avg_used_oracle": sum(r["n_used"] for r in per_turn) / len(per_turn)
        if per_turn else 0,
        "tokens_before": total_before,
        "tokens_after": total_after,
        "reduction_pct": round(100 * (total_before - total_after)
                               / total_before, 1) if total_before else 0,
        "gate_cost_usd": gate_cost,
        "gate_latency_s": round(gate_lat, 1),
        "per_turn": per_turn,
    }


def summarize(results):
    tb = sum(r["tokens_before"] for r in results)
    ta = sum(r["tokens_after"] for r in results)
    saved = tb - ta
    saved_usd = saved / 1e6 * SONNET_INPUT_USD_PER_MTOK
    cost = sum(r["gate_cost_usd"] for r in results)
    lat = sum(r["gate_latency_s"] for r in results)
    tt = sum(r["n_tool_using_turns"] for r in results)
    rec = (sum(r["recall_tool_turns"] * r["n_tool_using_turns"]
               for r in results) / tt) if tt else 1.0
    return {
        "sessions": len(results),
        "tool_using_turns": tt,
        "recall_tool_turns": round(rec, 4),
        "tokens_before": tb,
        "tokens_after": ta,
        "reduction_pct": round(100 * saved / tb, 1) if tb else 0,
        "saved_usd_sonnet_input": round(saved_usd, 6),
        "gate_cost_usd": round(cost, 6),
        "gate_latency_s": round(lat, 1),
        "net_usd": round(saved_usd - cost, 6),
        "pays_for_itself": saved_usd > cost,
    }


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="+", required=True)
    ap.add_argument("--out", default="bench/toolgate/toolgate_results.json")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    catalog = build_catalog()
    print(f"catalog: {len(catalog)} entries "
          f"({sum(1 for t in catalog if t['kind']=='skill')} skills), "
          f"{catalog_tokens(catalog)} tokens wire-format")
    cache = ToolgateCache()
    results = []
    try:
        for p in args.inputs:
            print(f"== {p}")
            results.append(analyze_session(p, catalog, cache,
                                           verbose=not args.quiet))
    finally:
        print(f"cache: {cache.hits} hits, {cache.misses} misses")
        cache.close()
    summ = summarize(results)
    print(json.dumps(summ, indent=2))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    json.dump({"summary": summ, "sessions": results,
               "catalog": [t["name"] for t in catalog],
               "sonnet_input_usd_per_mtok": SONNET_INPUT_USD_PER_MTOK,
               "prompt_version": PROMPT_VERSION},
              open(args.out, "w"), indent=2)
    print("wrote", args.out)


if __name__ == "__main__":
    main()
