#!/bin/bash
# Reproduce the agent-squeeze benchmark tables end-to-end.
# Requires: python3, OPENROUTER_API_KEY (Jev via OpenRouter).
# Optional: headroom-ai (pip install headroom-ai) for the Headroom column.
set -e
cd "$(dirname "$0")"

echo "== 1. generate inputs =="
python3 build_adversarial.py          # round 2 (deterministic seeds)
# round 1 inputs are committed artifacts (built with Headroom's own generators)

echo "== 2. prune with Jev =="
if [ -z "$OPENROUTER_API_KEY" ]; then
  echo "OPENROUTER_API_KEY not set; skipping Jev prune (key is transient, never stored)"
else
  mkdir -p outputs
  for f in inputs/*.json; do
    id=$(basename "$f" .json)
    echo "-- $id"
    python3 pruners/jev_prune.py "$f" "outputs/${id}_jev.json"
  done
fi

echo "== 3. prune with Headroom (optional) =="
if python3 -c "import headroom" 2>/dev/null; then
  for f in inputs/*.json; do
    id=$(basename "$f" .json)
    python3 pruners/headroom_prune.py "$f" "outputs/${id}_headroom.json"
  done
else
  echo "headroom-ai not installed; skipping (pip install headroom-ai to include)"
fi

echo "== 4. score =="
python3 score.py outputs/
