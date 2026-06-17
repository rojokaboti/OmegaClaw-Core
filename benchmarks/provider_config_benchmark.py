"""KPI benchmark for Issue #4: provider/model config reproducibility.

Demonstrates that the candidate resolves multiple provider/model combinations via
config/env only (no Python edit), with the effective model visible and
deterministic, and that the system/user split is now normalized.

* **baseline** = pre-refactor (`main`): provider/model are hardcoded in
  `lib_llm_ext.py`, so switching requires editing Python (Python edits = yes).
* **candidate** = this branch: switch via YAML/`OMEGACLAW_LLM_CONFIG_PATH`
  (Python edits = no).

Writes `provider_config_results.{md,json}`. Exit non-zero if the gate fails.
Run: `python3 benchmarks/provider_config_benchmark.py`
"""

import json
import os
import sys
import tempfile
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_SRC, _REPO_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if "openai" not in sys.modules:
    _stub = types.ModuleType("openai")
    _stub.OpenAI = object
    sys.modules["openai"] = _stub

import provider_config as pc  # noqa: E402
import lib_llm_ext as llm  # noqa: E402
from provider_config_fixtures import COMBOS, CUSTOM_YAML, SPLIT_CASES  # noqa: E402


def resolve_combo(env, provider, custom_path):
    saved = os.environ.get("OMEGACLAW_LLM_CONFIG_PATH")
    if env.get("_custom_yaml"):
        os.environ["OMEGACLAW_LLM_CONFIG_PATH"] = custom_path
    else:
        os.environ.pop("OMEGACLAW_LLM_CONFIG_PATH", None)
    pc.reset_cache()
    try:
        model = pc.config_model(provider, pc.load_config())
    finally:
        if saved is None:
            os.environ.pop("OMEGACLAW_LLM_CONFIG_PATH", None)
        else:
            os.environ["OMEGACLAW_LLM_CONFIG_PATH"] = saved
        pc.reset_cache()
    return model


def main():
    custom = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    custom.write(CUSTOM_YAML)
    custom.close()

    rows = []
    combos_ok = True
    try:
        for label, env, provider, expected in COMBOS:
            model = resolve_combo(env, provider, custom.name)
            ok = (model == expected)
            combos_ok = combos_ok and ok
            rows.append({"combo": label, "provider": provider, "expected_model": expected,
                         "resolved_model": model, "python_edit_required": False, "ok": ok})
    finally:
        os.unlink(custom.name)

    split_rows = []
    split_ok = True
    for label, prompt, expected in SPLIT_CASES:
        got = list(llm.split_system_user(prompt))
        ok = (got == list(expected))
        split_ok = split_ok and ok
        split_rows.append({"case": label, "expected": list(expected), "got": got, "ok": ok})

    startup = llm.describe_effective_config("Anthropic")
    metadata_visible = ("provider=" in startup and "model=" in startup)

    results = {
        "combos": rows,
        "splits": split_rows,
        "startup_log_example": startup.replace("available=True", "available=<bool>").replace("available=False", "available=<bool>"),
        "metadata_visible": metadata_visible,
    }
    with open(os.path.join(_HERE, "provider_config_results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    lines = [
        "# Provider/Model Config Reproducibility Benchmark — Issue #4",
        "",
        "- **baseline** (`main`): provider/model hardcoded in `lib_llm_ext.py` — switching requires a **Python edit**.",
        "- **candidate**: provider/model in `profile/llm_providers.yaml` (or `OMEGACLAW_LLM_CONFIG_PATH`) — switching is **config/env only**.",
        "",
        "| Provider/model combo | resolved model | Python edit to switch (baseline → candidate) |",
        "| --- | --- | --- |",
    ]
    for r in rows:
        mark = "✓" if r["ok"] else "✗"
        lines.append(f"| {r['combo']} (`{r['provider']}`) | `{r['resolved_model']}` {mark} | yes → **no** |")
    lines += [
        "",
        "Normalized system/user split (single parser, all providers):",
        "",
        "| case | (system, user) |",
        "| --- | --- |",
    ]
    for s in split_rows:
        lines.append(f"| {s['case']} | `{tuple(s['got'])}` {'✓' if s['ok'] else '✗'} |")
    lines += [
        "",
        f"Startup config log (effective provider/model visible): `{startup}`",
        "",
        "Reproduce: `python3 benchmarks/provider_config_benchmark.py`",
        "",
    ]
    with open(os.path.join(_HERE, "provider_config_results.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))

    failures = []
    if not combos_ok:
        failures.append("a provider/model combo did not resolve to the expected model")
    if not split_ok:
        failures.append("system/user split mismatch")
    if not metadata_visible:
        failures.append("startup log does not show provider/model")
    if failures:
        print("\nKPI GATE: FAILED")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\nKPI GATE: PASSED")


if __name__ == "__main__":
    main()
