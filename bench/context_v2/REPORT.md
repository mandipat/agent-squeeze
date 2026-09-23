# agent_squeeze v2 — context-aware Jev compression: bench report

## Numbers: v1 vs v2 (evidence recall 2/2 on every input for both)

| input | v1 reduction | v2 reduction | v1 cost | v2 cost | v1 latency | v2 latency |
|---|---|---|---|---|---|---|
| real_task1 (bug fix, 761 tok) | -5.2% | **14.3%** | $0.000027 | $0.000076 | 1.1s | 2.3s |
| real_task2 (exploration, 41k tok) | 58.6% | **68.0%** | $0.001723 | $0.002379 | 0.8s | 10.2s |
| real_task3 (multi-file, 902 tok) | -4.4% | **7.2%** | $0.000025 | $0.000142 | 0.8s | 4.8s |
| real_task4 (pytest, 1058 tok) | -2.8% | **1.4%** | $0.000035 | $0.000112 | 0.8s | 2.3s |
| skill_loads (adversarial 30k) | 41.5% | **91.5%** | $0.000883 | $0.001477 | 0.8s | 12.7s |

v2 beats v1 on all 5 inputs with recall intact. Reruns are free: sqlite cache
(`~/.agent_squeeze/decisions.sqlite`) gave 12/12 hits, $0.00, byte-identical
output on the task2 rerun.

## What v2 is

`agent_squeeze/context.py` parses Anthropic Messages API JSON (and the bench
`user/assistant/tool` format) into **turns** with `tool_use`/`tool_result`
paired by exact id — a tool call + its result is one atomic unit. Thinking
blocks are dropped at parse (conclusions live in text).

`bench/pruners/jev_context_prune.py`, three passes (Headroom-inspired, but
exact-match/LLM-judged only — never near-dup collapsing):

- **Pass 0, deterministic:** only provably-safe replacements — STALE reads
  (file edited after read) and SUPERSEDED reads (same file re-read, no edit
  between) become one-line markers. task1's stale `Read buggy.py` was
  correctly flagged (file edited afterwards; old content was wrong).
- **Pass 1, Jev with rolling state:** walk turns in order; state = task intent
  + ledger of keep/one-liner decisions so far + deterministic one-line
  summaries of what comes *later* (so the judge can see supersession).
  Tool pairs judged per-pair (long outputs windowed inside one batched call,
  keep if any window says keep); assistant text judged per turn. Drops become
  free one-line outcome notes (`[ran `pytest -x`: ok, 42 lines output]`),
  not silent voids. Unresolved errors always kept; user turns never dropped.
- **Pass 2, optional `--summarize`:** gray-zone text turns via one batched
  Aegis haiku call. Default off (see caveats).

## Design incidents (what the bench taught us)

1. **The needle incident.** First v2 cut dropped `skill_loads` recall to 1/2:
   the deterministic pass had a "consumed" rule (old + unreferenced → drop),
   and a planted needle looks exactly like "old and unreferenced". Fix:
   deterministic pass now only does provably-safe ops; every
   question-relevance call goes to Jev. Recall restored to 2/2.
2. **Forward-only blindness.** Rolling state alone kept all 12 task2 pairs
   (0% reduction): the judge couldn't know the final summary turn made the
   raw reads redundant. Fix: state now includes deterministic future-turn
   summaries. task2 went 0% → 68%.
3. **Vague drop framing.** "Answer true to keep, false to drop" kept an 80k
   file read. Fix: the question now names the exact one-line note that
   replaces a drop — "answer true only if the full output, not just the
   note, is required". task2 went 15% → 68%.

## Honest caveats

- **Latency:** v2 is 3–15x slower than v1 (sequential Jev calls; rolling
  state can't be parallelized). Reruns are instant via cache.
- **Cost:** v2 costs ~1.5–5x v1 per run ($0.0001–0.0024 here) — still
  fractions of a cent; the squeezed tokens pay it back immediately.
- **Text-turn drops never fired** on this corpus (0 everywhere): assistant
  chatter turns were all small or recent. The pair one-lining does the work;
  the text-drop path is implemented but unexercised on real data.
- **Aegis summarization not live-tested:** implemented (`--summarize`,
  one batched haiku call) but left off — re-reading a turn to summarize it
  costs ~its own size in input tokens, so it never pays in single-run
  token economics. Available for multi-turn agent loops where re-fetch
  cost dominates.
- **One transient 403** from OpenRouter mid-bench; the fail-safe kept the
  pair and the run completed. Worth watching if it recurs.
- **Stale-read edge:** a needle inside stale content would be replaced —
  correct for the agent (old content is misinformation), but it would read
  as a recall miss on a needle benchmark.
- Heuristics with magic numbers: `PROTECT_RECENT=2`, `MIN_TURN_CHARS=800`,
  `KEEP_P=0.5`/`SUMMARIZE_P=0.3`. Calibrated on this corpus, not proven
  generally.

## Reproduce

```bash
cd bench
python pruners/jev_prune.py inputs/real_task2.json context_v2/outputs/t2_v1.json
python pruners/jev_context_prune.py inputs/real_task2.json context_v2/outputs/t2_v2.json
python score.py context_v2/outputs/
python ../agent_squeeze/test_context.py   # parser unit tests
```

## Files

- `agent_squeeze/context.py` — Messages API / bench-input turn parser
- `agent_squeeze/test_context.py` — unit tests (task1 transcript + synthetic)
- `bench/pruners/jev_context_prune.py` — v2 pruner
- `bench/inputs/real_task1.json` — task1 bench input (new)
- `bench/context_v2/outputs/` — v1/v2 outputs + `summary.json` (on disk;
  gitignored per repo convention — reproducible via the commands above)
