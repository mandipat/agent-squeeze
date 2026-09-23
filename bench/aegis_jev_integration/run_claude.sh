#!/usr/bin/env bash
# run_claude.sh <prompt_file> <out_json> -- one headless Aegis run, usage captured.
set -e
PROMPT_FILE="$1"; OUT="$2"
DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$DIR/aegis_claude_haiku.py" -p "$(cat "$PROMPT_FILE")" \
  --output-format json --max-turns 1 > "$OUT"
python3 - "$OUT" <<'EOF'
import json,sys
d=json.load(open(sys.argv[1]))
u=d.get("usage",{}) or {}
print("result:", (d.get("result") or "")[:400].replace("\n"," "))
print("input_tokens:", u.get("input_tokens"), "output_tokens:", u.get("output_tokens"))
print("total_cost_usd:", d.get("total_cost_usd"))
print("model:", d.get("modelUsage",{}) and list(d["modelUsage"].keys()))
EOF
