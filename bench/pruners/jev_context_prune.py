"""v2 context-aware Jev pruner: turn structure + rolling state + cache.

v1 (jev_prune.py) sliced tool-result text into 6000-char chunks and judged
each in isolation against a static prompt. v2 keeps v1's proven per-unit
Jev judging (p >= 0.5, calibrated) but fixes its blind spots:

  pass 0 (deterministic, no LLM): tool lifecycle on exact id-matched pairs.
      A tool_use/tool_result pair is one atomic unit. Only PROVABLY safe
      replacements, no question-relevance guessing:
        - STALE read: file edited after it was read  -> [stale] marker
        - SUPERSEDED read: same file re-read later   -> [superseded] marker
      (Headroom's read_lifecycle idea, exact-match only — never the
      near-duplicate collapsing that destroyed needle recall in round 2.)
      Thinking blocks are dropped at parse time (conclusions live in text).
  pass 1 (Jev, rolling state): walk turns in order. State = task intent +
      compressed ledger of keep/one-liner decisions so far (1-2 lines each).
      Two granularities:
        - tool pairs: one batched noul call per pair ("is this tool output
          needed?"); p >= 0.5 keeps verbatim, else a free deterministic
          one-line outcome note replaces it ("ran `aws sso login`: ok").
          Long results are windowed (6000 chars) inside one batched call so
          every byte is judged — keep if ANY window says keep.
        - turn text: assistant chatter judged per turn (KEEP explanations
          that answer the user; DROP redundant status text). Small turns
          (<MIN_TURN_CHARS) and the last PROTECT_RECENT turns are auto-kept:
          judging them costs more than they can save.
      Unresolved errors are ALWAYS kept verbatim, no Jev call.
  pass 2 (optional --summarize): gray-zone text turns (0.3 <= p < 0.5) get a
      one-line summary via ONE batched Aegis call (haiku-class). Skipped by
      default: re-reading a turn to summarize it costs ~its own size in
      input tokens, so it never pays in single-run token economics. Silent
      drop is the default fallback, including when approvals block.

Fail-safes: user turns (questions) are never dropped; unresolved errors are
never dropped; if compression inflates tokens, revert to the original.

sqlite decision cache (~/.agent_squeeze/decisions.sqlite) keyed by
sha256(prompt_version + unit content) -> (keep, p), so reruns are free.
(Headroom's compression-store idea, local and exact-keyed.)

Output JSON schema matches jev_prune.py (score.py-compatible).

Usage:
    python pruners/jev_context_prune.py inputs/real_task2.json \\
        outputs/real_task2_v2.json [--summarize] [--protect-prefix N]

--protect-prefix N: keep the first N chars of the transcript (whole leading
turns) byte-identical — pass 0 and pass 1 never touch them, so the next
provider call serves that prefix from prompt cache (0.1x input price).
Only the tail is judged. Default 0 (classic path).
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", ".."))
from agent_squeeze.context import (  # noqa: E402
    parse, task_intent, iter_tool_units, pair_target, turn_cache_key,
)

API_MODEL = "typesafe/jev-1.13"
PROMPT_VERSION = "jev-context-v4"   # bump when judging changes (cache key)
PROTECT_RECENT = 2       # last N turns are live context: never judged
MIN_TURN_CHARS = 800     # text below this is kept without a Jev call
KEEP_P = 0.5             # Jev calibrated cutoff (same as v1)
SUMMARIZE_P = 0.3        # text turns in [SUMMARIZE_P, KEEP_P) = gray zone
WINDOW_CHARS = 6000      # judging window inside one batched Jev call
TEXT_QUESTION_TRUNC = 12000

OR_DECIDES_SKILL = os.path.expanduser(
    "~/workspace/skills/openrouter/bin/or_decide.py")
CACHE_DB = os.path.expanduser("~/.agent_squeeze/decisions.sqlite")

FRAMING = (
    "You are a precise context-pruning judge for an AI agent's transcript. "
    "The transcript is structured as TURNS (one user or assistant message, "
    "with its tool calls and their results kept together as atomic units). "
    "Decide whether each piece is REQUIRED for what comes next."
)

PAIR_RULES = (
    "KEEP this tool output (answer true) when: it contains facts, numbers, "
    "names, or findings the final answer could depend on AND that substance "
    "is not already captured in the ledger or in what comes later; it "
    "records an error, anomaly, or outlier; it is the only copy of "
    "information the user asked about.\n"
    "DROP it (answer false) when: it is routine boilerplate (heartbeats, "
    "health checks, login banners, directory listings already acted on); "
    "its substance is fully captured in the ledger or superseded by what "
    "comes later (e.g. a later turn summarizes these findings).\n"
    "When in doubt, KEEP."
)

TEXT_RULES = (
    "KEEP the turn's text (answer true) when it: answers or explains "
    "something to the user; records a decision, conclusion, or plan the "
    "agent is acting on; asks a question; contains the only copy of facts "
    "the task needs.\n"
    "DROP it (answer false) when it: is status chatter ('Let me check...', "
    "'On it.'); is exploration narration fully superseded by what comes "
    "later.\n"
    "When in doubt about user-facing explanations, KEEP."
)


# ---------------------------------------------------------------------------
# sqlite decision cache
# ---------------------------------------------------------------------------

def _cache_conn():
    os.makedirs(os.path.dirname(CACHE_DB), exist_ok=True)
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("CREATE TABLE IF NOT EXISTS decisions "
                 "(key TEXT PRIMARY KEY, keep INTEGER, p REAL, ts REAL)")
    return conn


class DecisionCache:
    def __init__(self):
        self.conn = _cache_conn()
        self.hits = 0
        self.misses = 0

    def get(self, key):
        row = self.conn.execute(
            "SELECT keep, p FROM decisions WHERE key=?", (key,)).fetchone()
        if row:
            self.hits += 1
            return bool(row[0]), row[1]
        self.misses += 1
        return None

    def put(self, key, keep, p):
        self.conn.execute(
            "INSERT OR REPLACE INTO decisions VALUES (?,?,?,?)",
            (key, int(keep), p, time.time()))
        self.conn.commit()

    def close(self):
        self.conn.close()


# ---------------------------------------------------------------------------
# Jev calls (batched per unit, like v1)
# ---------------------------------------------------------------------------

def jev_batch(state, questions):
    """POST one batched decisions call. Returns (answers, cost)."""
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


def _windows(text, size=WINDOW_CHARS):
    """Split text into ~size windows on line boundaries (v1-style)."""
    wins, cur, cur_len = [], [], 0
    for line in text.split("\n"):
        while len(line) > size:
            if cur:
                wins.append("\n".join(cur))
                cur, cur_len = [], 0
            wins.append(line[:size])
            line = line[size:]
        if not line.strip():
            continue
        if cur and cur_len + len(line) + 1 > size:
            wins.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += len(line) + 1
    if cur:
        wins.append("\n".join(cur))
    return wins or [""]


# ---------------------------------------------------------------------------
# pass 0: deterministic tool-lifecycle pass (provably safe only)
# ---------------------------------------------------------------------------

READ_TOOLS = {"Read"}
WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}


def _is_read(pair):
    return pair.name in READ_TOOLS


def _file_path_of(pair):
    inp = pair.input or {}
    for k in ("file_path", "path", "file", "notebook_path"):
        if inp.get(k):
            return str(inp[k])
    return ""


def _outcome_note(pair):
    """Free one-line outcome note replacing a dropped tool result."""
    tgt = pair_target(pair)
    n_lines = pair.result.count("\n") + 1 if pair.result else 0
    name = pair.name or "tool"
    if _is_read(pair):
        return f"[read {tgt or name}: {n_lines} lines, no errors]"
    if name in WRITE_TOOLS:
        verb = "edited" if name == "Edit" else "wrote"
        return f"[{verb} {tgt or 'file'}]"
    if name == "Bash":
        cmd = (tgt.split("\n")[0] or name)[:60]
        if pair.is_error:
            return f"[ran `{cmd}`: failed]"
        return f"[ran `{cmd}`: ok, {n_lines} lines output]"
    if name in ("Grep", "Glob"):
        return f"[searched {tgt or name}: {n_lines} lines]"
    if pair.is_error:
        return f"[{name} {tgt}: errored]"
    return f"[{name} {tgt or 'done'}: ok]"


def deterministic_pass(turns):
    """Replace only provably redundant/wrong tool results.

    STALE (file edited after read) and SUPERSEDED (same file re-read later,
    no edit between) reads get one-line markers. Everything else — including
    merely-old or unreferenced outputs — goes to the Jev judge, because a
    planted needle looks exactly like "old and unreferenced".
    Turns marked protected (--protect-prefix) are never touched: the prefix
    stays byte-identical for prompt-cache reuse.
    Returns (n_replaced, chars_saved).
    """
    pairs = list(iter_tool_units(turns))
    n_replaced, chars_saved = 0, 0

    protected_idx = {t.index for t in turns if getattr(t, "protected", False)}

    reads, writes = [], []  # (turn_idx, path, pair)
    for p in pairs:
        if not p.has_result:
            continue
        path = _file_path_of(p)
        if _is_read(p) and path:
            reads.append((p.result_turn, path, p))
        elif p.name in WRITE_TOOLS and path:
            writes.append((p.result_turn, path, p))

    for p in pairs:
        if not p.has_result or p.replacement:
            continue
        if p.use_turn in protected_idx or p.result_turn in protected_idx:
            continue  # protected prefix: byte-identical, never mark
        path = _file_path_of(p)
        if not (_is_read(p) and path):
            continue
        stale = any(wt > p.result_turn and wp == path
                    for wt, wp, _ in writes)
        superseded = any(rt > p.result_turn and rp == path
                         for rt, rp, qp in reads if qp is not p)
        if stale:
            p.replacement = (f"[read {path}: STALE — file edited "
                             f"afterwards; content outdated]")
        elif superseded:
            p.replacement = (f"[read {path}: SUPERSEDED — same file "
                             f"read again later]")
        if p.replacement:
            chars_saved += max(0, len(p.result) - len(p.replacement))
            n_replaced += 1
    return n_replaced, chars_saved


# ---------------------------------------------------------------------------
# pass 1: Jev with rolling state
# ---------------------------------------------------------------------------

def _later_success(pair, pairs):
    """True if a later non-error call of the same tool hits the same target.

    Hoisted to module level so the map-reduce pass (pass 1b) can share the
    unresolved-error fail-safe with the sequential pass (pass 1).
    """
    tgt = _file_path_of(pair) or pair_target(pair)
    for q in pairs:
        if q is pair or not q.has_result:
            continue
        if q.result_turn <= pair.result_turn:
            continue
        if q.name == pair.name and \
                (_file_path_of(q) or pair_target(q)) == tgt \
                and not q.is_error:
            return True
    return False

def _pair_ledger(pair):
    tgt = pair_target(pair)
    if pair.replacement:
        return f"[tool {pair.name} {tgt}]: {pair.replacement}"
    if not pair.has_result:
        return f"[tool {pair.name} {tgt}]: called, no result yet"
    if pair.is_error:
        return (f"[tool {pair.name} {tgt}]: ERROR — kept verbatim "
                f"({len(pair.result)} chars)")
    return f"[tool {pair.name} {tgt}]: kept ({len(pair.result)} chars)"


def _ledger_line(turn):
    txt = turn.text.strip().split("\n")[0][:160]
    core = txt if txt else "(no text)"
    return f"t{turn.index} [{turn.role}]: {core}"


def _future_line(turn):
    """Deterministic one-liner describing a turn that comes LATER than the
    unit being judged. Lets the judge see supersession without any
    near-duplicate content comparison."""
    txt = turn.text.strip().split("\n")[0][:120]
    parts = [txt] if txt else []
    for p in turn.tool_pairs:
        tgt = pair_target(p)
        if p.replacement:
            parts.append(f"[tool {p.name} {tgt}: {p.replacement[:60]}]")
        elif p.has_result:
            parts.append(f"[tool {p.name} {tgt}: {len(p.result)} chars"
                         f"{' ERROR' if p.is_error else ''}]")
        else:
            parts.append(f"[tool call {p.name} {tgt}]")
    core = " | ".join(parts) or "(empty)"
    return f"t{turn.index} [{turn.role}]: {core[:160]}"


def _pass2_summarize(gray_turns, stats, summarize):
    """Gray-zone text turns (SUMMARIZE_P <= p < KEEP_P): opt-in one-line
    Aegis summaries via ONE batched call. Shared by sequential and
    map-reduce passes. Default (summarize=False): silent drop."""
    if not (summarize and gray_turns):
        return
    total_chars = sum(len(t.text) for t in gray_turns)
    if total_chars >= 1500:
        summaries = aegis_summarize_batch(
            [(t.index, t.text[:2000]) for t in gray_turns])
        stats["aegis_calls"] = 1 if summaries else 0
        if summaries:
            for t, s in zip(gray_turns, summaries):
                if s:
                    t.text_dropped = False
                    t.summarized = s
                    stats["summarized"] += 1
                    stats["gray"] -= 1
    else:
        print(f"  [summarize] skipped: only {total_chars} gray chars; "
              f"a summary call would cost more than it saves",
              file=sys.stderr)


def jev_pass(turns, intent, cache, summarize=False):
    n = len(turns)
    ledger = []
    future_lines = [_future_line(t) for t in turns]
    stats = {"jev_calls": 0, "jev_questions": 0,
             "pairs_kept": 0, "pairs_onelined": 0,
             "pairs_protected": 0, "text_kept": 0, "text_dropped": 0,
             "text_auto_kept": 0, "text_protected": 0,
             "gray": 0, "summarized": 0,
             "cost_usd": 0.0, "fail_safe_keeps": 0,
             "aegis_calls": 0}
    gray_turns = []

    def state_for(idx):
        led = "\n".join(ledger[-12:]) or "(none yet)"
        fut = "\n".join(future_lines[idx + 1:idx + 13]) or "(end of transcript)"
        return (f"{FRAMING}\n\nTask: {intent or '(no stated task)'}\n\n"
                f"Compressed record of turns/units kept so far:\n{led}\n\n"
                f"What comes LATER in the transcript:\n{fut}")

    def later_success(pair, pairs):
        return _later_success(pair, pairs)

    pairs = list(iter_tool_units(turns))

    for t in turns:
        if t.role == "user":
            stats["text_auto_kept"] += 1  # user questions are sacred
            ledger.append(_ledger_line(t))
            continue

        if getattr(t, "protected", False):
            # --protect-prefix: prefix stays byte-identical for prompt-cache
            # reuse. No Jev questions asked on protected turns; keep
            # everything verbatim and record in the ledger as kept.
            for p in t.tool_pairs:
                if p.has_result and not p.replacement:
                    stats["pairs_protected"] += 1
                ledger.append(_pair_ledger(p))
            stats["text_protected"] += 1
            ledger.append(_ledger_line(t))
            continue

        # --- judge each tool pair (batched windows, keep if any says keep)
        for p in t.tool_pairs:
            if not p.has_result:
                ledger.append(_pair_ledger(p))
                continue
            if p.replacement:  # stale/superseded from pass 0
                ledger.append(_pair_ledger(p))
                continue
            if p.is_error and not later_success(p, pairs):
                stats["fail_safe_keeps"] += 1  # unresolved: ALWAYS keep
                ledger.append(_pair_ledger(p))
                continue

            key = _pair_cache_key(p)
            cached = cache.get(key)
            if cached is not None:
                keep, pmax = cached
            else:
                wins = _windows(p.result)
                note = _outcome_note(p)
                questions = {
                    f"w{i}": {"type": "noul",
                              "instructions":
                                  f"{PAIR_RULES}\n\nTool: {p.name} "
                                  f"{pair_target(p)}\n\nOutput window "
                                  f"{i + 1}/{len(wins)}:\n{w}\n\n"
                                  f"If you answer false, the ENTIRE output "
                                  f"is replaced by this one-line note and "
                                  f"nothing else survives:\n{note}\n"
                                  f"Answer true only if the full output — "
                                  f"not just the note — is required."}
                    for i, w in enumerate(wins)
                }
                try:
                    answers, cost = jev_batch(state_for(t.index), questions)
                except Exception as e:
                    print(f"  [jev] pair {p.id}: judge failed ({e}); "
                          f"keeping", file=sys.stderr)
                    stats["fail_safe_keeps"] += 1
                    ledger.append(_pair_ledger(p))
                    continue
                stats["cost_usd"] += cost
                stats["jev_calls"] += 1
                stats["jev_questions"] += len(questions)
                pmax = max(a["noul"] for a in answers.values())
                keep = pmax >= KEEP_P
                cache.put(key, keep, pmax)
            if keep:
                stats["pairs_kept"] += 1
            else:
                stats["pairs_onelined"] += 1
                p.replacement = _outcome_note(p)
            ledger.append(_pair_ledger(p))

        # --- judge the turn's own text ---
        if t.index >= n - PROTECT_RECENT:
            stats["text_auto_kept"] += 1  # live context
            ledger.append(_ledger_line(t))
            continue
        if not t.text.strip():
            ledger.append(_ledger_line(t))
            continue
        if len(t.text) < MIN_TURN_CHARS:
            stats["text_auto_kept"] += 1  # judging costs more than it saves
            ledger.append(_ledger_line(t))
            continue

        key = turn_cache_key(t, PROMPT_VERSION)
        cached = cache.get(key)
        txt = t.text if len(t.text) <= TEXT_QUESTION_TRUNC else \
            t.text[:TEXT_QUESTION_TRUNC] + "\n[truncated for judging]"
        if cached is not None:
            keep, p = cached
        else:
            instructions = (f"{TEXT_RULES}\n\nTurn t{t.index} text:\n{txt}\n\n"
                            f"Answer true to keep, false to drop.")
            try:
                answers, cost = jev_batch(
                    state_for(t.index), {"q": {"type": "noul",
                                        "instructions": instructions}})
                p = answers["q"]["noul"]
            except Exception as e:
                print(f"  [jev] turn {t.index} text: judge failed ({e}); "
                      f"keeping", file=sys.stderr)
                stats["fail_safe_keeps"] += 1
                ledger.append(_ledger_line(t))
                continue
            stats["cost_usd"] += cost
            stats["jev_calls"] += 1
            stats["jev_questions"] += 1
            # unresolved-error fail-safe: a drop never strands an error pair
            if p < KEEP_P and any(q.has_result and q.is_error
                                  and not q.replacement
                                  for q in t.tool_pairs):
                keep, p = True, KEEP_P
                stats["fail_safe_keeps"] += 1
            else:
                keep = p >= KEEP_P
            cache.put(key, keep, p)
        if keep:
            stats["text_kept"] += 1
            ledger.append(_ledger_line(t))
        elif p >= SUMMARIZE_P:
            stats["gray"] += 1
            gray_turns.append(t)
            t.text_dropped = True  # may upgrade to summarized below
        else:
            stats["text_dropped"] += 1
            t.text_dropped = True
            ledger.append(f"t{t.index} [{t.role}]: text dropped")

    # --- pass 2: optional batched Aegis summarization of gray-zone turns ---
    _pass2_summarize(gray_turns, stats, summarize)
    return stats


# ---------------------------------------------------------------------------
# pass 1b: Jev map-reduce — ONE batched call for the whole transcript
# ---------------------------------------------------------------------------
#
# Sequential jev_pass walks turns in order with a rolling ledger: one Jev
# call per tool pair / judged text turn, so latency scales with turn count
# (the 3-15x v2 latency overhead). Map-reduce trades the rolling ledger
# for a static full-transcript skeleton and asks every question in ONE
# decisions call — Jev evaluates all questions in parallel in one request
# (the ego-jev "two decisions, one network round trip" pattern; TypeSafe
# lists map-reduce over datasets as a first-class Jev use case).
#
# Honest trade-off vs rolling state: an earlier decision no longer informs a
# later one inside the same pass. Compensated by (a) the static skeleton —
# every unit's one-line outcome note is visible to every question, and
# (b) the same question texts and cache keys, so decisions are comparable
# across modes and share the sqlite decision cache.
#
# Fail-safes are identical to pass 1: unresolved errors always kept
# verbatim, user turns sacred, protected/prefix turns skipped, dropped
# pairs get a one-line outcome note (never silently lost), and a text drop
# never strands an error pair.
#
# Research sources: ego-jev (github.com/ZephyrDeng/ego-jev) — one System One
# call per DOM step; firecrawl.dev/blog/what-is-jev — map-reduce + the
# "gate in front of an agent" pattern; docs.typesafe.ai/introduction/
# coding-agents — where Jev fits in a coding agent's loop.

def jev_pass_mapreduce(turns, intent, cache, summarize=False):
    n = len(turns)
    stats = {"jev_calls": 0, "jev_questions": 0,
             "pairs_kept": 0, "pairs_onelined": 0,
             "pairs_protected": 0, "text_kept": 0, "text_dropped": 0,
             "text_auto_kept": 0, "text_protected": 0,
             "gray": 0, "summarized": 0,
             "cost_usd": 0.0, "fail_safe_keeps": 0,
             "aegis_calls": 0}
    gray_turns = []
    pairs = list(iter_tool_units(turns))

    # Static skeleton: every turn on one deterministic line, past and
    # future both visible (replaces the rolling decision ledger).
    tmap = "\n".join(_future_line(t) for t in turns) or "(empty transcript)"
    state = (f"{FRAMING}\n\nTask: {intent or '(no stated task)'}\n\n"
             f"TRANSCRIPT MAP — one line per turn (past and future all "
             f"visible at once; each judged unit's FULL text is inside "
             f"its own question below):\n{tmap}")

    questions = {}          # qid -> noul question
    pair_units = []         # (pair, cache_key, [qids], outcome_note)
    text_units = []         # (turn, cache_key, qid)

    for t in turns:
        if t.role == "user":
            stats["text_auto_kept"] += 1  # sacred
            continue
        if getattr(t, "protected", False):
            for p in t.tool_pairs:
                if p.has_result and not p.replacement:
                    stats["pairs_protected"] += 1
            stats["text_protected"] += 1
            continue

        # --- tool-pair units: same questions as the sequential pass ---
        for p in t.tool_pairs:
            if not p.has_result or p.replacement:
                continue  # pass 0 handled (stale/superseded markers)
            if p.is_error and not _later_success(p, pairs):
                stats["fail_safe_keeps"] += 1  # unresolved: ALWAYS keep
                continue
            key = _pair_cache_key(p)
            cached = cache.get(key)
            if cached is not None:
                _apply_pair(stats, p, *cached)
                continue
            wins = _windows(p.result)
            note = _outcome_note(p)
            qids = []
            for i, w in enumerate(wins):
                qid = f"p{t.index}_{p.id}_w{i}"
                questions[qid] = {
                    "type": "noul",
                    "instructions":
                        f"{PAIR_RULES}\n\nTool: {p.name} "
                        f"{pair_target(p)}\n\nOutput window "
                        f"{i + 1}/{len(wins)}:\n{w}\n\n"
                        f"If you answer false, the ENTIRE output "
                        f"is replaced by this one-line note and "
                        f"nothing else survives:\n{note}\n"
                        f"Answer true only if the full output — "
                        f"not just the note — is required.",
                }
                qids.append(qid)
            pair_units.append((p, key, qids, note))

        # --- text units: same questions and auto-keep rules as sequential ---
        if t.index >= n - PROTECT_RECENT:
            stats["text_auto_kept"] += 1  # live context
            continue
        if not t.text.strip():
            continue
        if len(t.text) < MIN_TURN_CHARS:
            stats["text_auto_kept"] += 1  # judging costs more than it saves
            continue
        key = turn_cache_key(t, PROMPT_VERSION)
        cached = cache.get(key)
        if cached is not None:
            _apply_text(stats, gray_turns, t, *cached)
            continue
        txt = t.text if len(t.text) <= TEXT_QUESTION_TRUNC else \
            t.text[:TEXT_QUESTION_TRUNC] + "\n[truncated for judging]"
        qid = f"t{t.index}"
        questions[qid] = {
            "type": "noul",
            "instructions": (f"{TEXT_RULES}\n\nTurn t{t.index} text:\n{txt}\n\n"
                             f"Answer true to keep, false to drop."),
        }
        text_units.append((t, key, qid))

    # ONE decisions call for every uncached unit in the transcript.
    if questions:
        try:
            answers, cost = jev_batch(state, questions)
        except Exception as e:
            print(f"  [jev mapreduce] judge failed ({e}); keeping "
                  f"{len(pair_units)} pairs + {len(text_units)} text turns",
                  file=sys.stderr)
            stats["fail_safe_keeps"] += len(pair_units) + len(text_units)
            stats["pairs_kept"] += len(pair_units)
            stats["text_kept"] += len(text_units)
            _pass2_summarize(gray_turns, stats, summarize)
            return stats
        stats["cost_usd"] += cost
        stats["jev_calls"] += 1
        stats["jev_questions"] += len(questions)
        for p, key, qids, note in pair_units:
            pmax = max(answers[q]["noul"] for q in qids)
            keep = pmax >= KEEP_P
            cache.put(key, keep, pmax)
            _apply_pair(stats, p, keep, pmax)
        for t, key, qid in text_units:
            p = answers[qid]["noul"]
            keep = p >= KEEP_P
            # unresolved-error fail-safe: a drop never strands an error pair
            if p < KEEP_P and any(q.has_result and q.is_error
                                  and not q.replacement
                                  for q in t.tool_pairs):
                keep, p = True, KEEP_P
                stats["fail_safe_keeps"] += 1
            cache.put(key, keep, p)
            _apply_text(stats, gray_turns, t, keep, p)

    _pass2_summarize(gray_turns, stats, summarize)
    return stats


def _apply_pair(stats, p, keep, pmax):
    if keep:
        stats["pairs_kept"] += 1
    else:
        stats["pairs_onelined"] += 1
        p.replacement = _outcome_note(p)


def _apply_text(stats, gray_turns, t, keep, p):
    if keep:
        stats["text_kept"] += 1
    elif p >= SUMMARIZE_P:
        stats["gray"] += 1  # may be upgraded by pass 2, else dropped
        gray_turns.append(t)
        t.text_dropped = True
    else:
        stats["text_dropped"] += 1
        t.text_dropped = True


def _pair_cache_key(pair):
    h = hashlib.sha256()
    h.update(PROMPT_VERSION.encode())
    h.update(b"\npair\n")
    h.update(f"{pair.name}\n{pair_target(pair)}\n".encode())
    h.update(pair.result.encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# pass 2: Aegis batched summarization (opt-in)
# ---------------------------------------------------------------------------

def aegis_summarize_batch(items):
    """One Aegis call -> one-line summaries for gray-zone text turns.

    Returns list of summary strings ("" on any failure -> silent drop
    fallback). A single batched call = at most one credential approval
    prompt. Economics: only call when the caller verified it pays.
    """
    numbered = "\n\n".join(
        f"--- TURN {i} ---\n{txt}" for i, txt in items)
    prompt = (
        "For each transcript turn below, write a ONE-LINE summary "
        "(<=25 words) capturing what was said or decided. "
        "Reply with exactly one numbered line per turn, in order, like:\n"
        "1. <summary>\n2. <summary>\n\n" + numbered)
    script = (
        "import os, sys\n"
        "sys.path.insert(0, '/opt/hatch/skills/skill-creator/bin')\n"
        "from dynamic_credentials import dynamic_credential_entry\n"
        "entry = dynamic_credential_entry('custom.aegis')\n"
        "surr = str(entry['surrogate']).strip()\n"
        "assert surr.startswith('hsurr:'), 'no surrogate'\n"
        "env = dict(os.environ)\n"
        "env.update({'ANTHROPIC_AUTH_TOKEN': surr,\n"
        " 'ANTHROPIC_BASE_URL': 'https://gateway-aegis.internjobs.io/anthropic',\n"
        " 'ANTHROPIC_CUSTOM_HEADERS': 'x-model-provider: bedrock',\n"
        " 'ANTHROPIC_MODEL': 'bedrock/aegis-haiku'})\n"
        "os.execvpe('claude', ['claude', '-p', sys.argv[1]], env)\n")
    try:
        out = subprocess.run(
            [sys.executable, "-c", script, prompt],
            capture_output=True, text=True, timeout=180)
    except Exception as e:
        print(f"  [summarize] aegis call failed ({e}); silent drop",
              file=sys.stderr)
        return []
    if out.returncode:
        print(f"  [summarize] aegis errored; silent drop "
              f"({out.stderr[-200:]})", file=sys.stderr)
        return []
    summaries, idx = [], 0
    for line in (l.strip() for l in out.stdout.splitlines() if l.strip()):
        m = re.match(r"^(\d+)[.)]\s*(.+)$", line)
        if m and int(m.group(1)) == idx + 1:
            summaries.append(m.group(2).strip())
            idx += 1
    while len(summaries) < len(items):
        summaries.append("")
    return summaries[:len(items)]


# ---------------------------------------------------------------------------
# reassembly
# ---------------------------------------------------------------------------

def reassemble(turns):
    """Rebuild messages_after in the original bench-input message format."""
    out = []
    for t in turns:
        if getattr(t, "summarized", ""):
            out.append({"role": "assistant",
                        "content": f"[summarized turn {t.index} (jev gray "
                                   f"zone): {t.summarized}]"})
            continue
        text_dropped = getattr(t, "text_dropped", False)
        for pos, m in t.source:
            role = m.get("role")
            if role == "tool":
                tid = m.get("tool_call_id", "")
                pair = next((p for p in t.tool_pairs if p.id == tid), None)
                if pair is not None and pair.replacement:
                    out.append({**m, "content": pair.replacement})
                else:
                    out.append(m)
            elif role == "assistant":
                if text_dropped and not m.get("tool_calls"):
                    continue  # pure chatter turn: remove the message
                if text_dropped:
                    out.append({**m, "content": ""})
                else:
                    out.append(m)
            else:
                out.append(m)
    return out


# ---------------------------------------------------------------------------
# --protect-prefix: cache-stable leading turns
# ---------------------------------------------------------------------------

def _turn_chars(t):
    """Original char size of a turn's messages (json encoding of source)."""
    return sum(len(json.dumps(m)) for _, m in t.source)


def mark_protected(turns, protect_chars):
    """Mark the largest leading turn prefix fitting in `protect_chars`.

    A turn is never split (cache.py's split_protected uses the same
    message-boundary rule). Protected turns are byte-identical in the
    output and skipped by both pruning passes. Returns (n_protected,
    protected_chars).
    """
    if protect_chars <= 0:
        return 0, 0
    cum, n, chars = 0, 0, 0
    for t in turns:
        c = _turn_chars(t)
        if cum + c > protect_chars:
            break
        cum += c
        t.protected = True
        n += 1
        chars += c
    return n, chars


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="bench input JSON")
    ap.add_argument("output", help="output JSON path")
    ap.add_argument("--summarize", action="store_true",
                    help="one-line Aegis summaries for gray-zone text turns "
                         "(default: silent drop)")
    ap.add_argument("--protect-prefix", type=int, default=0, metavar="N",
                    help="keep the first N chars of the transcript "
                         "(whole leading turns) byte-identical so the next "
                         "call serves them from prompt cache; only the "
                         "tail is judged (default: 0 = classic path)")
    ap.add_argument("--mapreduce", action="store_true",
                    help="one batched Jev decisions call for the whole "
                         "transcript (static skeleton state) instead of the "
                         "sequential rolling-state walk; far fewer Jev "
                         "calls (default: off)")
    args = ap.parse_args()

    t0 = time.time()
    with open(args.input) as f:
        doc = json.load(f)

    turns = parse(doc["messages"])
    for t in turns:  # per-run flags (not part of Turn defaults)
        t.text_dropped = False
        t.summarized = ""
        t.protected = False
    intent = doc.get("question") or task_intent(turns)

    n_protected, protected_chars = mark_protected(turns, args.protect_prefix)

    n_replaced, _det_saved = deterministic_pass(turns)

    cache = DecisionCache()
    try:
        if args.mapreduce:
            jstats = jev_pass_mapreduce(turns, intent, cache,
                                        summarize=args.summarize)
            method = "jev-context-v2-mapreduce"
        else:
            jstats = jev_pass(turns, intent, cache,
                              summarize=args.summarize)
            method = "jev-context-v2"
    finally:
        cache.close()

    messages_after = reassemble(turns)

    chars_before = len(json.dumps(doc["messages"]))
    chars_after = len(json.dumps(messages_after))
    if chars_after > chars_before:
        # inflation guard (Headroom does the same): never ship expansion
        messages_after = doc["messages"]
        chars_after = chars_before
        inflated = True
    else:
        inflated = False

    latency = time.time() - t0
    out = {
        "input_id": doc["id"],
        "method": method,
        "question": doc.get("question", ""),
        "evidence": doc.get("evidence", []),
        "expected_answer_contains": doc.get("expected_answer_contains", []),
        "messages_before": doc["messages"],
        "messages_after": messages_after,
        "stats": {
            "latency_s": round(latency, 2),
            "cost_usd": round(jstats["cost_usd"], 6),
            "aegis_calls": jstats["aegis_calls"],
            "turns_total": len(turns),
            "jev_calls": jstats["jev_calls"],
            "jev_questions": jstats["jev_questions"],
            "pairs_kept": jstats["pairs_kept"],
            "pairs_onelined": jstats["pairs_onelined"],
            "pairs_protected": jstats["pairs_protected"],
            "text_protected": jstats["text_protected"],
            "protect_prefix_chars": args.protect_prefix,
            "protected_turns": n_protected,
            "protected_chars": protected_chars,
            "text_turns_kept": jstats["text_kept"] + jstats["text_auto_kept"],
            "text_turns_dropped": jstats["text_dropped"] + jstats["gray"],
            "text_turns_summarized": jstats["summarized"],
            "deterministic_replacements": n_replaced,
            "cache_hits": cache.hits,
            "cache_misses": cache.misses,
            "fail_safe_keeps": jstats["fail_safe_keeps"],
            "inflation_guard": inflated,
            "chars_before": chars_before,
            "chars_after": chars_after,
            "token_reduction_pct": round(
                100 * (chars_before - chars_after) / chars_before, 2)
            if chars_before else 0.0,
        },
    }
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(out, f)
    s = out["stats"]
    print(f"{method}: {len(turns)} turns, det {n_replaced}, "
          f"jev {jstats['jev_calls']} calls/{jstats['jev_questions']}q "
          f"({cache.hits} hits), pairs kept {jstats['pairs_kept']}/"
          f"one-lined {jstats['pairs_onelined']}/"
          f"protected {jstats['pairs_protected']}, text dropped "
          f"{s['text_turns_dropped']} (protected {jstats['text_protected']}), "
          f"{s['token_reduction_pct']:.1f}% "
          f"reduction, ${s['cost_usd']:.6f} in {latency:.1f}s -> {args.output}")


if __name__ == "__main__":
    main()
