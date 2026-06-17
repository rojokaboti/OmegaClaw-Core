"""KPI benchmark for Issue #3: raw-LLM-logging privacy, baseline vs candidate.

Captures what `_log_raw` prints for a secret-bearing response under three configs:

* **baseline**  -- the pre-fix behavior (`raw={raw!r}`, unconditional), reproduced
  inline to match `main` before this change.
* **default**   -- the new metadata-only default (no env set).
* **debug**     -- the new raw-but-redacted mode (`OMEGACLAW_DEBUG_LLM_RAW=1`).

Reports per config: raw body present? secret-leak count? metadata present? Writes
`llm_logging_results.{md,json}`. Exit code is non-zero if the KPI gate fails.

Run: `python3 benchmarks/llm_logging_benchmark.py`
"""

import io
import json
import os
import sys
import time
import types
from contextlib import redirect_stdout

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
for _p in (_REPO_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Stub openai so importing lib_llm_ext works without the dependency.
if "openai" not in sys.modules:
    _stub = types.ModuleType("openai")
    _stub.OpenAI = object
    sys.modules["openai"] = _stub

import lib_llm_ext as llm  # noqa: E402
from llm_logging_fixtures import RESPONSE, SECRETS, NORMAL_TEXT  # noqa: E402


def _baseline_log(provider, model, raw):
    """The pre-fix _log_raw (main): logs the full raw body unconditionally."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[LLM_RAW] ts={ts} provider={provider} model={model} chars={len(raw or '')} raw={raw!r}")


def _capture(fn, **env):
    saved = {k: os.environ.get(k) for k in ("OMEGACLAW_DEBUG_LLM_RAW", "OMEGACLAW_LLM_LOG_PATH")}
    for k in saved:
        os.environ.pop(k, None)
    os.environ.update({k: v for k, v in env.items() if v is not None})
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            fn("OpenAI", "gpt-test", RESPONSE)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return buf.getvalue()


def _analyze(out):
    leaks = [s for s in SECRETS if s in out]
    return {
        "raw_body_present": NORMAL_TEXT in out,
        "secret_leaks": len(leaks),
        "leaked": leaks,
        "metadata_present": ("provider=" in out and "model=" in out and "chars=" in out),
    }


def main():
    configs = {
        "baseline": _capture(_baseline_log),
        "default": _capture(llm._log_raw),
        "debug": _capture(llm._log_raw, OMEGACLAW_DEBUG_LLM_RAW="1"),
    }
    analysis = {name: _analyze(out) for name, out in configs.items()}

    with open(os.path.join(_HERE, "llm_logging_results.json"), "w", encoding="utf-8") as f:
        json.dump({"analysis": analysis, "captured": configs}, f, indent=2)

    b, d, g = analysis["baseline"], analysis["default"], analysis["debug"]
    md = "\n".join([
        "# LLM Raw-Logging Privacy Benchmark — Issue #3",
        "",
        "One secret-bearing model response logged under each config (GitHub token + PAT, "
        "OpenAI/Anthropic keys, bearer token, AWS key, long base64 secret).",
        "",
        "- **baseline** = pre-fix `_log_raw` (`raw={raw!r}`, unconditional)",
        "- **default** = new metadata-only default (no env)",
        "- **debug** = new `OMEGACLAW_DEBUG_LLM_RAW=1` (raw, redacted)",
        "",
        "| Metric | baseline | default | debug |",
        "| --- | --- | --- | --- |",
        f"| Raw body in log | {b['raw_body_present']} | {d['raw_body_present']} | {g['raw_body_present']} |",
        f"| **Unredacted secret leaks** | {b['secret_leaks']} | {d['secret_leaks']} | {g['secret_leaks']} |",
        f"| Metadata present | {b['metadata_present']} | {d['metadata_present']} | {g['metadata_present']} |",
        "",
        f"Baseline leaked {b['secret_leaks']}/{len(SECRETS)} secret-shaped strings; the new "
        f"default leaks {d['secret_leaks']} (and logs no raw body) while keeping metadata; "
        f"debug shows raw context with {g['secret_leaks']} unredacted secrets.",
        "",
        "Reproduce: `python3 benchmarks/llm_logging_benchmark.py`",
        "",
    ])
    with open(os.path.join(_HERE, "llm_logging_results.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)

    failures = []
    if d["raw_body_present"] or d["secret_leaks"]:
        failures.append("default config leaked raw body or secrets")
    if g["secret_leaks"]:
        failures.append(f"debug config leaked {g['secret_leaks']} unredacted secret(s)")
    if not d["metadata_present"]:
        failures.append("default config dropped useful metadata")
    if b["secret_leaks"] == 0:
        failures.append("baseline did not leak (fixture not representative)")

    if failures:
        print("\nKPI GATE: FAILED")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\nKPI GATE: PASSED")


if __name__ == "__main__":
    main()
