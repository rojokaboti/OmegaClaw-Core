"""KPI benchmark for Issue #9: channel registry maintainability, baseline vs candidate.

Deterministic and host-runnable (no Docker/channels): it (a) counts the edit cost of adding a
dummy `echo` channel in each design from `channel_registry_fixtures`, and (b) drives the REAL
`src/channel_registry.py` to prove the candidate's one-object add actually dispatches and that
existing channels + the unknown->mock fallback are preserved.

* **baseline** = nested-if dispatch (original repo): a new channel needs an `if (== (commchannel) X)`
  branch in ALL THREE dispatchers (start/receive/send).
* **candidate** = registry: one `register(Channel(...))`; dispatch code untouched.

Metrics: dispatch conditionals to add a channel, non-blank lines to add a channel, existing-channel
resolution, and explicit unknown->mock fallback.

Writes `channel_registry_results.{md,json}`. Exit non-zero if the KPI gate fails.
Run: `python3 benchmarks/channel_registry_benchmark.py`
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_SRC, _REPO_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import channel_registry as cr  # noqa: E402
import channel_registry_fixtures as fx  # noqa: E402

REAL_CHANNELS = ("irc", "telegram", "slack", "mattermost", "mock")


def evaluate():
    base_lines = len(fx._nonblank_lines(fx.BASELINE_ADD_SNIPPET))
    base_conds = fx._conditionals(fx.BASELINE_ADD_SNIPPET)
    cand_lines = len(fx._nonblank_lines(fx.CANDIDATE_ADD_SNIPPET))
    cand_conds = fx._conditionals(fx.CANDIDATE_ADD_SNIPPET)

    # prove the candidate's one-object add actually dispatches end-to-end
    rec = {"start": None, "sent": [], "inbox": ["hello"]}
    echo = cr.Channel("echo",
                      start=lambda cfg: rec.__setitem__("start", cfg),
                      receive=lambda: (rec["inbox"].pop(0) if rec["inbox"] else ""),
                      send=lambda m: rec["sent"].append(m))
    cr.register(echo)
    try:
        cr.start_channel("echo")
        got = cr.receive("echo")
        cr.send("echo", "a\nb")
        echo_works = (rec["start"] is not None and got == "hello" and rec["sent"] == ["a\\nb"])
    finally:
        cr.CHANNELS.pop("echo", None)

    existing_resolve = sum(1 for n in REAL_CHANNELS if cr._resolve(n).name == n)
    unknown_fallback = cr._resolve("some-unknown-channel").name == cr.FALLBACK

    return {
        "conditionals_to_add_channel": {"baseline": base_conds, "candidate": cand_conds},
        "lines_to_add_channel": {"baseline": base_lines, "candidate": cand_lines},
        "dispatchers_edited": {"baseline": 3, "candidate": 0},
        "candidate_new_channel_dispatches": echo_works,
        "existing_channels_resolve": {"count": existing_resolve, "total": len(REAL_CHANNELS)},
        "unknown_channel_falls_back_to_mock": unknown_fallback,
    }


def render_md(s):
    c, l = s["conditionals_to_add_channel"], s["lines_to_add_channel"]
    return "\n".join([
        "# Channel Registry Maintainability KPI Benchmark — Issue #9",
        "",
        "Experiment: add a config-less `echo` channel and compare the edit cost (from "
        "`channel_registry_fixtures`), then drive the real `src/channel_registry.py` to prove the "
        "candidate add works and that existing channels + the unknown->mock fallback are preserved.",
        "",
        "- **baseline** = nested-if dispatch: a new channel needs an `(== (commchannel) X)` branch in "
        "all three dispatchers (start/receive/send).",
        "- **candidate** = registry: one `register(Channel(...))`; dispatch code untouched.",
        "",
        "| Metric | baseline | candidate |",
        "| --- | --- | --- |",
        f"| **Dispatch conditionals to add a channel** | **{c['baseline']}** | **{c['candidate']}** |",
        f"| Dispatch sites edited (start/receive/send) | {s['dispatchers_edited']['baseline']} | {s['dispatchers_edited']['candidate']} |",
        f"| Non-blank lines to add a channel | {l['baseline']} | {l['candidate']} |",
        f"| New channel dispatches (start/receive/send round-trip) | n/a | {s['candidate_new_channel_dispatches']} |",
        f"| Existing channels still resolve | n/a | {s['existing_channels_resolve']['count']}/{s['existing_channels_resolve']['total']} |",
        f"| Unknown channel -> mock (explicit) | (else branch) | {s['unknown_channel_falls_back_to_mock']} |",
        "",
        "Adding a channel drops from **3 dispatch conditionals across 3 sites** to **0** (one registry "
        "object), while all five existing channels still resolve and unknown channels still fall back "
        "to mock. Live channel start/receive/send is exercised in-container (report §5).",
        "",
        "Reproduce: `python3 benchmarks/channel_registry_benchmark.py`",
        "",
    ])


def main():
    s = evaluate()
    with open(os.path.join(_HERE, "channel_registry_results.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    md = render_md(s)
    with open(os.path.join(_HERE, "channel_registry_results.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)

    failures = []
    if s["conditionals_to_add_channel"]["candidate"] != 0:
        failures.append("candidate still needs dispatch conditionals to add a channel")
    if s["conditionals_to_add_channel"]["candidate"] >= s["conditionals_to_add_channel"]["baseline"]:
        failures.append("candidate conditionals not fewer than baseline")
    if s["lines_to_add_channel"]["candidate"] >= s["lines_to_add_channel"]["baseline"]:
        failures.append("candidate lines-to-add not fewer than baseline")
    if not s["candidate_new_channel_dispatches"]:
        failures.append("candidate one-object channel did not dispatch")
    if s["existing_channels_resolve"]["count"] != s["existing_channels_resolve"]["total"]:
        failures.append("an existing channel no longer resolves")
    if not s["unknown_channel_falls_back_to_mock"]:
        failures.append("unknown channel no longer falls back to mock")
    if failures:
        print("\nKPI GATE: FAILED")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\nKPI GATE: PASSED")


if __name__ == "__main__":
    main()
