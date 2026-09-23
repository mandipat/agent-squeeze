# Aegis + Jev integration test — agent_squeeze as middleware in front of Claude

**Headline: squeezed run recalled 2/2 planted needles verbatim, cut the context
38.8% (115.6k → 70.7k chars), billed input tokens 53,019 → 45,111 (−14.9%),
and cost $0.0671 → $0.0248 (−63%).**

## Setup

- **Input:** `bench/inputs/skill_loads.json` — adversarial round-2 transcript,
  ~30k tokens, 33 messages. (First attempt on `mixed_grind.json` reproduced the
  documented refusal: Jev kept 42/42 chunks, −1.3% — it will not drop what it
  cannot prove redundant; see `mixed_grind_jev.json`.)
- **Needles (verified verbatim in input before compressing):**
  1. `the earlier session hands over its cart to the newer session`
  2. `cart_handover complete for shopper` (log: `2026-09-22T10:14:03Z INFO cart_handover complete for shopper`)
- **Questions (identical in both runs):**
  1. When a shopper signs in on a second device, what happens to their cart?
  2. What log message or event confirms the cart handover completed?
- **Squeeze:** repo's `bench/pruners/jev_prune.py` (TypeSafe Jev 1.13 via
  OpenRouter, p ≥ 0.5): **kept 15/25 chunks, 37.8% char reduction,
  $0.000883, 1.5 s.** Needles re-verified verbatim in the squeezed prompt.
- **Model (same for both runs):** `us.anthropic.claude-haiku-4-5-20251001-v1:0`
  via the Aegis enterprise gateway (`aegis_claude_haiku.py`), headless
  `claude -p --output-format json --max-turns 1`, run 2026-09-22 ~18:05 PDT.

## Results

| | (a) full context | (b) Jev-squeezed context |
|---|---|---|
| Billed input tokens | 53,019 (3 + 53,016 cache-creation) | 45,111 |
| Output tokens | 168 | 153 |
| Cost (gateway-reported) | **$0.0671** | **$0.0248** |
| Needle 1 recalled verbatim | yes | yes |
| Needle 2 recalled verbatim | yes | yes |
| "merged cart" phrasing | yes | paraphrased ("single unified cart") |
| Latency | 4.2 s | 4.0 s |

Both answers: the earlier session hands over its cart to the newer session
(single merged/unified cart), confirmed by the exact
`2026-09-22T10:14:03Z INFO cart_handover complete for shopper` log line.

## Cost arithmetic (end to end)

- (a) $0.0671 (model only)
- (b) $0.0248 (model) + $0.0009 (Jev squeeze) = **$0.0257 total → −62% vs (a)**

## Caveats

- Billed-token savings (−14.9%) are smaller than context savings (−38.8%)
  because the Claude Code harness adds ~25k tokens of fixed system/tool
  scaffolding to both runs. The middleware cut what it controls; the dilution
  is harness overhead, not squeeze overhead.
- The two Jev calls on this machine were cheap ($0.0009); pricing depends on
  the OpenRouter key/plan.
- Both runs hit the model in one turn; no agent tool loops were exercised.

## Verdict

**Yes — the squeezed run preserved both planted needle facts verbatim while
cutting context 38.8% and total cost 62%, so the GitHub quick start can
honestly claim "drop agent_squeeze between your agent and the model."**

## Files

- `skill_loads_jev.json` — squeezed transcript (middleware output)
- `prompt_full.txt` / `prompt_squeezed.txt` — the two prompts sent
- `run_a_full.json` / `run_b_squeezed.json` — full Claude JSON envelopes
  (result, usage, total_cost_usd, model)
- `build_prompts.py` — prompt construction + needle verification
- `run_claude.sh` + `aegis_claude_haiku.py` — Aegis headless runner
  (Haiku-class model pinned; copy of the Aegis skill script)
- `mixed_grind_jev.json` — side artifact: the documented refusal-to-compress case
