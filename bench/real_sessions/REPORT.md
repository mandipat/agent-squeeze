# Real-session validation: Aegis → agent_squeeze Jev bench (2026-09-22)

3 real headless Claude sessions captured via the Aegis gateway
(`bedrock/aegis-sonnet`), converted to bench inputs, squeezed with the Jev
pruner, scored. All 4 sessions (incl. task1) now live here.

## Numbers (chars/4 token est; recall = evidence substrings found after squeeze)

| session | scenario | tokens in | tokens out | reduction | recall | Jev cost | Aegis cost | turns |
|---|---|---|---|---|---|---|---|---|
| task2 | repo exploration, read-only | 41,364 | 17,109 | **58.6%** | 2/2 | $0.001723 | $0.695 | 2 |
| task3 | multi-file change (wc.py + counter.py + README) | 902 | 942 | -4.4% | 2/2 | $0.000025 | $0.143 | 5 |
| task4 | pytest writing (statsx.py) | 1,058 | 1,088 | -2.8% | 2/2 | $0.000035 | $0.147 | 4 |
| task1 | bug fix, existing | — | — | — | — | — | $0.16 | 5 |

Negative reduction on task3/task4 is real: on tiny transcripts Jev keeps
every chunk (everything is relevant) and the `[compressed with jev: kept
N/M chunks]` marker adds bytes. The fail-safe never empties a tool message.

Evidence used (2 per session, planted as verbatim transcript substrings,
matched case-insensitively in the compressed blob):
- task2: `KEEP_THRESHOLD = 0.5`, `pass1_markers` — both kept (2/2)
- task3: `def count_words`, `def top_words(text, n)` — both kept (2/2)
- task4: `def test_percentile_p100`, `def test_stddev_two_elements` — both kept (2/2)

## Session notes

- task2: answered all 4 architecture questions with file/line references.
  Needed `--add-dir` to read the repo (cwd permission wall). 47-line
  transcript, 13 tool messages — the only session long enough for real
  compression (58.6%).
- task3: wrote all 3 files; the `python3 wc.py README.md` verification Bash
  call was blocked on user approval. Verified locally instead: CLI runs,
  prints `Total words: 57` + top 5 words. 4/4 chunks kept.
- task4: wrote 18 pytest tests; the `pytest` run was blocked on user approval
  and pytest wasn't installed on the VM. Installed pytest and ran locally:
  **17/18 pass**; `test_stddev_happy` fails on a bad assertion in the
  generated test (`0.138 < 1e-9` — Claude's own assertion bug, not the
  module). 3/3 chunks kept.

## Artifacts

- `to_bench_input.py` — stream-json → bench input converter
  (`--scenario/--question/--evidence/--expected`, repeatable evidence flags)
- `task2|3|4/transcript.jsonl` — raw stream-json transcripts; `stderr.log`
- `../inputs/real_task{2,3,4}.json` — bench inputs
- `../outputs/real_task{2,3,4}_jev.json` — squeezed outputs + stats
- `../outputs/real_task{2,3,4}_jev_score.json` — score summaries
