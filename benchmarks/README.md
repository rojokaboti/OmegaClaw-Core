# Action Protocol Benchmarks (Issue #1)

KPI experiment comparing the original loose-text command parser against the new
structured JSON action protocol.

## Run

```bash
python3 benchmarks/run_benchmark.py
```

Regenerates `results.json` (per-fixture rows + summaries) and `results.md`
(before/after table). Exit code is non-zero if the candidate fails the KPI
acceptance gate.

## What it measures

`fixtures.py` is a corpus of 50+ synthetic LLM outputs across seven categories:
`valid_json`, `legacy_text`, `malformed_json`, `unknown_tool`, `multiline_send`,
`file_ops`, `metta_expr`. Each fixture is labelled `execute` (a legitimate
action is expected) or `reject` (no executable command should be produced).

The runner feeds the same corpus through:

- **Baseline** — `helper.balance_parentheses` (original repo behavior).
- **Candidate (json)** — `action_protocol.parse_and_render_metta` in strict
  `json` mode (the shipping default).
- **Candidate (auto)** — the JSON-with-legacy-fallback migration mode.

and records parse success rate, validation rejection rate, false accepts
(unknown tool reaching the eval stream), false rejects (a legit action lost),
and the NOTHING_WAS_DONE count.

## KPI acceptance gate

The candidate (strict json) must:

1. have strictly fewer parse failures than the baseline, and
2. never accept an unknown/unsafe tool that the baseline would pass through
   (false accepts must be 0).

## Headline result

The committed `results.md` shows the candidate cutting parse failures ~60% and
eliminating unknown-tool false accepts entirely (baseline 38 → 0), while the
`auto` mode preserves 100% execute success by falling back to the legacy parser
for non-JSON output.

> Note: the in-container mock integration suite (`Autotests/mock/`) is the
> end-to-end gate and runs in CI under Docker. This benchmark and the
> `action_protocol` unit tests are pure Python and run on any host.
