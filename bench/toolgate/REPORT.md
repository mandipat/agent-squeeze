# toolgate: per-turn tool/skill gating — benchmark report

## Headline numbers

| metric | value |
|---|---|
| Selection recall (tool-using turns) | **20/24 = 83.3%** |
| Tool-catalog tokens, before → after | **54,873 → 6,520 (−88.1%)** |
| Gating cost (Jev, all 4 sessions) | $0.002842 |
| Saved @ Sonnet input ($3.00/MTok) | $0.145059 |
| Net | **+$0.142 saved; pays for itself ~51x** |
| Gating latency | ~0.7 s / turn (sequential; uncached) |
| Micro-precision of selected set | 24.4% (avg 2.10 selected vs 0.62 oracle) |

**One-line verdict:** gating pays for itself ~51x on replay with 83% recall, but
4 of 24 tool-using turns would have arrived without a tool the agent actually
called — a live deployment needs a fail-safe (below) before this is safe.

## Per-session

| session | assistant turns | tool-using turns | recall | avg selected | avg oracle | tokens before → after | reduction |
|---|---|---|---|---|---|---|---|
| real_task1 (bug fix) | 9 | 4 | 0.750 | 1.6 | 0.44 | 12,663 → 1,065 | 91.6% |
| real_task2 (exploration) | 15 | 13 | 0.846 | 2.7 | 0.87 | 21,105 → 3,272 | 84.5% |
| real_task3 (multi-file) | 8 | 4 | 0.750 | 1.4 | 0.50 | 11,256 → 950 | 91.6% |
| real_task4 (pytest) | 7 | 3 | 1.000 | 2.3 | 0.43 | 9,849 → 1,233 | 87.5% |

## The 4 misses (all informative)

| turn | agent used | Jev selected | what happened |
|---|---|---|---|
| task1 t5 | Write | Edit, skill:test-runner | judge predicted an edit; agent wrote a new file |
| task2 t1 | Task ("Agent") | Read, Bash, Glob, LS | judge missed the subagent-delegation step entirely |
| task2 t13 | Read | *(empty)* | judge concluded no tools needed; agent re-read a file |
| task3 t2 | Write | Bash | judge predicted shell work; agent wrote a file |

Pattern: misses are Write-vs-Edit confusion and under-prediction of
delegation/re-reads — all cases where the "when in doubt, say false"
framing bites. Every miss is a turn that would have failed live (tool
not in the `tools` array → API rejects the call).

## Oracle comparison

The oracle (tools actually used that turn) averages **0.62 tools**; Jev
selects **2.10** — it over-selects ~3.4x to buy its 83% recall at 24%
precision. For a gating layer that is the right side of the trade: a
missing tool breaks the turn, an extra tool just costs tokens. The
residual 17% miss rate is the actual problem, not the over-selection.

## Economics

- Catalog modeled at **1,407 tokens** wire-format (20 entries: 17 tools +
  3 skills, name + description + input_schema as JSON).
- Gating cost: $0.002842 total Jev spend (30 uncached turns × 20 questions;
  reruns free via the sqlite cache — 180/180 hits on re-run).
- Saved input tokens: 48,353 → $0.145 at the assumed **$3.00/MTok Sonnet
  input** price (stated assumption; tools array is billed as input).
- Net **+$0.142** on four short sessions. Real sessions are longer and
  real catalogs (with MCP servers) are 10–30k tokens, so the ratio
  improves with scale; gating cost is flat per turn.

## Skill gating — worked example

Three skill entries were in the catalog on every call. `skill:test-runner`
was selected **only in the two test-involving sessions** (task1 bug-fix +
test, task4 pytest writing — 9 selections across their turns) and
**never** in the exploration (task2) or multi-file-change (task3)
sessions. `skill:code-review` and `skill:doc-writer` were never selected
anywhere in this corpus. Per-turn skill gating works as intended; the
corpus just never needed a review or docs pass.

## Method

- `agent_squeeze/toolgate.py`: catalog modeled on Claude Code's real tools
  (Read, Write, Edit, Bash, BashOutput, KillShell, Glob, Grep, LS,
  TodoWrite, WebFetch, WebSearch, Task, NotebookRead/Edit, 2 MCP tools,
  3 skills). Token counts via tiktoken cl100k_base on the rendered
  `tools`-array JSON.
- Per assistant turn: state = task intent + rolling one-line ledger of
  prior turns + the latest turn in fuller form. The turn's **own**
  content is never in the state (that would leak the tool calls being
  predicted). One batched Jev call per turn, one noul question per
  catalog entry, keep p ≥ 0.5. Fail-open: a Jev error keeps all tools.
- Cache: `~/.agent_squeeze/decisions.sqlite`, table `toolgate`, key =
  sha256(prompt_version + state + tool name).
- Replay: walk the 4 real sessions through `agent_squeeze/context.py`;
  recall = used ⊆ selected per turn ("Agent" normalized to "Task").

## Honest caveats

1. **The catalog is approximated.** Names/descriptions track Claude Code's
   real ones; schemas are compact reconstructions, not byte copies. Treat
   1,407 tokens as an order-of-magnitude stand-in — the real catalog with
   MCP servers is larger, which favors gating.
2. **Replay recall is necessary but not sufficient.** A live agent handed a
   reduced toolset may choose different (worse) strategies than the
   transcript shows, or fail in ways replay can't see. Suggested follow-up:
   live A/B — run task2-style explorations with full vs gated catalogs and
   compare task success, not just tool recall.
3. **83% is not shippable.** Production needs a fail-safe: e.g. always
   union a 3–4 tool always-on core (Read/Bash/Glob/Grep), or retry-on-
   rejection (on tool-not-available, re-issue the turn with the full
   catalog — one extra call, bounded cost).
4. **Framing leans aggressive.** "When in doubt, say false" caused 2 of
   the 4 misses. A "when in doubt, say true" variant would trade tokens
   for recall; worth an ablation.
5. Token proxy is cl100k_base, not Anthropic's tokenizer; pricing assumes
   $3.00/MTok Sonnet input (public list at time of writing).
