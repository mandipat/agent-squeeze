"""Adversarial heavy transcripts (round 2) — engineered against Headroom's known
weaknesses. Each input is 30k+ tokens, deterministic seeds, OpenAI message format.

Weaknesses targeted (from /tmp/headroom-src deep dive):
- SmartCrusher: on JSON arrays keeps error-keyword rows / statistical outliers /
  change-point windows, drops the rest behind a CCR retrieval sentinel.
- Kompress-base: token scorer with hardcoded must-keep for numbers, ALLCAPS,
  dotted paths, unix paths, extensions, flags, CamelCase, directive words.

Design rule for needles: avoid ALL of the above triggers, keep row lengths
within +/-15% of neighbors, bury mid-array (no change point).
"""
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "inputs")
TARGET_CHARS = 120_000  # ~= 30k tokens at chars/4

FILLER_WORDS = ("ledger settlement batch invoice reconciliation pending retry "
                "upstream downstream handler registry cache index shard replica "
                "snapshot cursor offset watermark checkpoint journal entry debit "
                "credit balance account merchant acquirer issuer token vault "
                "policy quota limit window threshold signal metric trace span "
                "deploy rollout canary region zone cluster node pod container "
                "queue topic partition consumer producer broker schema field "
                "record file block chunk segment extent stripe mirror").split()


def a_call(call_id, tool_name, arguments, text=None):
    return {"role": "assistant",
            "content": text or "I'll use %s to gather data." % tool_name,
            "tool_calls": [{"id": call_id, "type": "function",
                            "function": {"name": tool_name, "arguments": arguments}}]}


def t_result(call_id, tool_name, content):
    return {"role": "tool", "tool_call_id": call_id, "name": tool_name,
            "content": content}


def pad_words(s, target, rng):
    while len(s) < target:
        s += " " + rng.choice(FILLER_WORDS)
    return s


def verify_and_write(doc):
    blob = json.dumps(doc["messages"])
    for e in doc["evidence"]:
        assert e in blob, "evidence missing verbatim: %r" % e[:80]
    toks = len(blob) // 4
    assert toks >= 30000, "only %d tokens, need 30000+" % toks
    path = os.path.join(OUT_DIR, doc["id"] + ".json")
    with open(path, "w") as f:
        json.dump(doc, f)
    print("%s: %d tokens (chars/4), %d evidence strings verified"
          % (doc["id"], toks, len(doc["evidence"])))


# ---------------------------------------------------------------- dup_tools
DUP_SEED = 20260925
SIG_NEEDLE = "def reconcile_ledger(entries, dry_run=True):"
CFG_NEEDLE = "mode = 'reconcile'  # ledger reconciliation mode"

FUNC_NAMES = ["settle_batch", "post_invoice", "match_entries", "close_period",
              "accrue_fees", "net_positions", "sweep_account", "hold_funds",
              "release_hold", "void_entry", "adjust_balance", "fetch_rates",
              "quote_fx", "lock_rate", "expire_quote", "audit_trail",
              "export_ledger", "import_feed", "validate_batch", "sign_payload",
              "verify_sig", "rotate_keys", "purge_cache", "warm_cache",
              "backfill", "replay_journal", "compact_log", "snapshot_db"]

SIG_TMPL = ["def {n}(payload, opts=None):", "def {n}(batch_id, limit=100):",
            "def {n}(ctx, records):", "async def {n}(session, ref):"]
# note: "limit=100" etc. are distractor numbers in non-needle rows (fine)

DOC_TMPL = ("Handles {w1} {w2} for the settlement pipeline; safe to retry and "
            "idempotent across workers.")


def search_row(rng, rank, herring=None):
    if herring == "npe":
        return {"rank": rank, "symbol": "charge_payment", "kind": "function",
                "signature": "def charge_payment(token):",
                "doc": pad_words("Raises NullPointerException when token is "
                                 "None; see recent failures in prod logs.",
                                 150, rng)}
    if herring == "oom":
        return {"level": "ERROR", "service": "payment",
                "message": pad_words("OOM killed worker 7 in pod payment-3; "
                                     "restart loop detected; manual "
                                     "intervention required.", 150, rng)}
    if herring == "panic":
        return {"level": "FATAL",
                "message": pad_words("panic: runtime error: index out of "
                                     "range in ledger_compact(); aborting "
                                     "settlement batch.", 150, rng)}
    name = rng.choice(FUNC_NAMES)
    sig = rng.choice(SIG_TMPL).format(n=name)
    doc = pad_words(DOC_TMPL.format(w1=rng.choice(FILLER_WORDS),
                                    w2=rng.choice(FILLER_WORDS)), 150, rng)
    return {"rank": rank, "symbol": name, "kind": "function",
            "signature": sig, "doc": doc}


def build_dup_tools():
    rng = random.Random(DUP_SEED)
    messages = [
        {"role": "system", "content": "You are a code search assistant helping "
         "reconcile the billing ledger after a database migration. Use the "
         "search and file tools to find the relevant code and configuration."},
        {"role": "user", "content": "We're reconciling the billing ledger "
         "after the migration. What function signature and config value does "
         "the ledger reconciliation use?"},
    ]
    herring_calls = {5: ["npe", "oom"], 11: ["panic", "npe"], 23: ["oom", "panic"]}
    cfg_lines_pool = [
        'timeout = 30  # seconds before giving up on upstream',
        'retries = 5  # attempts before marking the batch failed',
        'workers = 8  # parallel settlement workers',
        'log_level = "info"  # verbosity of service logs',
        'batch_size = 500  # entries per settlement batch',
        'region = "us-west"  # deployment region for workers',
        'snapshot_every = 60  # seconds between ledger snapshots',
        'queue = "settle-q"  # name of the settlement queue',
        'dry_run_default = false  # do not mutate on plain runs',
        'alert_channel = "#ledger-ops"  # where failures are reported',
        'settle_hour = 2  # hour of day for the settlement run',
        'currency = "USD"  # reporting currency for totals',
        'precision = 4  # decimal places for money math',
        'idempotency = true  # dedupe retried settlement posts',
        'ledger_table = "settlements"  # destination table name',
        'feed_url = "https://feed.internal/v1/entries"  # entry source',
        'page_size = 200  # entries fetched per feed page',
        'max_lag = 300  # seconds of acceptable feed lag',
        'notify_on = "mismatch"  # when to page the on-call',
        'archive_after = 90  # days before cold archiving',
        'checksum = true  # verify batch checksums',
        'strict_mode = false  # lenient parsing of old entries',
        'owner = "ledger-team"  # team owning this config',
        'cost_center = "cc-4412"  # billing cost center',
        'sla_hours = 4  # settlement SLA in hours',
        'sample_rate = 10  # percent of batches deep-audited',
        'warmup_secs = 20  # warmup before taking traffic',
        'shutdown_drain = 45  # seconds to drain on shutdown',
        'feature_x = false  # experimental settlement path',
    ]
    for i in range(1, 26):
        cid = "call_dup_%d" % i
        is_read = (i % 2 == 0) or (i == 21)
        if not is_read:
            rows = []
            hs = herring_calls.get(i, [])
            herring_at = {4: 0, 12: 1, 17: 0}  # row number -> index into hs
            for r in range(1, 29):
                h = (hs[herring_at[r] % len(hs)]
                     if (hs and r in herring_at) else None)
                rows.append(search_row(rng, r, h))
            if i == 17:
                rows[6] = {"rank": 7, "symbol": "reconcile_ledger",
                           "kind": "function", "signature": SIG_NEEDLE,
                           "doc": pad_words("Reconciles pending ledger "
                                            "entries against the settlement "
                                            "feed; idempotent.", 150, rng)}
            content = json.dumps({"tool": "search_code",
                                  "query": "ledger reconciliation handler",
                                  "results": rows})
            messages.append(a_call(cid, "search_code",
                                   '{"query": "ledger reconciliation handler"}'))
            messages.append(t_result(cid, "search_code", content))
        else:
            lines = ["# billing service configuration"]
            pool = cfg_lines_pool[:]
            rng.shuffle(pool)
            for ln in pool[:26]:
                lines.append(pad_words(ln, 64, rng))
            if i == 21:
                lines.insert(7, pad_words(CFG_NEEDLE, 64, rng))
            content = json.dumps({"tool": "read_file",
                                  "path": "/repo/billing/billing.conf",
                                  "lines": lines})
            messages.append(a_call(cid, "read_file",
                                   '{"path": "/repo/billing/billing.conf"}'))
            messages.append(t_result(cid, "read_file", content))
    return {"id": "dup_tools", "scenario": "duplicate tool calls",
            "question": "What function signature and config value does the "
                        "ledger reconciliation use?",
            "expected_answer_contains": ["reconcile_ledger", "reconcile",
                                         "ledger"],
            "evidence": [SIG_NEEDLE, CFG_NEEDLE],
            "messages": messages}

# ------------------------------------------------------------ skill_loads
SKILL_SEED = 20260926
PROSE_NEEDLE = ("the earlier session hands over its cart to the newer session")
HANDOVER_NEEDLE = "cart_handover complete for shopper"


def skill_pdf():
    body = []
    body.append("# pdf-extract\n\nExtracts text and tables from PDF documents "
                "for downstream analysis. Handles scanned pages via OCR "
                "fallback when the text layer is missing or incomplete.\n")
    body.append("## When to use\n\nUse this skill when the agent needs to "
                "read invoices, contracts, or reports stored as PDF files. "
                "Do not use it for HTML pages or plain text files.\n")
    body.append("## Parameters\n\n| name | type | required | description |\n"
                "|---|---|---|---|\n"
                "| path | string | yes | Absolute path to the PDF, e.g. "
                "/var/lib/docs/invoice_1042.pdf |\n"
                "| pages | string | no | Page range like 1-5 or 3,7,9. "
                "Defaults to all pages |\n"
                "| dpi | integer | no | Render resolution for OCR, default "
                "300. Higher values cost more memory |\n"
                "| --raw | flag | no | Skip table detection and return plain "
                "text |\n")
    body.append("## Examples\n\n```\nload_skill(name=\"pdf-extract\")\n"
                "extract(path=\"/var/lib/docs/report_Q3.pdf\", pages=\"1-12\", "
                "dpi=300)\n```\n\nBatch mode processes up to 50 documents "
                "per call with a 60 second timeout per file.\n")
    body.append("## Error handling\n\nRaises PdfReadError on corrupt files and "
                "OcrTimeoutError when OCR exceeds 120 seconds. HTTP 429 from "
                "the OCR service means the quota is exhausted; back off for "
                "300 seconds before retrying.\n")
    body.append("## Notes\n\n" + ("Scanned documents with handwritten "
                "annotations need dpi=400 or higher. " * 14) + "\n")
    return "".join(body)


def skill_web():
    body = []
    body.append("# web-search\n\nSearches the public web and returns ranked "
                "results with snippets. Respects robots.txt and applies a "
                "per-domain rate limit.\n")
    body.append("## When to use\n\nUse for current events, documentation "
                "lookups, and anything outside the training cutoff. Prefer "
                "site: queries for official docs.\n")
    body.append("## Parameters\n\n| name | type | required | description |\n"
                "|---|---|---|---|\n"
                "| query | string | yes | The search query string |\n"
                "| --num-results | integer | no | How many hits to return, "
                "default 10, max 50 |\n"
                "| --lang | string | no | Language code such as en or de |\n"
                "| cache_path | string | no | Where to store results, e.g. "
                "/tmp/search_cache.json |\n")
    body.append("## Examples\n\n```\nload_skill(name=\"web-search\")\n"
                "search(query=\"site:docs.example.com settlement API\", "
                "--num-results=25)\n```\n\nResults are cached for 3600 "
                "seconds keyed by the normalized query string.\n")
    body.append("## Error handling\n\nRaises RateLimitError after 100 "
                "requests per hour. SearchBackendError wraps HTTP 500 and "
                "HTTP 503 from https://api.search.example.com/v2/query with "
                "up to 3 retries.\n")
    body.append("## Notes\n\n" + ("Snippet length is capped at 400 characters "
                "per result to control token usage. " * 14) + "\n")
    return "".join(body)


def skill_image():
    body = []
    body.append("# image-resize\n\nResizes and converts product images for "
                "the storefront. Preserves aspect ratio unless --stretch is "
                "passed.\n")
    body.append("## When to use\n\nUse when product photos need thumbnails or "
                "format normalization before upload to the CDN.\n")
    body.append("## Parameters\n\n| name | type | required | description |\n"
                "|---|---|---|---|\n"
                "| src | string | yes | Source image, e.g. "
                "/assets/img/logo.png |\n"
                "| --width | integer | no | Target width in pixels, default "
                "1024 |\n"
                "| --height | integer | no | Target height in pixels |\n"
                "| --format | string | no | Output format: png, jpg, or webp |\n")
    body.append("## Examples\n\n```\nload_skill(name=\"image-resize\")\n"
                "resize(src=\"/assets/img/hero.jpg\", --width=1920, "
                "--format=webp)\n```\n\nEXIF orientation is applied before "
                "resizing so phone photos come out upright.\n")
    body.append("## Error handling\n\nRaises UnsupportedFormatError for TIFF "
                "and BMP inputs. Corrupt JPEGs raise DecodeError after 2 "
                "attempts.\n")
    body.append("## Notes\n\n" + ("WebP output at quality 80 is roughly 30 "
                "percent smaller than JPEG. " * 16) + "\n")
    return "".join(body)


def skill_session():
    body = []
    body.append("# session-store\n\nServer-side session and cart persistence "
                "layer. Backed by Redis with a Postgres write-through log for "
                "durability.\n")
    body.append("## When to use\n\nUse for anything involving shopper "
                "sessions, carts, or checkout state across devices.\n")
    body.append("## Parameters\n\n| name | type | required | description |\n"
                "|---|---|---|---|\n"
                "| session_id | string | yes | The session identifier |\n"
                "| --ttl | integer | no | Session lifetime in seconds, "
                "default 3600 |\n"
                "| store_path | string | no | Snapshot directory, e.g. "
                "/var/lib/sessions |\n")
    body.append("## Session continuity\n\nSession continuity: when a shopper "
                "signs in on a second device, " + PROSE_NEEDLE + ". The "
                "handover preserves cart contents and the shopper sees a "
                "single merged cart.\n")
    body.append("## Examples\n\n```\nload_skill(name=\"session-store\")\n"
                "get_cart(session_id=\"sess_9f31\", --ttl=7200)\n```\n\n"
                "Snapshots are written to /var/lib/sessions every 300 "
                "seconds.\n")
    body.append("## Error handling\n\nRaises SessionExpiredError when the TTL "
                "elapses and StoreUnavailableError on Redis failover. HTTP "
                "503 from the session API triggers 3 retries.\n")
    body.append("## Notes\n\n" + ("Cross-device handover is logged at INFO "
                "level with the cart_handover event name. " * 12) + "\n")
    return "".join(body)


def big_search_results(rng, topic, n, size_target):
    out = {"tool": "web_search", "query": topic, "results": []}
    while len(json.dumps(out)) < size_target:
        out["results"].append({
            "title": pad_words("Result about %s" % topic, 60, rng),
            "url": "https://docs.example.com/%s/%d" % (topic, rng.randint(1, 999)),
            "snippet": pad_words("Snippet text describing %s in detail."
                                 % topic, 320, rng)})
        if len(out["results"]) > n + 40:
            break
    return json.dumps(out)


def big_pdf_text(rng, size_target):
    paras = []
    while len(" ".join(paras)) < size_target:
        paras.append(pad_words("Extracted paragraph from the quarterly "
                               "settlement report.", 400, rng))
    return json.dumps({"tool": "pdf_extract", "pages": 12,
                       "text": "\n\n".join(paras)})


def build_skill_loads():
    rng = random.Random(SKILL_SEED)
    messages = [
        {"role": "system", "content": "You are a coding assistant with access "
         "to skill documentation. Load the skills you need, then do the task."},
        {"role": "user", "content": "Build a checkout analytics dashboard. "
         "Load the skills you need first."},
    ]
    skills = [("pdf-extract", skill_pdf()), ("web-search", skill_web()),
              ("image-resize", skill_image()), ("session-store", skill_session())]
    for idx, (name, doc) in enumerate(skills, 1):
        cid = "call_skill_%d" % idx
        messages.append(a_call(cid, "load_skill", '{"name": "%s"}' % name,
                               "I'll load the %s skill." % name))
        messages.append(t_result(cid, "load_skill", doc))
    # pre-pivot busywork: heavy tool use with the stale skills
    jobs = [("web_search", big_search_results(rng, "checkout funnel", 0, 13500)),
            ("pdf_extract", big_pdf_text(rng, 10500)),
            ("web_search", big_search_results(rng, "dashboard metrics", 0, 10500)),
            ("pdf_extract", big_pdf_text(rng, 10500)),
            ("web_search", big_search_results(rng, "analytics KPI", 0, 10500)),
            ("pdf_extract", big_pdf_text(rng, 10500)),
            ("web_search", big_search_results(rng, "conversion rate", 0, 10500)),
            ("pdf_extract", big_pdf_text(rng, 10500)),
            ("web_search", big_search_results(rng, "revenue chart", 0, 10500))]
    for j, (tool, content) in enumerate(jobs, 1):
        cid = "call_pre_%d" % j
        messages.append(a_call(cid, tool, '{"q": "dashboard"}'))
        messages.append(t_result(cid, tool, content))
    # THE PIVOT: dashboard work is now irrelevant
    messages.append({"role": "user", "content": "Actually, plans changed — "
                     "forget the dashboard. Shoppers report their carts "
                     "vanish when they sign in on a second device. What "
                     "happens to the cart in that case?"})
    lines = []
    for k in range(40):
        if k == 23:
            lines.append("2026-09-22T10:14:03Z INFO " + HANDOVER_NEEDLE +
                         " after second-device sign-in")
        else:
            lines.append("2026-09-22T10:%02d:%02dZ INFO session heartbeat "
                         "ok for shopper region us-west" % (rng.randint(0, 13),
                                                            rng.randint(0, 59)))
    messages.append(a_call("call_post_1", "grep_logs",
                           '{"pattern": "cart"}',
                           "I'll search the logs for cart handover events."))
    messages.append(t_result("call_post_1", "grep_logs",
                             json.dumps({"tool": "grep_logs",
                                         "pattern": "cart",
                                         "matches": lines})))
    desc = {"tool": "describe_session",
            "session": "sess_9f31",
            "state": pad_words("Session metadata for the affected shopper.",
                               3000, rng)}
    messages.append(a_call("call_post_2", "describe_session",
                           '{"session": "sess_9f31"}'))
    messages.append(t_result("call_post_2", "describe_session",
                             json.dumps(desc)))
    return {"id": "skill_loads", "scenario": "stale skill loads",
            "question": "When a shopper signs in on a second device, what "
                        "happens to their cart?",
            "expected_answer_contains": ["hands over", "merged cart",
                                         "cart_handover"],
            "evidence": [PROSE_NEEDLE, HANDOVER_NEEDLE],
            "messages": messages}

# ------------------------------------------------------------ mixed_grind
GRIND_SEED = 20260927
VER_NEEDLE = "version = '3.7.2'"
PORT_NEEDLE = 'port = 9443'

CONF_SHARED = [
    'host = "staging-04"  # staging host for this service',
    'workers = 8  # parallel request workers',
    'timeout = 30  # seconds before giving up on upstream',
    'retries = 5  # attempts before marking the batch failed',
    'log_level = "info"  # verbosity of service logs',
    'queue = "deploy-q"  # name of the deployment queue',
    'region = "us-west"  # deployment region for workers',
    'snapshot_every = 60  # seconds between state snapshots',
    'max_conns = 256  # max upstream connections',
    'backoff = 2  # seconds between deploy retries',
    'health_path = "/healthz"  # liveness probe endpoint',
    'metrics_port = 9090  # prometheus metrics listener',
    'tls = true  # terminate TLS at the edge',
    'compress = true  # gzip responses over 1KB',
    'cache_ttl = 120  # seconds for config cache entries',
    'grace_period = 15  # seconds for drain on shutdown',
    'log_retention = 7  # days of local log retention',
    'alert_channel = "#staging-ops"  # where failures are reported',
]


def traceback_block(rng, kind):
    frames = []
    for f in range(rng.randint(14, 22)):
        frames.append('  File "/opt/svc/%s.py", line %d, in %s\n    %s' %
                      (rng.choice(["deploy", "config", "health", "migrate",
                                   "rollback", "probe"]),
                       rng.randint(10, 400),
                       rng.choice(["run", "main", "check", "apply", "wait"]),
                       pad_words(rng.choice(["result = client.call(payload)",
                                             "state = store.load(key)",
                                             "resp = http.post(url, data)",
                                             "lock.acquire(timeout=30)"]),
                                 60, rng)))
    if kind == "npe":
        head = ("Traceback (most recent call last):\n" +
                "".join(frames) +
                "NullPointerException: Cannot invoke method on null object "
                "in deploy pipeline\n")
    elif kind == "oom":
        head = ("FATAL: OOM killed process 4412 (deploy.sh) score 987; "
                "out of memory in staging-04\n" +
                "".join("  [kernel] %s\n" % pad_words("memory cgroup event",
                                                      50, rng)
                        for _ in range(rng.randint(10, 16))))
    elif kind == "panic":
        head = ("panic: runtime error: index out of range [7] with length 7\n"
                "goroutine 41 [running]:\n" +
                "".join("  svc/%s.go:%d\n" % (rng.choice(["deploy", "probe"]),
                                              rng.randint(10, 300))
                        for _ in range(rng.randint(12, 18))))
    else:
        head = ("ERROR: deploy failed after %d attempts: connection refused "
                "to registry.internal:5000\n" % rng.randint(2, 9) +
                "".join(frames) +
                "CRITICAL: rollback aborted; manual intervention required\n")
    return pad_words(head, 4200, rng)


def build_mixed_grind():
    rng = random.Random(GRIND_SEED)
    messages = [
        {"role": "system", "content": "You are a debugging assistant helping "
         "diagnose a broken staging deploy. Use the shell and file tools."},
        {"role": "user", "content": "The staging deploy is broken. What "
         "version got deployed and on which port is the service listening?"},
    ]
    n = [0]

    def call(tool, args, content):
        n[0] += 1
        cid = "call_grind_%d" % n[0]
        messages.append(a_call(cid, tool, args))
        messages.append(t_result(cid, tool, content))

    # initial probing
    call("bash", '{"cmd": "ls /opt/svc"}',
         json.dumps({"tool": "bash", "cmd": "ls /opt/svc",
                     "stdout": "deploy.sh\nconfig/\nlogs/\nhealthcheck.py\n"}))
    call("bash", '{"cmd": "cat README"}',
         json.dumps({"tool": "bash", "cmd": "cat README",
                     "stdout": pad_words("Staging deploy runbook.", 800, rng)}))
    # config dumps: #8 and #19 identical, #33 adds the answer lines
    conf_ids = []
    for label in ("8", "19", "33"):
        lines = [pad_words(ln, 58, rng) for ln in CONF_SHARED]
        if label == "33":
            lines.append(VER_NEEDLE)
            lines.append(PORT_NEEDLE)
        body = json.dumps({"tool": "bash", "cmd": "cat service.conf",
                           "stdout": "\n".join(lines)})
        n[0] += 1
        cid = "call_grind_%d" % n[0]
        conf_ids.append(n[0])
        messages.append(a_call(cid, "bash", '{"cmd": "cat service.conf"}',
                               "I'll check the service config."))
        messages.append(t_result(cid, "bash", body))
    # many failing calls with scary tracebacks (red herrings)
    kinds = ["npe", "oom", "panic", "conn"]
    for k in range(15):
        kind = kinds[k % 4]
        call("bash", '{"cmd": "./deploy.sh --check"}',
             json.dumps({"tool": "bash", "cmd": "./deploy.sh --check",
                         "exit_code": 1,
                         "stderr": traceback_block(rng, kind)}))
    # near-duplicate successful health checks (dedup bait)
    for _ in range(4):
        call("bash", '{"cmd": "curl localhost:8080/health"}',
             json.dumps({"tool": "bash", "cmd": "curl localhost:8080/health",
                         "stdout": '{"status": "ok"}'}))
    # misc successful reads/greps
    for g in range(12):
        rows = [{"file": "svc/deploy.log", "line": rng.randint(1, 900),
                 "text": pad_words("routine deploy log line", 120, rng)}
                for _ in range(14)]
        call("grep", '{"pattern": "deploy"}',
             json.dumps({"tool": "grep", "pattern": "deploy",
                         "matches": rows}))
    for r in range(6):
        call("read", '{"path": "/opt/svc/logs/app.log"}',
             json.dumps({"tool": "read", "path": "/opt/svc/logs/app.log",
                         "content": pad_words("Application log excerpt.",
                                              2200, rng)}))
    return {"id": "mixed_grind", "scenario": "mixed debugging grind",
            "question": "What version got deployed and on which port is the "
                        "service listening?",
            "expected_answer_contains": ["3.7.2", "9443", "version", "port"],
            "evidence": [VER_NEEDLE, PORT_NEEDLE],
            "messages": messages}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for builder in (build_dup_tools, build_skill_loads, build_mixed_grind):
        verify_and_write(builder())


if __name__ == "__main__":
    main()
