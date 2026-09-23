"""Tests for the public benchmark harness (bench_harness.py + `cli bench`).

Run: PYTHONPATH=. python3 agent_squeeze/test_bench_harness.py
No Jev, no paid calls — only the registry and fast offline benches.
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent_squeeze import bench_harness as bh  # noqa: E402
from agent_squeeze import cli  # noqa: E402


def test_registry_scripts_exist():
    for name, (script, _argv, _keyed) in bh.BENCHES.items():
        path = os.path.join(bh.REPO_ROOT, script)
        assert os.path.isfile(path), f"bench {name}: missing {path}"


def test_registry_names_unique():
    names = bh.list_benches()
    assert len(names) == len(set(names))
    assert "live_jev_prune" in names  # the only keyed bench


def test_run_one_offline_bench_passes():
    r = bh.run_bench("ttl_tuning", timeout_s=120)
    assert r["status"] == "pass", r
    assert r["seconds"] >= 0
    assert r["tail"], "expected captured stdout tail"


def test_keyed_bench_skipped_without_opt_in():
    # Bogus script proves the skip happens BEFORE any execution attempt.
    entry = ("definitely/not/here.py", [], True)
    saved = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        r = bh.run_bench("bogus_keyed", entry=entry, include_keyed=False)
        assert r["status"] == "skipped", r
        assert "include-keyed" in r["reason"]
        # Even with opt-in, a missing key skips instead of running.
        r2 = bh.run_bench("bogus_keyed", entry=entry, include_keyed=True)
        assert r2["status"] == "skipped", r2
        assert "OPENROUTER_API_KEY" in r2["reason"]
    finally:
        if saved is not None:
            os.environ["OPENROUTER_API_KEY"] = saved
        else:
            os.environ.pop("OPENROUTER_API_KEY", None)


def test_timeout_status():
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write("import time; time.sleep(30)\n")
        path = f.name
    try:
        r = bh.run_bench("sleeper", entry=(path, [], False), timeout_s=1)
        assert r["status"] == "timeout", r
    finally:
        os.unlink(path)


def _argv_ns(**kw):
    class NS:  # argparse-like namespace for cmd_bench
        pass
    ns = NS()
    ns.list = kw.get("list", False)
    ns.all = kw.get("all", False)
    ns.name = kw.get("name")
    ns.include_keyed = kw.get("include_keyed", False)
    ns.timeout = kw.get("timeout", 120)
    ns.output = kw.get("output")
    return ns


def test_cli_bench_list_prints_all_names():
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_bench(_argv_ns(list=True))
    out = buf.getvalue()
    for name in bh.list_benches():
        assert name in out, f"missing {name} in --list output"


def test_cli_bench_single_name_and_json_output():
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        out_path = f.name
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.cmd_bench(_argv_ns(name=["ttl_tuning"], output=out_path, timeout=120))
        text = buf.getvalue()
        assert "pass" in text and "ttl_tuning" in text
        assert "1/1 benches passed" in text
        data = json.load(open(out_path))
        assert data["summary"]["passed"] == 1
        assert data["results"][0]["name"] == "ttl_tuning"
    finally:
        os.unlink(out_path)


if __name__ == "__main__":
    test_registry_scripts_exist()
    test_registry_names_unique()
    test_run_one_offline_bench_passes()
    test_keyed_bench_skipped_without_opt_in()
    test_timeout_status()
    test_cli_bench_list_prints_all_names()
    test_cli_bench_single_name_and_json_output()
    print("all bench-harness tests passed")
