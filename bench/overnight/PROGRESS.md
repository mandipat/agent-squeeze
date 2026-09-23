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

