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

