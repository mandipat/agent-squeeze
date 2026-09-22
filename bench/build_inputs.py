"""Build 3 long realistic agent-transcript inputs for the compression benchmark.

Reuses Headroom's OWN generators from /tmp/headroom-src/benchmarks/real_world_agent_benchmark.py
(generate_log_search, generate_database_query_results, generate_filesystem_search/tree,
 generate_github_code_search, generate_github_issues), then injects deterministic
 evidence needles (exact strings the scorer will look for).

Output: os.path.join(os.path.dirname(os.path.abspath(__file__)), "inputs")/<id>.json with {id, scenario, question,
expected_answer_contains, evidence, messages} where messages are OpenAI chat format.
Target: 6,000-10,000 tokens each (chars/4 estimate).
"""
import json
import os
import random
import sys

sys.path.insert(0, "/tmp/headroom-src/benchmarks")
from real_world_agent_benchmark import (
    seed_everything,
    generate_log_search,
    generate_database_query_results,
    generate_filesystem_search,
    generate_filesystem_tree,
    generate_github_code_search,
    generate_github_issues,
)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "inputs")
os.makedirs(OUT_DIR, exist_ok=True)


def tool_exchange(tool_name, arguments, output_dict, call_id):
    """assistant tool_calls turn + tool result turn."""
    return [
        {
            "role": "assistant",
            "content": f"I'll use {tool_name} to gather data.",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": json.dumps(arguments)},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "name": tool_name,
            "content": json.dumps(output_dict),
        },
    ]


def est_tokens(messages):
    return sum(len(json.dumps(m)) for m in messages) // 4


def save(doc):
    path = os.path.join(OUT_DIR, doc["id"] + ".json")
    with open(path, "w") as f:
        json.dump(doc, f)
    print(f"{doc['id']}: ~{est_tokens(doc['messages'])} tokens, "
          f"{len(doc['evidence'])} evidence strings -> {path}")


# ---------------------------------------------------------------- SRE incident
def build_sre():
    seed_everything(20260922)
    question = ("We're seeing 500 errors on the payment service since 14:32. "
                "What is the root cause?")
    logs = generate_log_search("payment error", num_entries=60)
    # --- evidence needles: injected error rows ---
    needle1 = {
        "timestamp": "2024-01-15T14:32:17Z", "level": "ERROR", "service": "payment-service",
        "message": "OOM killed worker 3 in pod payment-service-2",
        "trace_id": "ev1dence00000001",
        "metadata": {"host": "pod-payment-service-2", "region": "us-east-1"},
    }
    needle2 = {
        "timestamp": "2024-01-15T14:31:55Z", "level": "ERROR", "service": "payment-service",
        "message": "NullPointerException in PaymentProcessor.charge() at PaymentProcessor.java:412",
        "trace_id": "ev1dence00000002",
        "metadata": {"host": "pod-payment-service-2", "region": "us-east-1"},
    }
    logs["result"]["entries"].insert(12, needle1)
    logs["result"]["entries"].insert(13, needle2)

    db = generate_database_query_results(
        "SELECT * FROM service_metrics WHERE service='payment'", num_rows=36)

    fs = generate_filesystem_search("payment", num_results=24)
    fs_needle = {
        "path": "src/services/payment_processor.py", "type": "file", "size": 8421,
        "modified": "2024-01-14",
        "matches": [{
            "line": 412,
            "content": "charge = self.processor.charge(amount)  # raises NullPointerException when token is None",
            "context_before": "    # Charge the customer",
            "context_after": "        return charge.receipt",
        }],
        "score": 0.999,
    }
    fs["result"]["matches"].insert(0, fs_needle)

    messages = [
        {"role": "system", "content": (
            "You are an SRE assistant helping debug production incidents. "
            "You have access to tools for searching logs, querying metrics, and checking deployments. "
            "Analyze the data carefully and identify the root cause.")},
        {"role": "user", "content": question},
    ]
    messages += tool_exchange("search_logs", {"query": "payment error"}, logs, "call_sre_1")
    messages += tool_exchange("database_query",
                              {"query": "SELECT * FROM service_metrics WHERE service='payment'"},
                              db, "call_sre_2")
    messages += tool_exchange("search_files", {"query": "payment"}, fs, "call_sre_3")

    save({
        "id": "sre_incident",
        "scenario": "SRE incident debugging (logs + metrics + file search)",
        "question": question,
        "expected_answer_contains": ["payment-service", "NullPointerException", "OOM", "root cause"],
        "evidence": [
            "OOM killed worker 3 in pod payment-service-2",
            "NullPointerException in PaymentProcessor.charge() at PaymentProcessor.java:412",
            "charge = self.processor.charge(amount)  # raises NullPointerException when token is None",
        ],
        "messages": messages,
    })


# ------------------------------------------------------- codebase exploration
def build_codebase():
    seed_everything(20260923)
    question = ("How is JWT authentication implemented in this codebase? "
                "Trace the request flow from middleware to token verification.")
    tree = generate_filesystem_tree("/project", depth=2, files_per_dir=14)

    fs = generate_filesystem_search("authentication", num_results=31)
    fs_needle1 = {
        "path": "src/auth/jwt_handler.py", "type": "file", "size": 5230,
        "modified": "2024-01-12",
        "matches": [{
            "line": 87,
            "content": "def verify_jwt(token):  # validates signature with RS256 public key",
            "context_before": "    # Verify the JWT signature",
            "context_after": "        return claims",
        }],
        "score": 0.999,
    }
    fs_needle2 = {
        "path": "src/middleware/auth_middleware.py", "type": "file", "size": 3110,
        "modified": "2024-01-11",
        "matches": [{
            "line": 42,
            "content": "user = verify_jwt(token)  # middleware calls jwt_handler before routing",
            "context_before": "    # Authenticate every request",
            "context_after": "        request.user = user",
        }],
        "score": 0.998,
    }
    fs["result"]["matches"].insert(0, fs_needle1)
    fs["result"]["matches"].insert(1, fs_needle2)

    gh = generate_github_code_search("JWT authentication middleware", num_results=19)
    gh_needle = {
        "repository": {"full_name": "myorg/myrepo", "description": "The myrepo project",
                       "stars": 1204, "language": "Python",
                       "updated_at": "2024-01-13T00:00:00Z"},
        "path": "src/auth/jwt_handler.py",
        "sha": "evidence000000000000000000000000000000000001",
        "url": "https://github.com/myorg/myrepo/blob/main/src/auth/jwt_handler.py",
        "score": 99.99,
        "text_matches": [{
            "fragment": "def verify_jwt(token):\n    # RS256 signature verification against JWKS endpoint",
            "matches": [{"text": "verify_jwt", "indices": [4, 14]}],
        }],
    }
    gh["result"]["items"].insert(0, gh_needle)

    messages = [
        {"role": "system", "content": (
            "You are a developer assistant helping explore codebases. "
            "You have access to file system tools and code search. "
            "Help the user understand how the codebase is structured.")},
        {"role": "user", "content": question},
    ]
    messages += tool_exchange("list_directory_tree", {"path": "/project"}, tree, "call_cb_1")
    messages += tool_exchange("search_files", {"query": "authentication"}, fs, "call_cb_2")
    messages += tool_exchange("github_search_code", {"query": "JWT authentication middleware"},
                              gh, "call_cb_3")

    save({
        "id": "codebase_exploration",
        "scenario": "Codebase exploration (file tree + file search + code search)",
        "question": question,
        "expected_answer_contains": ["jwt", "verify_jwt", "middleware", "RS256"],
        "evidence": [
            "def verify_jwt(token):  # validates signature with RS256 public key",
            "user = verify_jwt(token)  # middleware calls jwt_handler before routing",
            "# RS256 signature verification against JWKS endpoint",
        ],
        "messages": messages,
    })


# ---------------------------------------------------------- github triage
def build_triage():
    seed_everything(20260924)
    question = ("Which open issues are P0/critical and should be fixed first? "
                "Cite the issue numbers.")
    issues = generate_github_issues("myorg/myrepo", num_issues=20)
    issue_needle1 = {
        "number": 1042,
        "title": "CRITICAL: data loss in checkout flow when payment retries",
        "state": "open",
        "user": {"login": "oncall-sre", "avatar_url": "https://avatars.githubusercontent.com/u/42"},
        "labels": ["bug", "P0"],
        "created_at": "2024-01-14T09:00:00Z",
        "updated_at": "2024-01-15T10:00:00Z",
        "comments": 23,
        "body": ("## Description\n\nCustomers lose cart contents when a payment retry is issued.\n\n"
                 "## Severity\n\nseverity: P0 - data loss in production checkout\n\n"
                 "## Impact\n\nAffects ~2% of checkout sessions."),
    }
    issue_needle2 = {
        "number": 1057,
        "title": "Security: auth tokens logged in plaintext",
        "state": "open",
        "user": {"login": "sec-team", "avatar_url": "https://avatars.githubusercontent.com/u/77"},
        "labels": ["bug", "security", "P0"],
        "created_at": "2024-01-13T15:30:00Z",
        "updated_at": "2024-01-15T08:00:00Z",
        "comments": 31,
        "body": ("## Description\n\nAuth tokens are written to access logs in plaintext.\n\n"
                 "## Severity\n\nseverity: P0 - credential exposure\n\n"
                 "## Repro\n\nTrigger any authenticated request and inspect the access log."),
    }
    issues["result"]["items"].insert(3, issue_needle1)
    issues["result"]["items"].insert(7, issue_needle2)

    gh = generate_github_code_search("token logging", num_results=15)

    logs = generate_log_search("exception", num_entries=48)
    log_needle = {
        "timestamp": "2024-01-15T11:02:41Z", "level": "ERROR", "service": "auth-service",
        "message": "Plaintext token written to access log for user user_4821",
        "trace_id": "ev1dence00000003",
        "metadata": {"host": "pod-auth-service-1", "region": "us-west-2"},
    }
    logs["result"]["entries"].insert(5, log_needle)

    messages = [
        {"role": "system", "content": (
            "You are a GitHub assistant helping triage issues. "
            "Analyze issues, find patterns, and identify related code.")},
        {"role": "user", "content": question},
    ]
    messages += tool_exchange("github_list_issues", {"repo": "myorg/myrepo"}, issues, "call_gt_1")
    messages += tool_exchange("github_search_code", {"query": "token logging"}, gh, "call_gt_2")
    messages += tool_exchange("search_logs", {"query": "exception"}, logs, "call_gt_3")

    save({
        "id": "github_triage",
        "scenario": "GitHub issue triage (issues + code search + logs)",
        "question": question,
        "expected_answer_contains": ["1042", "1057", "P0", "critical"],
        "evidence": [
            "CRITICAL: data loss in checkout flow when payment retries",
            "Security: auth tokens logged in plaintext",
            "severity: P0 - data loss in production checkout",
            "Plaintext token written to access log for user user_4821",
        ],
        "messages": messages,
    })


if __name__ == "__main__":
    build_sre()
    build_codebase()
    build_triage()
