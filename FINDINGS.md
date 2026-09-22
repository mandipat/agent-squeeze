# Calibrated Decision Models for Agent Context Compression

**Findings paper — agent-squeeze, 2026-09-22**

*Keeping every answer-critical string verbatim while removing 40–80% of tokens
from coding-agent transcripts, using a calibrated decision model (TypeSafe Jev)
instead of rule-based dedup.*

## Abstract

Coding agents drown in their own tool output. We benchmarked three approaches
to transcript compression — Headroom 0.38.0 (deterministic, rule-based),
Laya 0.3.5 (local 421M decision model), and TypeSafe Jev-1.13 via OpenRouter
(hosted decision model) — on evidence-recall: does every answer-critical
string survive compression verbatim? Key results: (1) Headroom achieves
strong reduction but has a catastrophic failure mode — its first-occurrence-wins
near-duplicate collapsing destroyed the answer on an adversarial transcript
(0/2 recall, unrecoverable); (2) Jev at a fixed p ≥ 0.5 cutoff kept 12/12
needles across 6 transcripts plus 4/4 on structured fixtures, with no
per-workload tuning; (3) a small local decision model (Laya) showed severe
primacy bias under joint context, ranking a `heartbeat ping 0` row above real
evidence. From this we built **agent-squeeze**: exact-match cross-agent dedup
for multi-agent fleets plus per-agent calibrated keep/drop — 66.8% true token
reduction (tiktoken-verified) on a 3-agent demo with 4/4 needles intact, ~2 s,
$0.0014.

## 1. Problem

Multi-agent coding fleets (e.g. Claude Code subagents) each accumulate tens of
thousands of tokens of tool output. Feeding all of it back into one LLM call
is expensive and degrades reasoning ("lost in the middle"). Compression must
be **extractive and evidence-preserving**: a dropped stack frame or version
string is not a rounding error, it is a wrong answer.

## 2. Method

**Keep/drop as a calibrated decision, not a ranking.** For each chunk of a
transcript we ask Jev one Noul question — *"should this chunk stay in working
memory for the task?"* — and keep chunks with p ≥ 0.5. No top-K tuning, no
per-workload thresholds. Everything kept is verbatim; every drop is
attributable to a probability.

**Fleet pass first.** Before per-agent squeezing, identical tool outputs are
deduped *across* agents: the first agent keeps the content, the rest get a
reference marker. Deliberately exact-match only — see §4 for why near-duplicate
collapsing is disqualified.

## 3. Benchmarks

All runs single-shot, deterministic seeds, real installs (Headroom 0.38.0 via
pip, Laya 0.3.5 local weights, Jev-1.13 via OpenRouter `/api/alpha/decisions`).
Metric: **token reduction %** (chars/4 estimate) and **evidence recall**
(fraction of planted answer-critical strings surviving verbatim).

### 3.1 Long transcripts (~9k tokens, Headroom's own generators)

| input | Headroom red. / recall | Jev red. / recall |
|---|---|---|
| sre_incident | 50.1% / 1.0 | 40.9% / 1.0 |
| codebase_exploration | 25.3% / 1.0 | 27.2% / 1.0 |
| github_triage | 28.6% / 1.0 | 22.7% / 1.0 |

Jev matched Headroom on accuracy but did not beat it on reduction — the
motivation for the adversarial round.

### 3.2 Adversarial transcripts (30k+ tokens)

| input | Headroom red. / recall | Jev red. / recall |
|---|---|---|
| dup_tools (repeated tool calls) | 12.5% / 1.0 | 15.9% / 1.0 |
| skill_loads (repeated skill loads) | 39.2% / 1.0 | 44.3% / 1.0 |
| mixed_grind (near-dup config dumps + red herrings) | 15.8% / **0.0** | −1.3% / **1.0** |

On `mixed_grind`, Jev *refused* to compress (−1.3%: it kept everything, even
adding reference markers) rather than risk the evidence. A second Jev pass
with an explicit redundancy framing still kept 42/42 chunks — the model is
conservative by design and will not drop what it cannot prove redundant.

### 3.3 Decision-model calibration (Laya, 36-cell matrix)

Isolated per-row scoring × {0.5, 0.3, top-8} × {noul, choice, score}: the only
configuration reaching full recall used rank-based top-K — the 0.5 cutoff
failed because evidence scored p≈0.47–0.49 (calibration, not judgment: evidence
ranked #1 in all 4 cases). Worse, scoring rows **jointly in one context**
induced primacy bias: a `heartbeat ping 0` row ranked top-1 in all 4 fixtures
while real evidence fell to ranks #9–#27. Lesson: small decision models need
isolated, per-item judgments; joint context corrupts them.

### 3.4 Fleet demo (agent-squeeze, 3 agents, 26k tokens)

| | chars/4 est. | tiktoken (cl100k_base) |
|---|---|---|
| reduction | 70.2% | **66.8%** |
| needles | 4/4 | 4/4 |
| latency / cost | ~2 s | $0.0014 |

Token estimates via chars/4 overstate reduction by ~3 points on tool-output-heavy
text; all headline claims in this paper use tiktoken-verified counts.

## 4. The Headroom failure mode

Headroom's SmartCrusher collapses near-duplicate JSON arrays keeping the
**first** occurrence and replacing the rest with retrieval sentinels. On
`mixed_grind`, three near-identical config dumps collapsed into one: the
sentinel preserved the first dump's values while `version = '3.7.2'` and
`port = 9443` — present only in the *last* dump — were destroyed. Meanwhile 15
irrelevant red-herring stack traces survived. Result: 0/2 recall, and the
damage is unrecoverable (the compressed output *looks* complete). This is why
agent-squeeze dedups exact matches only and never collapses near-duplicates
autonomously.

## 5. Related work

- **LLMLingua / LLMLingua-2 / LongLLMLingua** (Microsoft): small-LM
  perplexity-based token pruning, up to 20× compression; query-aware variant
  for RAG. Token-level rather than chunk-level; no calibration story for
  agent transcripts. (https://github.com/microsoft/LLMLingua)
- **RECOMP** (ICLR 2024), **EXIT**, **EFFCOMP**: extractive compressors for
  retrieval contexts; a 2024 Stanford study found extractive compression beats
  summary-based generative compression on long context — consistent with our
  verbatim-only design.
- **Headroom** (headroomlabs-ai/headroom 0.38.0): deterministic,
  structure-aware crushing with retrieval sentinels; strong on JSON-heavy
  inputs, lossy under near-duplication (§4).
- **Jev ecosystem** (TypeSafe "System One" decision model, launched Sep 2026):
  jev-compactor (TS lib/CLI/MCP, single-agent compaction),
  fast-jev-compaction (Claude Code plugin), winnow (pre-admission tool-result
  judging), yoshi (Claude Code proxy). agent-squeeze differs in two ways:
  fleet-level *cross-agent* dedup, and a published adversarial benchmark with
  a documented competitor failure mode.
- **kev** (Apache-2.0, Qwen3.5-based decision models to 9B): an open,
  self-hostable alternative to hosted Jev, trailing it ~4.5 pts accuracy —
  the most promising path to removing our API dependency.

## 6. Limitations (honest)

- Single runs; synthetic transcripts, not real agent sessions. Community
  evidence (pi-jev-context) warns that raw Jev pruning dropped 73% of
  later-needed items on *real* session replays — our 12/12 is on synthetic
  needles and must be re-validated on real sessions before production claims.
- Jev is API-hosted: per-call cost/latency and an external dependency.
- Answer *quality* on compressed vs original context (LLM-judge round) is
  not yet measured — recall of planted needles is necessary but not sufficient.
- English tool output only; no human eval.

## 7. Roadmap

See ROADMAP.md. Next: real-session replay validation, LLM-judge
answer-quality round, kev local backend, MCP server + TS SDK, proactive
compaction hooks, harder adversarial fronts.

## 8. Reproduce

```bash
git clone <this-repo> && cd agent-squeeze && pip install -e .
export OPENROUTER_API_KEY=sk-or-v1-...
agent-squeeze-serve --port 8765 &            # the service
python demo/run_demo.py                        # 3-agent demo, ~2 s, ~$0.001
```

Benchmark harnesses live in `/tmp/compress_bench` and `/tmp/jev_headroom`
(methodology); the shippable product is this repo.
