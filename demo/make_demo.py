"""Simulate 3 Claude Code agents running SIMULTANEOUSLY on one repo.

frontend / backend / infra explore autonomously. Realistic overlap:
every agent Reads package.json + README and runs git log (identical outputs
across agents — the fleet dedup win). Each agent also does unique work.

Planted needles (must survive compression):
  - backend:  config dump whose LAST occurrence alone carries
              version = '3.7.2' and port = 9443   (the exact shape that
              destroyed Headroom's dedup in benchmarks)
  - frontend: the checkout API route  POST /api/v2/checkout
  - infra:    the deploy secret name  DEPLOY_KEY_PROD_7
Writes demo/transcripts.json  ({agent_id: [messages]}) and demo/needles.txt.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

PKG = json.dumps({"name": "shop", "version": "3.7.1",
                  "scripts": {"dev": "next dev", "build": "next build"}})
README = "# shop\n\nMonorepo: web/ api/ infra/.\nRun `make dev` to start.\n"
GITLOG = ("a1b2c3d feat: add cart drawer\n"
          "e4f5g6h fix: null token on charge\n"
          "i7j8k9l chore: bump deps\n")


def tool(name, content):
    return {"role": "tool", "name": name, "content": content}


def build_frontend():
    m = [
        {"role": "user", "name": "", "content":
         "You are the frontend agent. Map the checkout flow UI and report the current checkout API route."},
        {"role": "assistant", "name": "", "content": "[tool call: Read package.json]"},
        tool("Read", PKG),
        {"role": "assistant", "name": "", "content": "[tool call: Read README.md]"},
        tool("Read", README),
        {"role": "assistant", "name": "", "content": "[tool call: Bash git log --oneline -20]"},
        tool("Bash", GITLOG),
        {"role": "assistant", "name": "", "content": "[tool call: Glob web/**/*.tsx]"},
        tool("Glob", "\n".join(f"web/components/{c}.tsx" for c in
                               ["Cart", "CartDrawer", "Checkout", "PayButton",
                                "OrderSummary", "AddressForm"] * 40)),
        {"role": "assistant", "name": "", "content": "[tool call: Grep 'fetch(' web/]"},
        tool("Grep", "\n".join(
            f"web/components/{c}.tsx:{i}: fetch('/api/v1/{e}') // legacy"
            for i, (c, e) in enumerate(
                [("Cart", "cart"), ("PayButton", "pay"), ("OrderSummary", "orders")] * 60))),
        {"role": "assistant", "name": "", "content": "[tool call: Read web/lib/api.ts]"},
        tool("Read", "// api client\n"
             "export const CHECKOUT_ROUTE = 'POST /api/v2/checkout'; // current\n"
             + "// legacy routes below (do not use)\n"
             + "\n".join(f"// const LEGACY_{i} = '/api/v1/old{i}';"
                         for i in range(120))),
    ]
    return m


def build_backend():
    base = "\n".join([f"db.host = db.internal", f"db.pool = 20",
                      f"cache = redis.internal"]
                     + [f"opt_{i} = value_{i}_with_a_longer_setting_string" for i in range(400)])
    cfg_v1 = base
    cfg_v2 = base  # identical re-read (dup 1)
    cfg_v3 = base + "\nversion = '3.7.2'\nport = 9443\n"  # dup 2 — ONLY copy with the needle
    m = [
        {"role": "user", "name": "", "content":
         "You are the backend agent. Find why charges fail on null tokens, and report the service version and port the failing deploy runs on."},
        {"role": "assistant", "name": "", "content": "[tool call: Read package.json]"},
        tool("Read", PKG),
        {"role": "assistant", "name": "", "content": "[tool call: Read README.md]"},
        tool("Read", README),
        {"role": "assistant", "name": "", "content": "[tool call: Bash git log --oneline -20]"},
        tool("Bash", GITLOG),
        {"role": "assistant", "name": "", "content": "[tool call: Bash cat service.conf]"},
        tool("Bash", cfg_v1),
        {"role": "assistant", "name": "", "content": "[tool call: Grep NullPointer api/]"},
        tool("Grep", "\n".join(
            f"api/payments/handler_{i}.py:{10+i}: # no NPE here" for i in range(150))),
        {"role": "assistant", "name": "", "content": "[tool call: Bash cat service.conf]"},
        tool("Bash", cfg_v2),
        {"role": "assistant", "name": "", "content": "[tool call: Bash pytest api/ -x -q]"},
        tool("Bash", "\n".join(f"test_{i} PASSED" for i in range(200))
             + "\n1 failed: test_charge_null_token\n"
             + "NullPointerException: token is None in processor.charge"),
        {"role": "assistant", "name": "", "content": "[tool call: Bash cat service.conf]"},
        tool("Bash", cfg_v3),
    ]
    return m


def build_infra():
    m = [
        {"role": "user", "name": "", "content":
         "You are the infra agent. Audit deploy config and secrets, and report the production deploy key name."},
        {"role": "assistant", "name": "", "content": "[tool call: Read package.json]"},
        tool("Read", PKG),
        {"role": "assistant", "name": "", "content": "[tool call: Read README.md]"},
        tool("Read", README),
        {"role": "assistant", "name": "", "content": "[tool call: Bash git log --oneline -20]"},
        tool("Bash", GITLOG),
        {"role": "assistant", "name": "", "content": "[tool call: Glob infra/**/*]"},
        tool("Glob", "\n".join(f"infra/{p}" for p in
                               ["main.tf", "vars.tf", "outputs.tf", "modules/vpc.tf",
                                "modules/ecs.tf", "scripts/deploy.sh"] * 60)),
        {"role": "assistant", "name": "", "content": "[tool call: Read infra/scripts/deploy.sh]"},
        tool("Read", "#!/bin/bash\nset -euo pipefail\n"
             "# deploys with the prod key\n"
             "echo ${DEPLOY_KEY_PROD_7} | docker login --password-stdin\n"
             + "\n".join(f"# step {i}: echo ok" for i in range(150))),
        {"role": "assistant", "name": "", "content": "[tool call: Bash kubectl get pods]"},
        tool("Bash", "\n".join(f"pod-{i}   Running   3d" for i in range(120))),
    ]
    return m


def main():
    transcripts = {
        "frontend": build_frontend(),
        "backend": build_backend(),
        "infra": build_infra(),
    }
    with open(os.path.join(HERE, "transcripts.json"), "w") as f:
        json.dump({"transcripts": transcripts}, f)
    needles = ["version = '3.7.2'", "port = 9443",
               "POST /api/v2/checkout", "DEPLOY_KEY_PROD_7"]
    with open(os.path.join(HERE, "needles.txt"), "w") as f:
        f.write("\n".join(needles) + "\n")
    toks = sum(len(json.dumps(m)) // 4 for ts in transcripts.values() for m in ts)
    print(f"demo: 3 agents, ~{toks} fleet tokens, {len(needles)} needles")


if __name__ == "__main__":
    main()
