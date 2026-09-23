# cache-aware squeeze — offline benchmark

Deterministic boilerplate policy (dedup + noise), no Jev calls. protect_tokens=1024 (4096 for the synthetic input, so the prefix covers early tool turns). Next-turn cost model: Anthropic ratios (cache read 0.1x, write 1.25x); naive assumed to rewrite the prefix → cache miss every turn. On the three real/adversarial inputs the deterministic policy legitimately drops nothing (their tool outputs are all unique), so those rows just show the structural behavior: no prune → fully byte-stable prefix.

| input | before | naive after | naive −% | aware after | aware −% | stable prefix | next-turn naive $ | next-turn aware $ | saving | 10-turn naive $ | 10-turn aware $ | 10-turn saving |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| sre_incident.json | 7942 | 7942 | 0.0% | 7942 | 0.0% | 7942 | 0.0238 | 0.0024 | 90.0% | 0.2383 | 0.0536 | 77.5% |
| mixed_grind.json | 28703 | 28703 | 0.0% | 28703 | 0.0% | 28703 | 0.0861 | 0.0086 | 90.0% | 0.8611 | 0.1937 | 77.5% |
| github_triage.json | 7788 | 7788 | 0.0% | 7788 | 0.0% | 7788 | 0.0234 | 0.0023 | 90.0% | 0.2336 | 0.0526 | 77.5% |
| synthetic_monitoring.json | 101052 | 86688 | 14.21% | 86940 | 13.97% | 2381 | 0.2601 | 0.2544 | 2.46% | 2.6082 | 2.5528 | 2.12% |

## takeaway

Cache-aware squeeze trades a hair of reduction (the protected prefix is untouchable: 13.97% vs 14.21% naive) for a byte-identical prefix that the provider cache can serve at 0.1x. The win compounds over a session: a cache-breaking prune pays full price on the prefix every turn, while the protected prefix pays one write + cheap reads. Summarizing/rewriting the prefix — the #1 cache-killer per the prompt-caching playbook — maximizes this miss cost. Note the existing squeezer is already half-way there: it never rewrites user/assistant text; cache-aware mode extends the guarantee to tool messages inside the protected zone.
