# agent-squeeze roadmap — the continuous improvement plan

This is the living plan. Every item ends in a benchmark number, not a vibe.

## Phase 1 — Harden the accuracy claim (next)
- [ ] **Real-session replays.** The pi-jev-context project found raw Jev
      pruning dropped 73% of later-needed items on *real* sessions. Replay
      real Claude Code transcripts (with consent) through the pipeline and
      measure needle recall on organic evidence, not planted needles.
- [ ] **LLM-judge answer-quality round.** Same task, original vs compressed
      context, blind judge scores the answers. Recall is necessary, not
      sufficient — this closes the loop.
- [ ] **Multi-seed runs.** 5 seeds per benchmark; report mean ± std instead
      of single runs.

## Phase 2 — Kill the API dependency
- [ ] **kev backend.** jaredpalmer/kev ships Apache-2.0 decision models to 9B
      that trail hosted Jev ~4.5 pts. Add a `--backend kev` path: self-hosted,
      zero per-call cost, works offline. Re-run the full benchmark matrix
      against it and publish the delta honestly.
- [ ] **Threshold re-calibration per backend.** The p ≥ 0.5 story is
      Jev-specific; calibrate each backend on the fixture set.

## Phase 3 — Compress more (without losing accuracy)
- [x] **Two-tier chunks.** Small chunks for error-dense regions, large chunks
      for prose — finer keep/drop granularity where it matters. (done: Runs
      20–22; default-on in squeeze.py/cache.py/fleet.py; `--single-tier`
      disables on all CLI paths incl. `--protect-prefix` and fleet)
- [ ] **Cross-agent *semantic* dedup (opt-in).** Exact-match is the safe
      default; add an explicit `--allow-near-dup` mode with the Headroom
      failure case as a regression test that must stay green.
- [ ] **Tool-definition pruning.** Agents carry 50–200 MCP tool definitions
      every turn; Jev-prune the tool *list* per task (cf. jev-tool-permissions).

## Phase 4 — Distribution (the viral loop)
- [ ] **MCP server + TypeScript SDK.** Meet jev-compactor where the users are;
      same two-line wrap API.
- [ ] **Proactive compaction hook.** Claude Code hook that squeezes at
      60% context instead of waiting for the transcript to be handed over.
- [x] **Public benchmark harness.** One command that replays the whole
      paper: `agent-squeeze bench --all`. Anyone can verify, anyone can beat.
      (done: Run 27 — `agent_squeeze/bench_harness.py` + `cli bench`; 13
      offline benches with deterministic judges, live Jev bench registered
      but skipped unless `--include-keyed`; `-o` writes JSON for CI.)

## Phase 5 — Research bets
- [ ] **Primacy-bias study.** The Laya joint-context finding deserves its own
      writeup: which decision models degrade under joint scoring, and why.
- [ ] **Adaptive thresholds.** Per-transcript calibration from a tiny
      held-out probe set instead of a global 0.5.

---
*Rule: no optimization lands without a benchmark proving recall didn't move.*
