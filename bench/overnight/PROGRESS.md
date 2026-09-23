# agent-squeeze overnight build — PROGRESS.md

Runs every 30 min, Tue 2026-09-22 night → Wed 2026-09-23 08:00 PDT.
One focused improvement per run, ~20 min. Commits are local-only — **never
push** (no standing GitHub token; pushes need the user).

## Run 1 — 2026-09-22 23:13 PDT: cache-aware squeeze (prompt-cache interplay)

**What:** new module `agent_squeeze/cache.py` + offline benchmark
`bench/cache_aware/` + 6 unit tests (`agent_squeeze/test_cache.py`, all pass).

**Why:** provider prompt caches key on exact prefix bytes, so any rewrite of
an earlier message breaks the whole cache entry — compaction is the #1
cache-killer in long sessions (floppa2003 prompt-caching-playbook). Pruning
oldest tool outputs while keeping message structure intact beats summarizing
the prefix. The existing squeezer was already half-way there (it never
rewrites user/assistant text); cache-aware mode extends the guarantee to tool
messages inside a protected prefix.

**API:**
- `squeeze_cache_aware(msgs, task, protect_tokens=1024)` — prefix returned
  byte-identical; only the tail is Jev-squeezed. `policy_fn` injectable for
  offline use (defaults to `jev.score_chunks`).
- `stable_prefix_tokens(original, squeezed)` — leading byte-identical tokens
  = what the next call serves from cache.
- `cache_breakpoints(msgs, protect_tokens)` — suggests up to 2 Anthropic
  `cache_control` breakpoint indices (prefix end + last message, which slides
  forward each turn).
- `inject_cache_control(anthropic_msgs, bps)` — adds `{"type": "ephemeral"}`
  to the last text block of breakpoint messages; does not mutate input.
- `next_turn_cost_model` / `session_cost_model` — simulate next-call and
  10-turn input cost with Anthropic ratios (cache read 0.1x, write 1.25x).

**Numbers** (deterministic boilerplate policy, zero paid calls — no Jev,
decision cache untouched, OpenRouter key budget preserved):

| input | before | naive −% | aware −% | stable prefix | 10-turn naive $ | 10-turn aware $ |
|---|---|---|---|---|---|---|
| synthetic_monitoring (101k tok) | 101052 | 14.21% | 13.97% | 2381 | 2.6082 | 2.5528 |
| sre_incident | 7942 | −0.01% | −0.01% | 80 | 0.2383 | 0.2364 |
| mixed_grind | 28703 | 0.0% | 0.0% | 28703 | 0.8611 | 0.1937 |
| github_triage | 7788 | 0.0% | 0.0% | 57 | 0.2336 | 0.2323 |

The policy legitimately drops nothing on the three real/adversarial inputs
(all-unique tool outputs), so the synthetic monitoring session (seeded,
byte-identical, 60 polling rounds with repeated boilerplate + 3 high-signal
errors) carries the comparison: cache-aware gives up 0.24pp reduction for a
2,381-token protected prefix; 10-turn session $2.55 vs $2.61 naive. The gap
compounds with larger prefixes and longer sessions — a rewritten prefix pays
full price on every byte, every turn.

**Sources** (prompt-cache interplay research):
- floppa2003/skills prompt-caching-playbook — compaction is the #1
  cache-killer; pruning-not-summarizing preserves the prefix; breakpoint
  slides forward each turn (Anthropic up to 4 breakpoints)
- bm629/agent-skills token-optimization SKILL.md — stable prefix first,
  breakpoint at end of largest stable block, byte-identical between calls;
  never edit an earlier message in place
- papr-ai/paprwork PROMPT_CACHE_AND_COST_OPTIMIZATION.md — system prompt
  never mutated per turn (cache-safe); file reads stay full in history for
  cache stability; compression handles overflow
- Medium/Adnan Masood (Aug 2026) — Anthropic 5-min cache breaks even on 2nd
  use; 20k-token prefix at 80% reuse cuts stable-prefix cost 55–70%

**Next (candidate runs):**
- Wire `protect_tokens` into the CLI (`--protect-prefix`) and the v2
  context-aware pruner.
- Claude Code compaction-hook recipe: run cache-aware squeeze as a
  SessionStart/PreCompact hook so compaction stops nuking the cache.
- Jev-call benchmark on synthetic_monitoring to verify the deterministic
  policy tracks real Jev keep/drop (uses cached decisions; key near cap).
- TypeScript SDK spike; MCP server compression recipe; prompt-cache TTL
  tuning (5-min vs 1-hour breakpoints per the Masood numbers).

**Blocked:** nothing.

**Awaiting push:** everything since the sprint started (local commits only).


## Run 2 — 2026-09-22 23:45 PDT: wire cache-aware squeeze into CLI + server

**What:** `--protect-prefix N` flag on `cli squeeze` (0 = classic path,
default) and new `POST /v1/squeeze-cache-aware` endpoint on the service;
both dispatch to `cache.squeeze_cache_aware`. New wiring test
`agent_squeeze/test_cache_wiring.py` (3 tests, all pass): prefix stays
byte-identical through CLI and server paths, classic path untouched.
README "Use" and plugin `SKILL.md` now document both.

**Why:** Run 1's cache-aware mode was library-only — nobody could call it
from the CLI or the service that agents actually hit. This makes it usable.
The server endpoint also matters for the Claude Code compaction-hook recipe
(candidate run): a PreCompact hook can POST the transcript to
`/v1/squeeze-cache-aware` and get a cache-safe squeezed transcript back.

**Numbers** (deterministic stub policy, zero paid calls — Jev stubbed at
`cache.jev.score_chunks`, decision cache and OpenRouter key untouched):

| path | protected tokens | reduction |
|---|---|---|
| CLI `--protect-prefix 100` | 11 | 0.09% |
| CLI default (no flag) | — (classic path) | 0.09% |
| server `/v1/squeeze-cache-aware` | 11 | 0.09% |

(The toy 8-message transcript is nearly all unique text, so the stub
policy legitimately drops nothing — the assertion that matters is
byte-identical prefix + `protected_tokens` present in stats.)

**Next (candidate runs):**
- Claude Code compaction-hook recipe: PreCompact hook → POST
  `/v1/squeeze-cache-aware` → replace transcript; docs in `claude-plugin/`.
- Jev-call benchmark on synthetic_monitoring to verify the deterministic
  policy tracks real Jev keep/drop (uses cached decisions; key near cap).
- TypeScript SDK spike; MCP server compression recipe; `--protect-prefix`
  into `fleet` and the v2 context-aware pruner.

**Blocked:** nothing.

**Awaiting push:** everything since the sprint started (local commits only).
## Run 3 — 2026-09-22 23:50 PDT: Claude Code compaction-hook recipe

**What:** new `claude-plugin/hooks/` dir: `squeeze-precompact.py` (stdlib-only
PreCompact hook), `README.md` (install recipe + settings.json snippet), and
`test_squeeze_precompact.py` (3 tests, all pass). Linked from the skill's
cache-aware section.

**Why:** Claude Code's built-in compaction summarizes the transcript, which
rewrites earlier messages and destroys the provider prompt cache — every
post-compaction turn re-pays full input price, and the summary drops the
original goal/constraints (documented failure mode, anthropics/claude-code
#22638). PreCompact cannot block or rewrite the transcript (hook contract
verified via jayantdevkar/claude-code-karma and jcdendrite/claude-config
behavior docs), but it *can* return `additionalContext`. The hook POSTs the
transcript to `/v1/squeeze-cache-aware` (protect=4096 default) and injects a
verbatim, extractive evidence digest + stats into the post-compaction window.
Fail-safe by design: service down / bad transcript → exit 0 silently, never
breaks the user's session.

**Numbers** (fake HTTP service, zero paid calls — Jev/service never touched):

| test | result |
|---|---|
| digest injection (fake service, 4-line transcript) | PASS — goal, kept evidence, stats in `additionalContext`; hook requested `protect_tokens=4096` |
| service down (nothing listening) | PASS — exit 0, stdout empty |
| missing transcript | PASS — exit 0, stdout empty |
| existing suites (`test_cache`, `test_cache_wiring`) | PASS — unaffected |

**Next (candidate runs):**
- Live-fire the hook against a real Claude Code session end-to-end
  (manual `/compact`, verify the digest lands post-compaction).
- Jev-call benchmark on synthetic_monitoring to verify the deterministic
  policy tracks real Jev keep/drop (uses cached decisions; key near cap).
- TypeScript SDK spike; MCP server compression recipe; `--protect-prefix`
  into `fleet` and the v2 context-aware pruner.

**Blocked:** nothing.

**Awaiting push:** everything since the sprint started (local commits only).

## Run 4 — 2026-09-23 00:05 PDT: admit-time tool-result gate (Jev research fold-in)

**What:** new module `agent_squeeze/admit.py` + offline benchmark
`bench/admit_time/run.py` + 7 unit tests (`agent_squeeze/test_admit.py`,
all pass; full suite 19/19).

**Why (research → code):** web research on how people use Jev/TypeSafe
decision models turned up a decisive asymmetry: pi-jev-context measured that
*retroactive* pruning of old history is risky — raw Jev dropped 73% of items
later needed on real sessions (21% even with deterministic source
protection), so its shadow old-context pruning was never applied — while
*write-time* trimming (cut long tool output to verbatim key lines before it
enters context) saved 31–53% tokens on held-out sets with 0 key lines lost
and only 1/184 real-session replays hiding something used later. Folded in:
(1) admit-time gate — judge a tool result when its relevance is fresh, not
weeks-old; (2) LiteLLM's Jev-compaction notice pattern (tool call + ids
intact, dropped result replaced with a short notice); (3) winnow's admission
gate (judge before admitting into context); (4) omp-fast-jev-compaction's
operational honesty — deterministic, attributable decisions.

**API:**
- `admit_tool_result(name, text, task, store, policy_fn, head_lines=8,
  tail_lines=8)` — decisions: `keep_full` (short/errors), `trim` (verbatim
  head+tail lines, middle held), `notice` (LiteLLM-style one-liner for
  repetitive boilerplate), `hold` (ref-only admission). `policy_fn` injectable
  for offline use; `jev_admit` (two noul questions batched in one Jev call:
  relevance + excerpt-sufficiency) for production.
- `HoldStore` — off-context verbatim storage; `readmit(ref)` is
  byte-identical; `readmit_if_mentioned(followup)` re-admits any held result
  the agent later references by ref. Nothing is ever lost.
- `admit_session(results, task)` — batch gating + stats (chars in/out,
  reduction %, per-decision counts).

**Numbers** (deterministic offline policy, zero paid calls — no Jev,
decision cache untouched, OpenRouter key budget preserved):

| result | chars | decision | admitted |
|---|---|---|---|
| 60-round poll log (3 needles + 1 OOM error) | ~3.5k | trim | head/tail verbatim |
| 2000 JSON records | ~143k | trim | head/tail verbatim |
| 800× identical heartbeat | ~11k | notice | one-liner |
| `ls` listing | 26 | keep_full | verbatim |
| deploy traceback | ~3.5k | keep_full | verbatim (error path) |

Total: 153,850 → 5,749 admitted chars (−96.26%), 2 holds (149,347 chars held).
Needle recall 3/3 (top/bottom needles in admitted verbatim lines, middle
needle recoverable byte-identically via hold ref); all 3 error lines kept
verbatim; hold roundtrips 2/2 byte-identical. The headline number is inflated
by synthetic boilerplate, as it should be — the real claim is the mechanism:
relevance judged fresh at write time with lossless recall.

**Sources** (Jev/decision-model usage research):
- robokrunch/awesome-jev — pi-jev-context: write-time verbatim trim 31–53%
  saved, 0 key lines lost; shadow old-context pruning 73% drop of
  later-needed items → never applied
- docs.litellm.ai/blog/typesafe-jev-compaction — LiteLLM Jev compaction:
  tool calls + IDs kept, results below 0.2 replaced with notice, last
  assistant message + tool exchange protected
- edwardyen724-g/jev-compactor — "Jev judges relevance. Code decides
  structure." nothing kept is ever rewritten; ~300ms per pass
- Reamd7/omp-fast-jev-compaction — keepThreshold 0.5, preserveRecentMessages,
  compactAtPercent 60, minReductionRatio, cooldownTokens (ops knobs)
- cobanov/awesome-jev — winnow (admission gate), yoshi (prune while
  preserving tool-call protocol), fast-jev-compaction (verbatim select)
- zentor.ai/blog/what-is-typesafe-jev — Jev request shape: state + typed
  questions, parallel in isolation, "no context-rot"; choice/score/noul
- jaredpalmer/kev — Apache-2.0 local Jev-like family (Kev-9B 0.812/0.837
  acc vs Jev 0.857) if we ever want a free on-device fallback

**Next (candidate runs):**
- Jev-call benchmark on synthetic_monitoring / admit corpus to verify the
  deterministic policy tracks real Jev keep/drop (uses cached decisions;
  key near cap — batch questions, reuse `~/.agent_squeeze/decisions.sqlite`).
- TypeScript SDK spike; MCP server compression recipe; `--protect-prefix`
  into `fleet` and the v2 pruner.
- Wire admit gate into the server (`/v1/admit`) and the toolgate path so
  agents can gate tool results live.
- Claude Code hook variant: admit-time gate inside a PostToolUse hook.

**Blocked:** nothing.

**Awaiting push:** everything since the sprint started (local commits only).
## Run 5 — 2026-09-23 00:25 PDT: wire admit gate into CLI + server

**What:** the admit-time gate (Run 4) was library-only — now it is callable
from the CLI and the service. New: `agent_squeeze/cli.py` `admit` subcommand
(JSONL `{"name","text"}` → admissions JSON + stats), server endpoints
`POST /v1/admit`, `/v1/admit-batch`, `/v1/readmit`, `/v1/readmit-if-mentioned`,
and `PersistentHoldStore` in `admit.py` (JSON-backed, `holds.json` under
`~/.agent_squeeze/`, `AGENT_SQUEEZE_HOLD_DIR` override; reloads on every
`hold()` so threaded requests and process restarts share state). 5 wiring
tests in `agent_squeeze/test_admit_wiring.py`, all pass; README "Use" and
plugin `SKILL.md` gained admit-time sections.

**Why:** a gate agents cannot call is a gate agents will not use. Batch
endpoint matters for harness loops that emit many tool results per turn;
persistent holds make refs issued at write time resolvable when the agent
later references them (`readmit-if-mentioned` scans a follow-up for refs).

**Numbers** (deterministic stub policy, zero paid calls — no Jev, decision
cache untouched, OpenRouter key budget preserved):

| path | decisions | reduction |
|---|---|---|
| CLI `admit` (3 results: 4.4k boilerplate, 3.7k unique log, 7 chars) | notice / trim / keep_full | 91.7% |
| server `/v1/admit` single | notice | 97.8%, readmit byte-identical |
| server `/v1/admit-batch` | keep_full×1, trim×1, notice×1 | 91.7% |
| `/v1/readmit` unknown ref | — | 404 as designed |
| hold persistence across store "restarts" | — | byte-identical |

Full suite still green: test_admit, test_cache, test_cache_wiring,
test_context (all pass).

**Next (candidate runs):**
- Claude Code PostToolUse hook: admit-time gate inside the tool loop
  (results gated before they ever reach the transcript).
- Jev-call benchmark on synthetic_monitoring / admit corpus to verify the
  deterministic policy tracks real Jev keep/drop (batch questions, reuse
  `~/.agent_squeeze/decisions.sqlite` — key near cap).
- TypeScript SDK spike; MCP server compression recipe; `--protect-prefix`
  into `fleet` and the v2 pruner.

**Blocked:** nothing.

**Awaiting push:** everything since the sprint started (local commits only).


## Run 6 — 2026-09-23 00:30 PDT: PostToolUse admit-time hook + write-time annotations

**What:** new `claude-plugin/hooks/admit-posttooluse.py` (stdlib-only
PostToolUse hook) + `test_admit_posttooluse.py` (6 tests, all pass);
annotation log + `annotation_probs` in `agent_squeeze/admit.py`;
`admit-readmit` CLI subcommand; hooks README recipe + settings snippet.

**Why:** the candidate from Runs 4–5: gate inside the tool loop. Relevance
is judged best when fresh — right after the tool call, not against cold
history (pi-jev-context: retroactive pruning dropped 73% of later-needed
items; write-time trim saved 31–53% with 0 key lines lost). The hook judges
each result with the free deterministic gate (Jev optional via
`AGENT_SQUEEZE_ADMIT_JEV=1`), appends the judgment to
`~/.agent_squeeze/admit-annotations.jsonl`, and injects the admitted text
(verbatim excerpt + hold ref) as `additionalContext` on trim/notice/hold —
`keep_full` stays silent. `annotation_probs` turns the log into per-chunk
keep priors that plug straight into `squeeze_with_policy`, so fresh
write-time judgments anchor the next retroactive squeeze instead of the
pruner guessing cold.

**Honest limit (documented in README):** PostToolUse cannot rewrite the
tool result already in the transcript; the raw result stays in history. The
hook steers the model to the gated excerpt and records the judgment — the
token savings land on the next squeeze pass.

**Numbers** (deterministic heuristic, zero paid calls — no Jev, decision
cache and OpenRouter key untouched):

| test | result |
|---|---|
| 800-line boilerplate heartbeat | notice: excerpt + ref injected, full text NOT re-emitted, hold byte-identical |
| long traceback error | keep_full, hook silent |
| 2-char result | keep_full, hook silent |
| dict tool_response | extracted, notice |
| malformed stdin / empty payload | exit 0 silent |
| annotation priors → `squeeze_with_policy` | notice-annotated result's chunks 2 → 1 kept (squeezer's conservative keep-one-chunk floor); unmatched message → neutral 0.5 |

CLI `admit-readmit`: ref resolves byte-identically; unknown ref → exit 1
with clear stderr.

**Next (candidate runs):**
- Jev-call benchmark on synthetic_monitoring / admit corpus to verify the
  deterministic policy tracks real Jev keep/drop (batch questions, reuse
  `~/.agent_squeeze/decisions.sqlite` — key near cap).
- TypeScript SDK spike; MCP server compression recipe; `--protect-prefix`
  into `fleet` and the v2 pruner.
- Live-fire the PostToolUse hook in a real Claude Code session; measure how
  often the model actually acts on the excerpt vs the raw result.

**Blocked:** nothing.

**Awaiting push:** everything since the sprint started (local commits only).

## Run 7 — 2026-09-23 00:43 PDT: MCP server (compression as MCP tools)

**What:** new `agent_squeeze/mcp_server.py` (stdlib-only stdio JSON-RPC 2.0)
exposing `squeeze_transcript`, `admit_tool_result`, `readmit` as MCP tools;
`agent_squeeze/test_mcp.py` (7 tests, all pass); `bench/mcp_server/README.md`
recipe (claude_desktop_config.json snippet + `claude mcp add`); README "Use"
+ skill SKILL.md notes; `agent-squeeze-mcp` console script in pyproject.

**Why:** the MCP-server-compression candidate from the mission list. Agents
on MCP hosts (Claude Desktop, Claude Code) get compression without running
the HTTP service. Free deterministic policy by default — boilerplate-detector
for squeeze, admit.py's deterministic heuristic for the admit gate — so the
default path costs nothing and hits no paid endpoints (key near cap);
`AGENT_SQUEEZE_MCP_JEV=1` opts into real TypeSafe Jev for squeeze.

**Numbers** (offline, zero paid calls — no Jev, decision cache and OpenRouter
key untouched):

| test | result |
|---|---|
| 800-line heartbeat (2 chunks) + traceback transcript, protect=10 | boilerplate chunk dropped (`[squeezed: kept 1/2 chunks]`), traceback kept verbatim, cost $0 |
| admit 800-line boilerplate → notice + ref; readmit | byte-identical roundtrip |
| readmit unknown ref / unknown tool / parse error | −32602 / −32602 / −32700 as designed |
| full suite (test_mcp, test_admit, test_admit_wiring, test_cache, test_cache_wiring, test_context) | 31 passed, 0 failed |

Note: pytest is not installed on this VM — tests ran via a plain
`test_*`-function runner (plain asserts, no fixtures), all pass.

**Next (candidate runs):**
- TypeScript SDK spike; `--protect-prefix` into `fleet` and the v2 pruner.
- Jev-call benchmark on synthetic_monitoring / admit corpus to verify the
  deterministic policies track real Jev keep/drop (batch questions, reuse
  `~/.agent_squeeze/decisions.sqlite` — key near cap; consider waiting for
  the cap reset).
- Live-fire the PostToolUse hook in a real Claude Code session; measure how
  often the model acts on the excerpt vs the raw result.

**Blocked:** nothing.

**Awaiting push:** everything since the sprint started (local commits only).


## Run 8 — 2026-09-23 00:58 PDT: TypeScript SDK spike (@agent-squeeze/sdk)

**What:** new `ts-sdk/` package — typed client (`AgentSqueezeClient`) for all
7 service endpoints (`/v1/squeeze`, `/v1/squeeze-cache-aware`,
`/v1/squeeze-fleet`, `/v1/admit`, `/v1/admit-batch`, `/v1/readmit`,
`/v1/readmit-if-mentioned`) plus bearer auth via `AGENT_SQUEEZE_TOKEN` env and
per-request timeouts. Zero runtime dependencies (Node ≥ 18 global fetch);
ships `dist/` via `npm run build` (tsc, committed as source only).
`AgentSqueezeError` carries `.status`/`.body` (e.g. 404 on unknown readmit
ref). Includes `README.md`, `examples/quickstart.mjs`, and a `node:test`
suite that spins up a stub HTTP service and round-trips every endpoint.
Repo README "Use" now shows the SDK path. `.gitignore` gained
`node_modules/` and `ts-sdk/dist/`.

**Why:** the TypeScript-SDK candidate from the mission list. Python CLI + HTTP
service covered server-side agents; TS/Node agents (LangChain.js, Vercel AI
SDK, Mastra, Claude Code hooks in TS) had no typed on-ramp. The SDK also
makes the service's JSON contract explicit — any endpoint drift now fails
compilation of the typed response shapes.

**Numbers** (offline, zero paid calls — no Jev, decision cache and OpenRouter
key untouched):

| check | result |
|---|---|
| `tsc` strict build (noUnusedLocals/Parameters) | clean |
| `node --test test/client.test.mjs` (stub HTTP service) | 1/1 pass — all 7 endpoints round-trip; Bearer header asserted on every call; 404 → `AgentSqueezeError{status:404}` |
| full suite via `npm test` | build + test green |

Not yet live-fired against the real Python service (that would call the Jev
policy on `/v1/squeeze` — paid; key near cap). Contract matches server.py
request/response shapes by inspection.

**Next (candidate runs):**
- `--protect-prefix` into `fleet` and the v2 pruner.
- Live-fire the PostToolUse hook in a real Claude Code session; measure how
  often the model acts on the excerpt vs the raw result.
- Jev-call benchmark on synthetic_monitoring / admit corpus to verify the
  deterministic policies track real Jev keep/drop (batch questions, reuse
  `~/.agent_squeeze/decisions.sqlite` — key near cap; consider waiting for
  the cap reset).

**Blocked:** nothing.

**Awaiting push:** everything since the sprint started (local commits only).


## Run 9 — 2026-09-23 01:13 PDT: `--protect-prefix` into fleet + fix CLI double-call bug

**What:** `squeeze_fleet(..., protect_tokens=0, policy_fn=None)` — per-agent
pass-2 squeeze is now cache-aware when `protect_tokens > 0` (via
`squeeze_cache_aware`); pass-1 cross-agent exact-dedup unchanged.
`cli fleet --protect-prefix N` and `POST /v1/squeeze-fleet`
`{"protect_tokens": N}` expose it. New `agent_squeeze/test_fleet.py` (4
tests, all pass). README fleet section documents the flag.

**Bug fix (real cost saver):** `cmd_squeeze` called `squeeze_transcript`
unconditionally *before* the `if args.protect_prefix > 0` branch — so a
`--protect-prefix` run fired TWO full Jev passes (double paid cost, double
latency), and a default run fired it twice too. The first unconditional call
is removed; exactly one pass per invocation now.

**Why:** the candidate queued since Run 1. Fleet sessions in long runs are
the worst cache-killers: N agents each compacting independently rewrite N
prefixes, paying full input price on every turn for every agent. Per-agent
protected prefixes keep fleet sessions cache-warm the same way Run 1 did for
single transcripts.

**Numbers** (deterministic stub policy, zero paid calls — no Jev, decision
cache and OpenRouter key untouched):

| check | result |
|---|---|
| library: 2 agents, shared duplicate tool result, protect=100 | dedup 1 marker (pass 1 intact); per-agent first messages byte-identical; `protected_tokens` > 0; b's 600-line heartbeat 2 chunks → 1 kept (floor), a's `keepme` chunk kept |
| library default (protect=0) | classic path unchanged, no `protected_tokens` in stats |
| CLI `fleet --protect-prefix 100` | prefix byte-identical, `protected_tokens` > 0 in report |
| server `/v1/squeeze-fleet` + `protect_tokens: 100` | prefix byte-identical, `protected_tokens` > 0 |
| full suite | 35 tests pass (31 prior + 4 fleet) |

**Next (candidate runs):**
- `--protect-prefix` into the v2 context-aware pruner (`bench/pruners/jev_context_prune.py`) — protect the parsed turn prefix before the Jev passes.
- Live-fire the PostToolUse hook in a real Claude Code session; measure how often the model acts on the excerpt vs the raw result.
- Jev-call benchmark on synthetic_monitoring / admit corpus to verify the deterministic policies track real Jev keep/drop (batch questions, reuse `~/.agent_squeeze/decisions.sqlite` — key near cap; consider waiting for the cap reset).

**Blocked:** nothing.

**Awaiting push:** everything since the sprint started (local commits only).


## Run 10 — 2026-09-23 01:35 PDT: `--protect-prefix` into the v2 pruner

**What:** `bench/pruners/jev_context_prune.py` gained `--protect-prefix N`
(chars, default 0 = classic path). `mark_protected()` marks the largest
leading whole-turn prefix fitting N chars (a turn is never split, same
message-boundary rule as `cache.split_protected`); protected turns skip
`deterministic_pass` (no stale/superseded markers) and the Jev pass (no
judge questions — pair and text decisions short-circuit to keep-verbatim
with ledger lines), so the output prefix is byte-identical and prompt-cache
safe. New stats: `protect_prefix_chars`, `protected_turns`,
`protected_chars`, `pairs_protected`, `text_protected`. New
`bench/pruners/test_jev_context_protect.py` (4 tests, all pass).

**Why:** the candidate queued since Run 1. The v2 pruner rewrites early
turns like every other compactor — the exact cache-killing behavior Run 1
documented. Now a v2 run with `--protect-prefix` leaves a cache-stable
prefix while the Jev judge works the tail, with fresh ledger lines recording
the kept prefix.

**Numbers** (stub judge, zero paid calls — no Jev, real decision cache
untouched via temp `CACHE_DB` override):

| check | result |
|---|---|
| protected turns byte-identical in output (synthetic) | PASS — first 2 turns (2 msgs + merged tool result) identical |
| pass 0 skip: protected read NOT marked superseded | PASS — `replacement == ""` (classic path marks it SUPERSEDED) |
| stub judge dropped everything: judge never asked about protected content | PASS — stub asserts marker absent from every question instruction |
| tail still judged: long tail chatter dropped | PASS — `text_dropped`, jev_calls ≥ 1 |
| real input `sre_incident.json` + `--protect-prefix 3000` (stubbed) | 1 turn / 114 chars protected, prefix byte-identical, tail judged |
| full library suite | 5 relevant suites green; `test_admit`/`test_mcp` fail identically on pristine tree (runner invocation quirk, pre-existing) |

**Next (candidate runs):**
- Live-fire the PostToolUse hook in a real Claude Code session; measure how
  often the model acts on the excerpt vs the raw result.
- Jev-call benchmark on synthetic_monitoring / admit corpus to verify the
  deterministic policies track real Jev keep/drop (batch questions, reuse
  `~/.agent_squeeze/decisions.sqlite` — key near cap; consider waiting for
  the cap reset).

**Blocked:** nothing.

**Awaiting push:** everything since the sprint started (local commits only).

## Run 11 — 2026-09-23 01:50 PDT: GitHub quickstart (zero-key 60-second tour)

**What:** new `quickstart/` dir — `fleet.json` (2-agent sample: frontend
chasing `TRANSACTION_TIMEOUT_77X`, backend chasing `SLOWQ_9ab2`, identical
install boilerplate across both, polling/healthcheck noise),
`needles.json` (per-agent needles), `run.py` (swaps the Jev judge for the
free deterministic boilerplate policy — no key, $0 — runs
`squeeze_fleet`, prints per-agent/fleet numbers, exits 1 if a needle is
lost), `README.md` (the tour page with expected output). Top-level README
"Quick start" now leads with the no-key path before the keyed service path.

**Why:** the GitHub-quickstart candidate from the mission list. Every README
path previously demanded `OPENROUTER_API_KEY` up front — a visitor hitting
the repo at 2am bounced. Now `git clone && python quickstart/run.py`
demonstrates pass-1 cross-agent exact dedup, pass-2 boilerplate drop, and
needle survival in one command with zero setup. The sample data is honest
about the mechanism: healthcheck-noise chunks drop, the needle chunks stay
via unique-line keep + the never-empty fail-safe.

**Numbers** (free deterministic policy, zero paid calls — no Jev, decision
cache and OpenRouter key untouched):

| check | result |
|---|---|
| frontend | 3,584 → 1,634 tokens (54.4%) |
| backend | 5,134 → 1,591 tokens (69.0%) |
| fleet | 12,144 → 3,225 tokens (73.4%), 1 cross-agent exact duplicate |
| needle check | 2/2 survive (TRANSACTION_TIMEOUT_77X, SLOWQ_9ab2) |
| cost / latency | $0.000000, ~0.0s (no judge calls) |

No library code touched — no test impact (suite was 35 green at Run 9/10).

**Next (candidate runs):**
- Live-fire the PostToolUse hook in a real Claude Code session; measure how
  often the model acts on the excerpt vs the raw result.
- Jev-call benchmark on synthetic_monitoring / admit corpus to verify the
  deterministic policies track real Jev keep/drop (batch questions, reuse
  `~/.agent_squeeze/decisions.sqlite` — key near cap; consider waiting for
  the cap reset).

**Blocked:** nothing.

**Awaiting push:** everything since the sprint started (local commits only).


## Run 12 — 2026-09-23 02:05 PDT: deep-research agent compression (new sector)

**What:** new module `agent_squeeze/research.py` + synthetic benchmark
`bench/research/run.py` + 7 unit tests (`agent_squeeze/test_research.py`,
all pass; full suite 42/42). Research agents hoard fetched pages — search
results, blog posts wrapped in nav/cookie/footer boilerplate, docs, forum
threads, 404s. `squeeze_research_pages` keeps cited/high-signal lines
verbatim (citations must be byte-exact quotes), holds the rest off-context
(reuses admit.py's `HoldStore`, byte-identical readmit), and reduces
pure-boilerplate pages, error pages, and exact-duplicate re-fetches to a
one-liner notice. Decisions: keep_full / keep_quotes / notice.
`deterministic_policy` is the free offline judge (boilerplate, noise, and
error-page regexes + cited-quote + task-keyword signal detection); a real
Jev `policy_fn` is injectable for production. Nothing kept is ever
rewritten — the same rule as the admit gate (Run 4).

**Why:** the deep-research-agent candidate from the mission list. Prior runs
covered coding-agent transcripts, fleet, MCP, hooks, TS SDK, cache interplay;
research agents were the last big sector unexplored. Their failure mode is
distinct: the final report cites a handful of passages but the whole page
stays in context — this keeps the citeable lines verbatim and holds the
rest, so the report's quotes survive while the noise leaves context.

**Numbers** (deterministic policy, zero paid calls — no Jev, decision cache
and OpenRouter key untouched):

| check | result |
|---|---|
| synthetic 8-page corpus (search, blog+boilerplate, docs, press release, exact-duplicate re-fetch, 404, noisy forum, whitepaper) | 6,302 → 3,774 chars (−40.1%), 1,577 → 945 tokens |
| blog with nav/cookie/ads/footer | 16 → 4 lines (boilerplate gone, 2 cited quotes kept verbatim) |
| forum thread (12× "+1/following", "lol", "unsubscribed") | 23 → 5 lines (signal kept: threshold advice, 94%-agreement replay note) |
| exact-duplicate re-fetch | notice → "duplicate of {url}", held |
| 404 page | notice, held |
| needle recall | 2/2 FOUND; cited quotes 2/2 verbatim |
| unit tests | 7/7; full suite 42/42 |

Honest limits (documented): synthetic corpus is small (6.3k chars) — the
mechanism is the claim, not the headline number; near-duplicates (press
release vs docs) are not collapsed, only byte-identical re-fetches.

**Next (candidate runs):**
- Live-fire the PostToolUse hook in a real Claude Code session; measure how
  often the model acts on the excerpt vs the raw result.
- Jev-call benchmark on synthetic_monitoring / admit corpus to verify the
  deterministic policies track real Jev keep/drop (batch questions, reuse
  `~/.agent_squeeze/decisions.sqlite` — key near cap; consider waiting for
  the cap reset).
- Support-chatbot sector: compress long conversation histories while keeping
  resolution summaries + tool-call protocol intact.

**Blocked:** nothing.

**Awaiting push:** everything since the sprint started (local commits only).

## Run 13 — 2026-09-23 02:15 PDT: support-chatbot compression (new sector)

**What:** new module `agent_squeeze/support.py` + synthetic benchmark
`bench/support_chat/run.py` + 7 unit tests (`agent_squeeze/test_support.py`,
all pass; full suite 49/49). Support histories bloat in predictable ways:
greetings, identity verification re-asked by every handoff, template
apologies, hold-music equivalents, verbatim repeated troubleshooting
scripts, "anything else" closings. `squeeze_support_thread` works
turn-by-turn with cross-turn memory (`seen_templates`, `seen_facts`):
decisions `keep_full` (first fact establishment, resolution summary),
`keep_excerpt` (verbatim fact lines, rest held via `HoldStore`, byte-identical
readmit), `notice` (pleasantries, template apologies, exact-duplicate script
steps, re-verification once facts exist). Free deterministic regex policy;
a real Jev `policy_fn` is injectable. Nothing kept is ever rewritten —
the admit-gate rule.

**Why:** the support-chatbot candidate from the mission list (Run 12 queued
it). Its failure mode is distinct from transcripts/research: the *same*
information is re-established repeatedly (verification × N handoffs) while
the resolution facts that matter for handoff appear once at the end. The
cross-turn memory collapses the repeats; the resolution turn is protected
verbatim.

**Numbers** (deterministic policy, zero paid calls — no Jev, decision cache
and OpenRouter key untouched):

| check | result |
|---|---|
| synthetic 14-turn thread (greeting, verify, apology, script×2, handoff re-verify, hold, resolution, closing) | 464 → 326 tokens (−29.7%), 5/14 turns noticed, 2 held |
| needle recall | 3/3 (ACC-77410, CASE-2026-9931, $129.99) |
| resolution summary | `keep_full` verbatim (asserted in bench + unit test) |
| repeated troubleshooting script | 2nd copy → notice `[duplicate of earlier turn]` |
| handoff re-verification | notice `[re-verification; facts established earlier]` |
| unit tests | 7/7; full suite 49/49 |

**Bug fixed during the run:** `REVERIFY_RE` false-positived on "Confirmation
email" (`confirm` substring + "address") — trigger now word-boundary
anchored (`\bconfirm\b`).

Honest limit: 29.7% is small because short turns (≤200 chars) are kept
verbatim — judging costs more than it saves at that size. Real support
threads run 50–200 turns with multi-handoff repeats, where the repeat
collapse compounds.

**Next (candidate runs):**
- Live-fire the PostToolUse hook in a real Claude Code session; measure how
  often the model acts on the excerpt vs the raw result.
- Jev-call benchmark on synthetic_monitoring / admit corpus to verify the
  deterministic policies track real Jev keep/drop (batch questions, reuse
  `~/.agent_squeeze/decisions.sqlite` — key near cap; consider waiting for
  the cap reset).

**Blocked:** nothing.

**Awaiting push:** everything since the sprint started (local commits only).

## Run 14 — 2026-09-23 02:40 PDT: data-pipeline agent compression (new sector)

**What:** new module `agent_squeeze/pipeline.py` + synthetic benchmark
`bench/pipeline/run.py` + 7 unit tests (`agent_squeeze/test_pipeline.py`,
all pass; full suite 56/56). Pipeline agents (ETL/ELT harnesses, step
runners) bloat context with poll-interval heartbeats, connection banners,
repeated DDL/schema echoes, row-sample reprints, and retry noise.
`squeeze_pipeline_run` squeezes step-by-step with cross-step memory
(`seen_schemas`, `seen_patterns`): decisions `keep_full` (errors/tracebacks,
final metrics, short steps ≤400 chars — judging costs more than it saves
at that size), `keep_excerpt` (verbatim metric/schema lines, rest held via
`HoldStore`, byte-identical readmit), `notice` (pure progress/heartbeat
steps, exact-duplicate schema echoes, pattern×N collapses). Free
deterministic regex policy; a real Jev `policy_fn` is injectable. Nothing
kept is ever rewritten — the admit-gate rule.

**Why:** the data-pipeline-agent candidate from the mission list. Prior runs
covered coding transcripts, fleet, research, support-chat, MCP, hooks, TS
SDK, cache interplay; pipeline runners were the remaining high-noise
sector. Its failure mode is distinct: failures hide in the middle of
verbose steps, so errors are always kept verbatim and only metrics/schema
lines survive otherwise.

**Numbers** (deterministic policy, zero paid calls — no Jev, decision cache
and OpenRouter key untouched):

| check | result |
|---|---|
| synthetic 5-step ETL (connect, 60-heartbeat extract, transform with re-echoed DDL, traceback validate, load) | 3,331 → 825 chars (−75.2%), 832 → 206 tokens |
| extract (60 heartbeats) | keep_excerpt: heartbeat noise gone, rows/duration/checksum kept |
| transform (identical DDL to extract) | notice `[schema identical to earlier step]` |
| validate (traceback + `negative revenue for order OR-99120`) | keep_full verbatim |
| needle recall | 4/4 (OR-99120, 9f2c1ad4b8, 999,997, rows rejected: 3) |
| held roundtrip | byte-identical |
| unit tests | 7/7; full suite 56/56 (`test_admit` fails identically on pristine tree — pre-existing runner quirk per Run 10) |

**Bug fixed during the run:** new files must run with `PYTHONPATH=.`
(module-import fix, not a code bug); also the classic per-test fight with
the `FULL_KEEP_CHARS` floor — fixtures padded past 400 chars so the judge
path is actually exercised.

**Next (candidate runs):**
- Live-fire the PostToolUse hook in a real Claude Code session; measure how
  often the model acts on the excerpt vs the raw result.
- Jev-call benchmark on synthetic_monitoring / admit corpus to verify the
  deterministic policies track real Jev keep/drop (batch questions, reuse
  `~/.agent_squeeze/decisions.sqlite` — key near cap; consider waiting for
  the cap reset).
- Prompt-cache TTL tuning bench (5-min vs 1-hour breakpoints) per the
  Masood numbers from Run 1 research.

**Blocked:** nothing.

**Awaiting push:** everything since the sprint started (local commits only).

## Run 15 — 2026-09-23 02:43 PDT: prompt-cache TTL tuning (5-min vs 1-hour)

**What:** new module `agent_squeeze/ttl.py` + deterministic simulator
`bench/ttl_tuning/run.py` + 7 unit tests (`agent_squeeze/test_ttl.py`, all
pass; full suite 63/63). Runs 1–10 protected a stable prefix so provider
prompt caches survive squeezing; this run answers the remaining pricing
question: Anthropic's 5-min TTL writes at 1.25x and reads at 0.1x, the 1-hour
TTL writes at 2.0x and reads at 0.1x. `simulate_session` models the prefix as
one cache entry (each hit refreshes the TTL, a gap beyond it forces a
rewrite), the dynamic tail always at full price; `recommend_ttl` runs both
and picks the cheaper one (ties → 5-min, cheaper write).

**Numbers** (pure arithmetic over deterministic gap patterns, zero paid calls
— no Jev, decision cache and OpenRouter key untouched; $3/MTok Sonnet-class):

| session rhythm | prefix | 5-min | 1-hour | winner | saving |
|---|---|---|---|---|---|
| coding_burst (gaps < 90s) | 200k | $1.48 | $1.93 | 5-min | 23.3% |
| research_slow (gaps 5–25 min) | 200k | $8.38 | $1.93 | 1-hour | 77.0% |
| mixed (bursts + 45-min pauses) | 200k | $3.55 | $1.93 | 1-hour | 45.6% |
| overnight_idle (gaps 3–6 h) | 200k | $4.54 | $7.24 | 5-min | 37.3% |

Breakeven sweep (50k prefix, 12 turns, constant gap): gap ≤ 5 min → 5-min
wins (hit 0.92 both, the 1.25x-vs-2.0x write decides); gap 8+ min → 1-hour
wins by 4–5x (5-min hit rate collapses to 0.00, every turn rewrites the
prefix at 1.25x). Non-obvious flip: at multi-hour idle gaps *neither* TTL
hits, so the cheaper 5-min write wins again — 1-hour is only for the
5-min-to-~1-hour gap band, where the read savings amortize the pricier write
and the margin grows with prefix size.

**Rule of thumb (now in README):** default 5-min; switch a long session to
1-hour TTL when typical inter-turn gap is 5–60 min *and* the protected prefix
is ≥ ~50k tokens.

**Next (candidate runs):**
- Wire `recommend_ttl` into the CLI/server (`squeeze-ttl` subcommand or
  `--ttl-recommend` flag) so agents pick the TTL from their session stats.
- Live-fire the PostToolUse hook in a real Claude Code session; measure how
  often the model acts on the excerpt vs the raw result.
- Jev-call benchmark on synthetic_monitoring / admit corpus to verify the
  deterministic policies track real Jev keep/drop (batch questions, reuse
  `~/.agent_squeeze/decisions.sqlite` — key near cap; consider waiting for
  the cap reset).

**Blocked:** nothing.

**Awaiting push:** everything since the sprint started (local commits only).

## Run 16 — 2026-09-23 03:00 PDT: wire TTL recommender into CLI + server + TS SDK

**What:** the Run 15 TTL simulator is now callable from every surface:
`agent_squeeze squeeze-ttl --gaps 30,45,1200 --prefix 200000 --dynamic 2000`
(prints one-line verdict; `-o` writes full JSON), `POST /v1/recommend-ttl`
(accepts `turn_gaps_sec` or legacy `gaps`, validates input → 400 on
missing/non-list gaps), and TS SDK `client.recommendTtl(gaps, prefix,
dynamic, basePerMtok)` with a new `TtlRecommendation` type. New
`agent_squeeze/test_ttl_wiring.py` (6 tests, all pass): CLI recommends
1hour for slow gaps / 5min for bursts, JSON output roundtrips, empty gaps
rejected, server returns 1hour for [600,900] and 5min for [30,45], 400 on
missing gaps. TS stub test now round-trips all 8 endpoints. README
cache-aware section gained the "Pick the TTL" subsection with the rule of
thumb and a verified CLI example.

**Why:** the candidate queued at Run 15. A simulator nobody can call is a
number nobody uses — the TTL choice is per-session operational data
(inter-turn rhythm), so it belongs behind a one-command flag the harness or
the developer runs at session start, not buried in a bench script.

**Numbers** (pure arithmetic, zero paid calls — no Jev, decision cache and
OpenRouter key untouched):

| check | result |
|---|---|
| `squeeze-ttl --gaps 30,45,1200 --prefix 200000 --dynamic 2000` | 1hour (5min $1.5780 vs 1hour $1.3380; saves 15.2%, hit rates 0.333/0.667) |
| `squeeze-ttl --gaps 600,900,1200` | 1hour (asserted); `--gaps 30,45,60` → 5min (asserted) |
| new wiring tests | 6/6 pass |
| full Python suite | 69 passed, 0 failed |
| TS `tsc` strict build + `npm test` | green (8/8 endpoints round-trip) |

**Next (candidate runs):**
- MCP server: add a `recommend_ttl` tool (it exposes only the 3
  compression tools today).
- Live-fire the PostToolUse hook in a real Claude Code session; measure how
  often the model acts on the excerpt vs the raw result.
- Jev-call benchmark on synthetic_monitoring / admit corpus to verify the
  deterministic policies track real Jev keep/drop (batch questions, reuse
  `~/.agent_squeeze/decisions.sqlite` — key near cap; consider waiting for
  the cap reset).

**Blocked:** nothing.

**Awaiting push:** everything since the sprint started (local commits only).

## Run 17 — 2026-09-23 03:13 PDT: `recommend_ttl` MCP tool

**What:** the MCP server exposed only the 3 compression tools — the Run 15
TTL recommender (Run 16 wired it into CLI + HTTP + TS SDK) was unreachable
from MCP hosts. Added `recommend_ttl` to `agent_squeeze/mcp_server.py`:
takes `turn_gaps_sec` (required) + optional `prefix_tokens` (default
200k), `dynamic_tokens` (2k), `base_per_mtok` (3.0); returns the same
verdict shape as `ttl.recommend_ttl` (`recommended`, costs, `saving_pct`,
hit rates, rounded). Pure arithmetic, no network, $0 — consistent with the
server's free-deterministic default. Updated the docstring ("Three tools" →
"Four tools"), `bench/mcp_server/README.md` tool table, and the top-level
README tools list. New `test_recommend_ttl_*` tests in
`agent_squeeze/test_mcp.py` (3 tests; `tools/list` assertion now expects
the 4-tool set).

**Why:** the candidate queued at Run 16. An MCP host (Claude Desktop,
Claude Code) running agent-squeeze compression via the stdio server had no
way to ask "which cache TTL should this session use?" without also running
the HTTP service — the cheapest path for a small agent just got one tool
cheaper.

**Numbers** (offline, zero paid calls — no Jev, decision cache and OpenRouter
key untouched):

| check | result |
|---|---|
| `tools/call recommend_ttl` gaps [600,900,1200] | `1hour` (1hour < 5min cost, saving > 0) |
| `tools/call recommend_ttl` gaps [30,45,60] | `5min` |
| `tools/call recommend_ttl` missing gaps | −32602 as designed |
| `tools/list` | 4 tools with inputSchemas |
| full Python suite | 72 passed, 0 failed (69 prior + 3 new) |

**Next (candidate runs):**
- Live-fire the PostToolUse hook in a real Claude Code session; measure how
  often the model acts on the excerpt vs the raw result.
- Jev-call benchmark on synthetic_monitoring / admit corpus to verify the
  deterministic policies track real Jev keep/drop (batch questions, reuse
  `~/.agent_squeeze/decisions.sqlite` — key near cap; consider waiting for
  the cap reset).
- Voice/conversational-agent sector: compression of long spoken-dialogue
  transcripts (turns are short, interruptions heavy) — new bloat profile.

**Blocked:** nothing.

**Awaiting push:** everything since the sprint started (local commits only).


## Run 18 — 2026-09-23 03:45 PDT: voice/conversational-agent compression (new sector)

**What:** new module `agent_squeeze/voice.py` + synthetic benchmark
`bench/voice_dialogue/run.py` + 8 unit tests (`agent_squeeze/test_voice.py`,
all pass; full suite 77/77). Spoken-dialogue transcripts bloat differently
from chat: ASR fillers ("um", "uh", "you know"), backchannels ("mm-hmm",
"yeah, right"), barge-in fragments (cut-off utterances), confirmation
re-asks ("just to confirm..."), TTS readbacks of long content, and
silence/no-audio events. `squeeze_voice_dialogue` works turn-by-turn with
cross-turn memory (`seen_repeats`): decisions `keep_full` (commitments,
outcomes, corrections, short turns ≤160 chars — judging costs more than it
saves at that size), `keep_excerpt` (verbatim fact/commitment lines from
noisy turns, rest held via `HoldStore`, byte-identical readmit), `notice`
(fillers, backchannels, restated repeats, confirmation re-asks, readback
framing). Free deterministic word-set/regex policy; a real Jev `policy_fn`
is injectable. Nothing kept is ever rewritten — the admit-gate rule.

**Barge-in rule (research fold-in):** only what the agent *actually spoke*
may enter context (voice-agent practice — logging the full unspoken
generation is a documented hallucination-of-memory failure). A turn with
`interrupted=True` keeps only its `committed_text`; an interrupted turn with
nothing committed is a notice. The bench asserts the unspoken tail
("for Friday morning") never appears in the output. Also folded in:
restatement detection via Jaccard (near-identical rephrases) + a
recall-style content-word coverage check (long rambles that dilute Jaccard);
corrections ("No wait — Thursday, not Friday!") are explicitly *not*
restatements and stay keep_full.

**Numbers** (deterministic policy, zero paid calls — no Jev, decision cache
and OpenRouter key untouched):

| check | result |
|---|---|
| synthetic 18-turn spoken dialogue (booking intent, fillers, backchannels, barge-in, restated repeat, ramble restatement, 2 confirmation re-asks, noisy fact turn, TTS readback, commitment) | 335 → 299 tokens (−10.75%), 12 keep_full / 3 keep_excerpt / 4 notice, 3 held |
| needle recall | 3/3 (AX-4471, 4820 Meridian Ave, Thursday 10:30 AM) |
| barge-in honesty | PASS — unspoken tail absent from output |
| commitment turn | keep_full verbatim (asserted) |
| TTS readback (5-line itinerary) | keep_excerpt: only the 7:05 AM / 12:40 PM lines, framing held |
| confirmation re-ask after fact established | notice `[confirmation re-ask; fact established earlier]` |
| unit tests | 8/8; full suite 77/77 |

**Bugs fixed during the run:** (1) backchannel/filler word-sets missed
split tokens ("mm"/"hmm", "you"/"know") and comma combos ("Yeah, right.") —
word-set matching replaced whole-line regexes; (2) punctuation killed
Jaccard ("morning?" vs "morning") — tokenization now strips punctuation;
(3) restated-repeat threshold 0.55→0.45 plus the coverage check for rambles;
(4) dead `_tokens` helper after the `_content_words` refactor (NameError).

Honest limit (documented): spoken turns are tiny, so notice/excerpt labels
can outweigh the dropped text on short fixtures — the −10.75% is real but
modest; the mechanism (barge-in honesty, repeat collapse, readback
excerpting) is the claim. Long real-world calls (200+ turns of hold music,
repeated menus, re-read confirmations) are where this compounds.

**Sources** (voice-agent context research):
- dev-31/realtime-voice-call-agent agent-memory SKILL.md — three context
  strategies (truncate/summarise/retrieve) with their costs; compaction on a
  control message through the same ordering guarantees; per-message latency
  metadata inline in the transcript
- Medium/Kannappan Suresh — the barge-in sequence: kill TTS, cancel
  inference, commit *only what was actually spoken* to history
- aion_agent context-compaction.md — mid-turn compaction numbers
  (85k→12k tokens), compaction-block marker convention
- christianbalevski/adf memory-management.md — LLM-powered compaction via a
  signal-only tool, automatic threshold triggers

**Next (candidate runs):**
- Live-fire the PostToolUse hook in a real Claude Code session; measure how
  often the model acts on the excerpt vs the raw result.
- Jev-call benchmark on synthetic_monitoring / admit corpus to verify the
  deterministic policies track real Jev keep/drop (batch questions, reuse
  `~/.agent_squeeze/decisions.sqlite` — key near cap; consider waiting for
  the cap reset).

**Blocked:** nothing.

**Awaiting push:** everything since the sprint started (local commits only).
---

## Run 19 (2026-09-23 ~03:45 PDT): `--mapreduce` batch mode for the v2 Jev pruner

**Fold-in from Jev usage research.** Web-research on how people actually use
Jev surfaced the pattern that fixes v2's known 3–15x latency overhead
(sequential Jev calls with rolling state):
- ego-jev (github.com/ZephyrDeng/ego-jev): one System One call per DOM step,
  "two decisions, one network round trip" — Jev answers all questions in
  parallel in ONE request.
- TypeSafe lists **map-reduce over large datasets** as a first-class Jev use
  case; LangChain's `TypeSafeClassifier` batches multiple questions per call.
- Vercel AI SDK 7 `evaluate`, Browser Use jev-ultrafast (7.1 s Flights demo).

**What was built** (`bench/pruners/jev_context_prune.py --mapreduce`):
- `jev_pass_mapreduce()`: asks EVERY unit's noul question (tool-pair windows
  + text turns) in ONE decisions call against a static full-transcript
  skeleton (every turn on one deterministic one-line), replacing the rolling
  decision ledger. Same question texts, same sqlite cache keys, same
  fail-safes (unresolved errors kept verbatim, user turns sacred,
  text-drop never strands an error pair, dropped pairs get outcome notes).
- Refactors to share code: `_later_success` hoisted to module level,
  `_pass2_summarize` extracted (used by both passes).

**Numbers** (deterministic stub judge — no Jev, decision cache and OpenRouter
key untouched):

| check | result |
|---|---|
| synthetic 8-turn fixture (3 tool pairs, 3 judged text turns) | map-reduce: **1 Jev call / 5 questions** vs sequential: **4 calls / 5 questions** |
| decision parity (same stub answers) | identical keep/drop on every unit, identical −74.3% token reduction both modes |
| cache rerun | 0 Jev calls, byte-identical decisions |
| fail-safes | unresolved AssertionError kept verbatim, never reached the judge |
| unit tests | 6/6 new (`test_jev_mapreduce.py`); existing pruners + agent_squeeze tests still green |

**Honest trade-off (documented in code):** an earlier decision no longer
informs a later one within the same pass; compensated by the static
skeleton — every unit's outcome note is visible to every question. Live-Jev
verification (does the static skeleton judge as well as the rolling ledger
in practice?) is pending the OpenRouter cap reset — flagged in code.

**Sources** (Jev usage research):
- github.com/ZephyrDeng/ego-jev — one System One call per DOM step
- firecrawl.dev/blog/what-is-jev — map-reduce, gate-in-front-of-agent
- docs.typesafe.ai/introduction/coding-agents — where Jev fits in a coding-agent loop
- runtimewire.com/article/langchain-adds-jev-decision-model-agent-workflows — batch classification middleware
- lumadock.com/blog/what-is-jev-typesafe — guardrails + calibration notes

**Next (candidate runs):**
- Jev-call benchmark on synthetic_monitoring / admit corpus with the
  `--mapreduce` flag (wait for cap reset; batch questions, reuse
  `~/.agent_squeeze/decisions.sqlite`).
- Live-fire the PostToolUse hook in a real Claude Code session; measure how
  often the model acts on the excerpt vs the raw result.

**Blocked:** live-Jev verification still waits on the OpenRouter key cap reset.

**Awaiting push:** everything since the sprint started (local commits only).

## Run 20 — two-tier chunking in squeeze.py (ROADMAP Phase 3 item)

**What:** error-dense tool results (tracebacks, pytest failures) are now
chunked at 1500 chars instead of 6000; prose tool results keep the 6000-char
chunks. Finer keep/drop granularity where the signal lines are sparse inside
noise (frame lines, repeated log lines), fewer Jev calls where it doesn't
matter. Detection: `_is_error_dense` heuristic (mirrors context.py's
`_looks_like_error`, only fires on results >= 2x the small chunk so short
errors never fragment). Default on, backwards compatible
(`two_tier=True`), CLI flag `--single-tier` to opt out.

**Numbers** (deterministic perfect-judge stub — no Jev, OpenRouter cap untouched):

| mode | reduction | Jev judge calls | needle recall |
|---|---|---|---|
| single-tier (6000) | 47.01% | 5 | 2/2 |
| two-tier (1500/6000) | **66.63%** | 9 | 2/2 |

Reduction delta **+19.62 pts** with recall intact; cost is ~1.8x judge calls
on the error result only (prose path byte-identical in both modes — asserted
in tests). Fail-safe (never empty a tool result) holds in both modes.

**Files:** `agent_squeeze/squeeze.py` (+ERROR_CHUNK_CHARS, +`_is_error_dense`,
`two_tier` param), `agent_squeeze/cli.py` (`--single-tier` flag),
`bench/chunk_tiers/bench_chunk_tiers.py`, `bench/chunk_tiers/test_chunk_tiers.py`
(4/4 pass).

**Next (candidate runs):**
- Re-run the adversarial fixture through two-tier squeeze with the needle
  recall check from `bench/pruners` — confirm no regression on the
  adversarial transcript.
- Fold two-tier into cache-aware path (`squeeze_cache_aware`) for the
  dynamic tail; measure whether cache-hit rate changes.
- Live-Jev A/B when the cap resets: does the finer chunking improve judge
  accuracy on real error text, or just reduce waste?

**Blocked:** live-Jev verification still waits on the OpenRouter key cap reset.

**Awaiting push:** everything since the sprint started (local commits only).

## Run 21 — two-tier regression on all fixtures (Run 20 follow-up)

**What:** answered Run 20's open question — does two-tier chunking regress
evidence recall on the adversarial transcripts? Built
`bench/chunk_tiers/bench_fixtures.py` (runs all 10 `bench/inputs/*.json`
fixtures through `squeeze_transcript` in both modes) and
`test_chunk_tiers_regression.py` (3 tests). Deterministic perfect-evidence
judge (keep chunk iff it contains an evidence string, case-insensitive) —
isolates chunking policy, no Jev, no OpenRouter, cap untouched.

**Numbers** (reduction % single → two, judge calls, recall both modes):

| fixture | tokens | err-dense | single-tier | two-tier | delta | recall |
|---|---|---|---|---|---|---|
| mixed_grind | 28703 | 11 | 0.0% (42) | 26.75% (64) | **+26.75** | 2/2 |
| github_triage | 7788 | 1 | 40.97% (6) | 55.42% (12) | **+14.45** | 4/4 |
| sre_incident | 7942 | 1 | 41.84% (7) | 56.01% (14) | **+14.17** | 3/3 |
| dup_tools | 30011 | 3 | 17.27% (37) | 28.51% (49) | **+11.24** | 2/2 |
| real_task2 | 38069 | 4 | 58.74% (31) | 69.64% (84) | **+10.90** | 2/2 |
| codebase_exploration | 7998 | 0 | 42.22% (6) | 42.22% (6) | +0.00 | 3/3 |
| skill_loads | 28687 | 0 | 39.40% (25) | 39.40% (25) | +0.00 | 2/2 |
| real_task1/3/4 | ~300 ea | 0 | 0.0% | 0.0% | +0.00 | 2/2 |

**Verdict: no regression anywhere.** Recall is 100% in both modes on all
fixtures (no needle lost to finer chunk boundaries); two-tier reduction is
never worse (prose-only fixtures byte-identical, asserted in tests). The
mixed_grind case is the big story: at 6000-char chunks the needles landed in
nearly every chunk (0% reduction); at 1500 chars they localize to fewer
chunks → 26.75%. Trade-off: 1.3–2.7x more judge calls on error-dense
results.

**Files:** `bench/chunk_tiers/bench_fixtures.py`,
`bench/chunk_tiers/test_chunk_tiers_regression.py` (3/3 pass), Run-20's
4/4 chunk-tier tests still green.

**Next (candidate runs):**
- Live-Jev A/B when the OpenRouter cap resets: does finer chunking improve
  real-judge accuracy on error text, or just waste reduction? Re-run this
  fixture bench with the real `jev.score_chunks`.
- Fold two-tier into the cache-aware path (`squeeze_cache_aware`); measure
  cache-hit-rate impact.

**Blocked:** live-Jev verification still waits on the OpenRouter key cap reset.

**Awaiting push:** everything since the sprint started (local commits only).

## Run 22 — two-tier folded into the cache-aware path

**What:** answered Run 21's open question — two-tier chunking now lives in
the cache-aware path, not just the plain path. Added `two_tier=True` to
`squeeze_with_policy` and `squeeze_cache_aware` in `agent_squeeze/cache.py`
(per-tool-message `_is_error_dense` → `ERROR_CHUNK_CHARS`, mirroring
`squeeze.squeeze_transcript`). `server.py` / `mcp_server.py` / `fleet.py`
get the new default (two-tier on) with no changes. Also fixed a
pre-existing broken `test_cache_wiring` (manual argparse `Namespace` was
missing `single_tier`, AttributeError on HEAD too — unrelated to this run).

**Tests:** `test_cache.py` gained 2 two-tier tests (finer chunking
10 → 39 chunks on an error-dense synthetic tail; prefix byte-identical +
needle intact in both modes). All 13 suites green: **86/86**.

**Numbers** (`bench/chunk_tiers/bench_cache_aware_tiers.py` — all 10
`bench/inputs/*.json` fixtures through `squeeze_cache_aware`
(protect_tokens=1024) with the perfect-evidence judge; deltas are reduction
pts single→two, recall both modes, next-turn cost at $3/MTok):

| fixture | err-dense | single | two-tier | delta | recall | next-$ single → two |
|---|---|---|---|---|---|---|
| mixed_grind | 11 | 0.0% | 26.8% | **+26.8** | 2/2 | 0.008611 → 0.059713¹ |
| github_triage | 1 | 41.0% | 55.4% | **+14.5** | 4/4 | 0.013637 → 0.010262 |
| sre_incident | 1 | 41.8% | 56.0% | **+14.2** | 3/3 | 0.013641 → 0.010266 |
| dup_tools | 3 | 17.3% | 28.5% | **+11.2** | 2/2 | 0.074260 → 0.064135 |
| real_task2 | 4 | 58.7% | 69.6% | **+10.9** | 2/2 | 0.046484 → 0.034025 |
| prose-only (5 fixtures) | 0 | — | — | +0.0 | 2/2–3/3 | identical |

¹ mixed_grind next-$ looks higher for two-tier only because single-tier
squeezed 0% — the whole unsqueezed transcript sits in cache at 0.1x. It is a
modeling artifact of "squeeze less → more cache hits", not a regression:
with two-tier, 26.8% fewer real tokens flow into the next call.

**Cache-hit verdict: unaffected by tiering.** The protected prefix is
byte-identical in both modes on every fixture (mixed_grind: 923 protected
tokens identical single and two); only the dynamic tail changes. Stable
prefix bytes = protected prefix either way, so the prompt-cache breakpoint
survives two-tier unchanged. Deltas replicate the plain-path bench (Run 21)
almost exactly, and recall is 100% in both modes on all fixtures.

**Blocked:** live-Jev A/B still waits on the OpenRouter key cap reset.

**Awaiting push:** everything since the sprint started (local commits only).

**Next (candidate runs):**
- Expose `--single-tier` on the cache-aware CLI path (`--protect-prefix`)
  — currently tiering is default-on there with no flag to disable.
- `bench/pruners` adversarial re-check through two-tier squeeze with needle
  recall (Run 21's remaining open item).

## Run 23 — 2026-09-23 04:43 PDT: `--single-tier` wired to the cache-aware + fleet CLI paths

**What:** answered Run 22's open question. Two-tier chunking was
default-on in `squeeze_cache_aware`/`squeeze_fleet` with no CLI off-ramp;
now the existing `--single-tier` flag reaches them:
- `cli.py`: `cmd_squeeze --protect-prefix` branch passes
  `two_tier=not args.single_tier` to `squeeze_cache_aware`; `fleet` parser
  gains `--single-tier`, threaded through `cmd_fleet` into `squeeze_fleet`.
- `fleet.py`: `squeeze_fleet(..., two_tier=True)` — forwarded to
  `squeeze_cache_aware` on the protect path and to `squeeze_transcript`
  on the classic path (which was already default-on, so behavior for the
  plain fleet path is unchanged; the flag now controls both).
- Fixed the two monkeypatch wrappers in `test_fleet.py` that would have
  TypeError'd on the new `two_tier` kwarg; ROADMAP.md two-tier checkbox
  marked done.

**Tests:** 2 new wiring tests — `test_cli_single_tier_propagates`
(squeeze `--protect-prefix --single-tier` → `two_tier=False`, and
`True` without the flag) and `test_fleet_cli_single_tier`
(fleet `--protect-prefix --single-tier` propagation via real
`cli.main()` argv parsing). All 13 suites green: **88/88 test fns**
(86 + 2 new). Deterministic stub policies only — no Jev calls, OpenRouter
cap untouched.

**Blocked:** live-Jev A/B still waits on the OpenRouter key cap reset.

**Awaiting push:** everything since the sprint started (local commits only).

## Run 24 — 2026-09-23 05:05 PDT: chunk-boundary needle split — real regression found, reassembly fix

**What:** answered Runs 20–23's open adversarial item — and found a real bug.
Run 21's regression bench used a perfect evidence judge on line-packed
fixtures, so needles were never split by a chunk boundary and the split
risk was never actually tested. Built
`bench/chunk_tiers/bench_boundary_split.py`: an error-dense tool result
with one 4400-char single-line payload (minified-JSON-style) carrying
`SPLIT_NEEDLE_9ZQ4` straddling the 1500-char hard-split boundary (whole
under single-tier's 6000, split under two-tier) plus a control needle
inside a chunk. Two findings:

1. **Reassembly injected "\n" into hard-split lines (fixed).** Chunks from
   a hard-split long line were re-joined with `"\n".join`, silently
   breaking any string straddling the split — even when both fragments
   were kept, the needle never reappeared verbatim. Fix:
   `chunk_text_breaks()` (new, in `squeeze.py`) marks each chunk's
   trailing break as hard (one long line) or soft (line boundary);
   `reassemble_kept()` joins hard breaks with `""`. Chunk texts are
   byte-identical to the old `chunk_text()`; `admit.py`'s alignment-only
   use is untouched. Threaded through `squeeze_transcript` and
   `cache.squeeze_with_policy` (both reassembly sites).
2. **Fragment-blind judges lose split needles (documented, inherent).**
   A verbatim-string judge (keep iff chunk contains the full needle) drops
   both halves under two-tier — a recall regression vs single-tier that
   no chunker can avoid. A fragment-aware judge (keep on ≥6-char pieces)
   now recovers the needle verbatim post-fix. Noted as a known limit in
   the bench header; the live-Jev question (does real Jev reason about
   fragments?) stays queued for the cap reset.

**Tests:** new `agent_squeeze/test_chunk_breaks.py` (6/6 pass): hard-break
marking, soft breaks on packed lines, long-line and mixed roundtrips,
split-needle verbatim recovery under two-tier, keep-everything roundtrip
byte-identical (modulo the pre-existing trailing-newline drop, asserted
explicitly). `test_admit.py` fails identically on the pristine tree
(relative-import runner quirk, pre-existing per Run 10); everything else
green.

**Numbers** (deterministic judges, zero paid calls — no Jev, decision
cache and OpenRouter key untouched):

| needle@1495 judge | single recall | two-tier recall (before → after fix) |
|---|---|---|
| perfect (verbatim-string) | 2/2 | 0/2 → 0/2 (inherent judge limit, documented) |
| fragment-aware | 2/2 | 1/2 → **2/2** |

needle@1000 control (inside a chunk): 2/2 both tiers, both judges — no
regression from the reassembly change.

**Next (candidate runs):**
- MCP server `squeeze_text`: optional `single_tier` boolean in the tool
  schema (the CLI gap Run 23 fixed still exists there).
- Live-Jev A/B on the boundary-split fixture when the cap resets: does
  real Jev keep needle fragments, or does it need overlap windows at hard
  splits?
- Overlap-window experiment: 100-char overlap on hard splits would let
  fragment-blind judges see the whole needle (cost: ~7% more chunk chars
  on long-line payloads only).

**Blocked:** live-Jev verification still waits on the OpenRouter key cap reset.

**Awaiting push:** everything since the sprint started (local commits only).

---
[END OF PROGRESS — keep appending below this line for future runs]

## Run 25 — 2026-09-23 05:25 PDT: overlap windows on hard splits (Run 24's open experiment)

**What:** answered Run 24's queued overlap-window experiment. New in
`agent_squeeze/squeeze.py`: `OVERLAP_CHARS = 100`,
`chunk_text_breaks_overlap(text, max_chars, overlap_chars)` (every chunk
sitting on a hard split gets the next chunk's first `overlap_chars` chars
appended; soft-break chunks untouched), and `_kept_parts()` — the strip
rule that makes the overlap actually work: a kept chunk's overlap suffix
is stripped **only when its successor chunk is also kept** (the
successor's core then carries those bytes); when the successor is dropped,
the overlap stays, because the judge saw it inside this chunk and kept
this chunk. First attempt stripped unconditionally and re-cut the needle
on reassembly — the strip rule was the real fix. `reassemble_kept` now
accepts 2- or 3-tuples (legacy callers untouched). `squeeze_transcript`
and `cache.squeeze_with_policy` gained `overlap_chars=0` (default off,
byte-identical to legacy). README gained a 3-line chunking note.

**Tests:** new `agent_squeeze/test_chunk_overlap.py` (8/8 pass): overlap
only after hard splits, zero overlap on packed lines / `overlap_chars=0`
== legacy texts, keep-all roundtrips byte-identical, dropped-successor
keeps overlap with no duplication, legacy 2-tuples still accepted,
squeeze + cache paths rescue the straddling needle for the fragment-blind
judge, off-path byte-identical to legacy. Full suite: **102/102 test fns**
green via the PYTHONPATH runner (94 prior + 8 new).

**Numbers** (`bench/chunk_tiers/bench_overlap.py` — Run 24's fixture:
4400-char single-line payload, needle SPLIT_NEEDLE_9ZQ4 swept across the
1500 boundary; deterministic perfect-judge, zero paid calls — no Jev,
decision cache and OpenRouter key untouched):

| needle offset | perfect judge ov=0 | perfect judge ov=100 |
|---|---|---|
| inside chunk 0 (1470, control) | True | True |
| straddles 1500 (1485 / 1495 / 1499) | **False** | **True** |
| at boundary / inside chunk 1 (1500 / 1520, control) | True | True |

Judge-input overhead: +200 chars (+4.3%) on the fixture — only long-line
payloads pay it, packed prose pays nothing. Judge CALL count unchanged.
Keep-all roundtrip through the overlap path: byte-identical (mod the
pre-existing trailing-newline drop). Fragment-aware judge sanity: all
True with ov=100 (at 1485 even the fragment-aware judge needed the
overlap — only 1 needle char lands in the continuation).

**Honest limit:** the overlap window is sized for needles (evidence
strings); a needle longer than 100 chars + boundary distance still splits.
Live-Jev A/B (does real Jev need this, or does it reason about fragments
anyway?) still queued for the cap reset.

**Next (candidate runs):**
- CLI `--overlap-chars` flag (and MCP `squeeze_text` schema boolean) so
  the overlap is reachable outside the library — same gap Run 23 fixed
  for `--single-tier`.
- Live-Jev A/B on the boundary-split fixture when the cap resets:
  overlap on/off with real Jev keep/drop.
- Live-fire the PostToolUse hook in a real Claude Code session; measure
  how often the model acts on the excerpt vs the raw result.

**Blocked:** live-Jev verification still waits on the OpenRouter key cap reset.

**Awaiting push:** everything since the sprint started (local commits only).

## Run 26 — 2026-09-23 05:45 PDT: wire `--overlap-chars` / `single_tier` through CLI + server + MCP

**What:** answered Run 25's queued item — the overlap window was
library-only. Now reachable from every surface:
- `cli.py`: `squeeze` and `fleet` gained `--overlap-chars N`
  (`overlap_chars=0` default = legacy). Threaded into `squeeze_transcript`
  (classic path), `squeeze_cache_aware` (protect-prefix path), and
  `squeeze_fleet` (both pass-2 paths via new `overlap_chars` params on
  `cache.squeeze_cache_aware` and `fleet.squeeze_fleet`).
- `server.py`: `POST /v1/squeeze`, `/v1/squeeze-cache-aware`,
  `/v1/squeeze-fleet` accept `overlap_chars` and `single_tier` (explicit
  `two_tier` wins if both given) — previously these endpoints took only
  `threshold`/`protect_tokens`.
- `mcp_server.py`: `squeeze_transcript` tool schema gained `single_tier`
  (bool) and `overlap_chars` (int); `_tool_squeeze` passes both through
  to `squeeze_cache_aware` (free deterministic policy untouched).
- Fixed the two `test_fleet.py` monkeypatch wrappers (new `overlap_chars`
  kwarg) and the `test_cache_wiring.py` argparse Namespace (new CLI arg);
  README chunking note documents all four surfaces.

**Tests:** new `agent_squeeze/test_overlap_wiring.py` (6/6 pass): flag
propagation on squeeze (0/50/100) + cache-aware + fleet via real argv
parsing; `--single-tier` still reaches `two_tier=False` alongside;
server endpoints accept `overlap_chars`/`single_tier` (asserted at the
library boundary, Jev stubbed); MCP schema lists both new args and
passes them through with cost still $0; **end-to-end through the real
CLI**: fragment-blind judge, straddling needle at offset 1495 — ov=0
loses it, `--overlap-chars 100` rescues it verbatim. Full suite:
**108/108 test fns** green (102 prior + 6 new). Zero paid calls — no Jev,
decision cache and OpenRouter key untouched.

**Blocked:** live-Jev verification still waits on the OpenRouter key cap reset.

**Awaiting push:** everything since the sprint started (local commits only).


## Run 27 — 2026-09-23 05:50 PDT: public benchmark harness (`agent-squeeze bench --all`)

**What:** answered the ROADMAP Phase 4 item "one command that replays the
whole paper". New `agent_squeeze/bench_harness.py`: a registry of 14
benches, each run as a subprocess from the repo root (PYTHONPATH set, works
from any cwd) with per-bench timeout, exit-code status (pass/fail/timeout/
skipped), elapsed seconds, and the last 6 stdout lines as the report.
New `cli bench` subcommand: `--list`, `--all` (default), `--name X`
(repeatable), `--timeout S`, `--include-keyed`, `-o results.json` for CI.
13 offline benches (admit_time, pipeline, research, support_chat,
ttl_tuning, voice_dialogue, 5 chunk_tiers benches, cache_aware, quickstart)
— all deterministic stub judges, zero paid calls. `live_jev_prune`
(bench/pruners/jev_prune.py on sre_incident.json) is registered but
**skipped** unless `--include-keyed` is passed AND OPENROUTER_API_KEY is
set, so an accidental full-matrix run can never burn API budget. README
Quick start now shows the offline bench command; ROADMAP Phase 4 checkbox
marked done.

**Tests:** new `agent_squeeze/test_bench_harness.py` (7/7 pass): registry
scripts exist on disk, names unique, one real bench passes via
`run_bench`, keyed bench skips before execution (bogus script proves
no-execution), timeout status, `bench --list` prints all names, `--name` +
`-o` JSON roundtrips.

**Numbers** (`bench --all`, this VM, zero paid calls — no Jev, decision
cache and OpenRouter key untouched):

| result | count |
|---|---|
| pass | 13/14 benches |
| skipped | 1 (`live_jev_prune`, by design) |
| total wall time | **1.8s** |

Full test suite: 16/17 files green; `test_admit.py` fails identically on
the pristine tree (relative-import runner quirk, pre-existing since Run
10 — not caused by this run). Test fn total now 115 (108 + 7 new).

**Next (candidate runs):**
- Live-Jev A/B on the boundary-split fixture when the cap resets
  (overlap on/off with real Jev keep/drop) — the cap reset also unlocks
  the `live_jev_prune` bench for real.
- Live-fire the PostToolUse hook in a real Claude Code session; measure
  how often the model acts on the excerpt vs the raw result.
- Multi-seed runs (ROADMAP Phase 1): 5 seeds per bench, report mean ± std
  instead of single runs — the harness's JSON output makes this a natural
  next layer.

**Blocked:** live-Jev verification still waits on the OpenRouter key cap reset.

**Awaiting push:** everything since the sprint started (local commits only).

## Run 28 — 2026-09-23 06:05 PDT: tool-definition pruning (ROADMAP Phase 3)

**What:** answered the Phase 3 "tool-definition pruning" item. New module
`agent_squeeze/tooldef.py` + synthetic benchmark `bench/tooldef/run.py` +
7 unit tests (`agent_squeeze/test_tooldef.py`, all pass). Coding agents
carry 50–200 tool definitions (each with a JSON schema) into every turn,
and the list is rarely task-specific — the file-writing agent pays for the
image-generation schema on every call. `prune_tool_definitions(tools,
task, called, policy_fn, store)` decides per tool: `keep` (verbatim, never
rewritten — the admit-gate rule) or `prune` (off-context, held via
`HoldStore`, byte-identical recall by name via `readmit_tool`). Signal
priority: (1) recently-called tools are sacred (the agent already
demonstrated need); (2) task-vocabulary overlap over name + description +
schema property names, with a small explicit alias table for task verbs
("debug"/"fix" → file/read/grep/shell/command/test; "PR" → git/gh/pull —
production Jev reasons this directly; the free judge carries the table);
(3) fail-safe: an empty or signal-free task keeps EVERYTHING (losing a
needed tool's schema is a hard failure; a fat list is only cost). Real-Jev
production shape documented: one noul question per tool ("will the agent
need {name} for this task?") asked in a single map-reduce decisions call —
inject via `policy_fn`.

**Bugs found by the benchmark (fixed this run):** (1) the free judge was
too strict at first — recall 2/8 (only "pytest" matched the task text);
the alias table lifted it to 8/8; (2) the name-fallback used substring
matching, so the "gh" acronym alias false-kept "flight_status" — fallback
removed, word-boundary matching only; (3) stopword "with" counted as task
vocabulary, false-keeping "edit_image" — small STOP set added.

**Numbers** (deterministic free policy, zero paid calls — no Jev, decision
cache and OpenRouter key untouched):

| check | result |
|---|---|
| synthetic 41-tool coding-agent harness ("Debug the failing pytest suite … open a PR") | 41 → 8 tools, 1547 → 318 tokens (−79.4%) |
| needle recall (exec, read_file, write_file, edit_file, grep, git, gh, pytest) | **8/8** |
| pruned definition readmit | byte-identical |
| full Python suite | **122/122** test fns (115 prior + 7 new) |
| `bench --all` | **14/15** pass (live Jev skipped by design), 1.8s |

Also registered `tooldef` in the public bench harness (Run 27's registry),
marked the ROADMAP Phase 3 checkbox done, and documented the module in the
README chunking/tools section.

**Next (candidate runs):**
- Wire `prune_tool_definitions` into the server (`/v1/prune-tools`) and
  MCP server so agents can prune their tool list live at session start.
- Live-Jev A/B when the cap resets: does real Jev beat the alias-table
  judge on recall (needs fewer aliases, catches cross-domain tools)?
- Cross-agent *semantic* dedup (Phase 3, opt-in `--allow-near-dup`).
- Multi-seed runs (ROADMAP Phase 1): 5 seeds per bench, mean ± std.

**Blocked:** live-Jev verification still waits on the OpenRouter key cap reset.

**Awaiting push:** everything since the sprint started (local commits only).

## Run 29 — 2026-09-23 06:15 PDT: wire tool-definition pruning into server + MCP + TS SDK

**What:** the Run 28 tooldef module was library-only — now it is callable
from every surface. New: `POST /v1/prune-tools`
(`{"tools": [...], "task": "...", "called": [...]}` → `{"tools": [kept…],
"ledger": […], "stats": {…}}`) and `POST /v1/readmit-tool` (`{"name": "gh"}`
or `{"ref": …}` → byte-identical definition; 404 on unknown) on the HTTP
service — both use the shared `PersistentHoldStore`, so pruned definitions
persist across requests/restarts like admit holds; `prune_tool_definitions`
+ `readmit_tool` MCP tools in `mcp_server.py` (registry now 6 tools, docs
updated); TS SDK `pruneTools(tools, task, called)` + `readmitTool(name)`
with typed result shapes (`ToolDefinition`, `ToolPruneResult`, …), stub
test now round-trips all 10 endpoints incl. the 404 path. README gained an
HTTP prune/readmit example; `bench/mcp_server/README.md` tool table and the
plugin `SKILL.md` list the new tools.

**Recall bug found by the wiring test (fixed this run):** the deterministic
judge pruned `git` on the task "…open a PR" — one strong signal (task
acronym "PR" → alias "git") wasn't enough under the flat 2-hit threshold.
Fix: `_task_keywords` now returns `(words, strong)` where strong =
uppercase acronyms + their alias-table expansions ("PR"→git/gh/pull); a
single strong hit keeps ("explicit naming, not vocabulary coincidence").
Deliberately narrow — verb aliases ("fix"→"edit") stay weak and still need
2 hits, so the Run 28 precision work (stopword set, word-boundary matching)
is untouched.

**Numbers** (free deterministic policy everywhere, zero paid calls — no
Jev, decision cache and OpenRouter key untouched):

| check | result |
|---|---|
| new `test_tooldef_wiring.py` | 5/5 pass (server prune + readmit roundtrip byte-identical, 400/404 paths, MCP prune/readmit, tool registry = 6) |
| full Python suite | **127/127** test fns (122 prior + 5 new) — caught 1 stale `test_tools_list` assertion (4→6 tools), fixed |
| Run 28 `bench/tooldef` numbers after the strong-signal fix | **unchanged**: 41 → 8 tools, 1547 → 318 tokens (−79.4%), needle recall 8/8 |
| `bench --all` | 14/15 pass (live Jev skipped by design), 1.8s |
| TS SDK `npm test` | tsc strict build + stub round-trip of all 10 endpoints green |

**Next (candidate runs):**
- Live-Jev A/B when the cap resets: does real Jev beat the alias-table
  judge on recall (needs fewer aliases, catches cross-domain tools)?
- Cross-agent *semantic* dedup (Phase 3, opt-in `--allow-near-dup`).
- Multi-seed runs (ROADMAP Phase 1): 5 seeds per bench, mean ± std.
- CLI subcommand for tooldef pruning (`agent-squeeze prune-tools` from a
  tools JSON file) to complete the surface parity.

**Blocked:** live-Jev verification still waits on the OpenRouter key cap reset.

**Awaiting push:** everything since the sprint started (local commits only).
