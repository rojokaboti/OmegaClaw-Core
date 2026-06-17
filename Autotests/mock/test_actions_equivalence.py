"""Seam-equivalence check for the mock ``act()`` helper (Issue #1).

Proves that JSON produced by ``act(...)`` renders, via the strict-JSON action
protocol, to *exactly* the same MeTTa s-expression that the legacy
``helper.balance_parentheses`` produced for the equivalent loose-text command.

Because the agent's downstream behavior depends only on that rendered s-expr,
identical rendering means a converted mock fixture drives the agent identically
to its original legacy form -- verifiable on the host without Docker.

Runs under pytest in CI and standalone: ``python3 test_actions_equivalence.py``.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_HERE, _SRC, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import action_protocol as ap  # noqa: E402
from helper import balance_parentheses  # noqa: E402
from actions import act  # noqa: E402

# (act-spec, legacy-text-equivalent) pairs covering every tool used by mocks.
CASES = [
    ((("send", "Hello"),), "send Hello"),
    ((("send", "Here are the planets:\n1. Mercury\n2. Venus"),),
     "send Here are the planets:\n1. Mercury\n2. Venus"),
    ((("query", "city food shortage"),), "query city food shortage"),
    ((("search", "weather Berlin"),), "search weather Berlin"),
    ((("pin", "task done"),), "pin task done"),
    ((("remember", "user likes brevity"),), "remember user likes brevity"),
    ((("shell", "mkdir -p /tmp/testcat"),), "shell mkdir -p /tmp/testcat"),
    ((("read-file", "/tmp/a.txt"),), "read-file /tmp/a.txt"),
    ((("tavily-search", "btc price"),), "tavily-search btc price"),
    ((("technical-analysis", "AAPL"),), "technical-analysis AAPL"),
    ((("episodes", "2026-06-16 12:00:00"),), "episodes 2026-06-16 12:00:00"),
    ((("metta", "(+ 1 2)"),), "metta (+ 1 2)"),
    ((("write-file", "/tmp/x/a.txt", "data here"),), "write-file /tmp/x/a.txt data here"),
    ((("append-file", "/tmp/log.txt", "a line"),), "append-file /tmp/log.txt a line"),
    # multi-action sequence
    ((("shell", "mkdir -p /tmp/testcat"), ("write-file", "/tmp/testcat/hello.txt", "Hello")),
     'shell mkdir -p /tmp/testcat\nwrite-file /tmp/testcat/hello.txt Hello'),
    # write-file with a multiline script body (newlines round-trip to the file)
    ((("write-file", "/tmp/d.sh", "#!/bin/bash\ndate\n"),),
     '(write-file "/tmp/d.sh" "#!/bin/bash\\ndate\\n")'),
    # metta expression with nested parens, followed by more actions on one line
    ((("metta", "(|- ((--> sam friend) (stv 1.0 0.9)))"), ("send", "ok")),
     '(metta "(|- ((--> sam friend) (stv 1.0 0.9)))") (send "ok")'),
    # shell git commit with embedded double quotes
    ((("shell", 'git -C /tmp/r commit -m "add hello 7"'),),
     '(shell "git -C /tmp/r commit -m \\"add hello 7\\"")'),
    # empty write-file content
    ((("write-file", "/tmp/e.txt", ""),), '(write-file "/tmp/e.txt" "")'),
]


def _render_json(json_str):
    os.environ["OMEGACLAW_ACTION_PROTOCOL"] = "json"
    return ap.parse_and_render_metta(json_str)


def test_act_renders_like_legacy_parser():
    mismatches = []
    for specs, legacy in CASES:
        got = _render_json(act(*specs))
        want = balance_parentheses(legacy)
        if got != want:
            mismatches.append((specs, got, want))
    assert not mismatches, "\n".join(
        f"{s}\n  json   -> {g}\n  legacy -> {w}" for s, g, w in mismatches
    )


def test_act_output_is_valid_protocol():
    for specs, _ in CASES:
        r = ap.parse_actions(act(*specs))
        assert r.ok, f"{specs} did not parse: {r.errors}"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    if failures:
        sys.exit(1)
    print("\nact() equivalence verified")
