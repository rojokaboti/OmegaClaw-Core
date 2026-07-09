"""KPI benchmark for Issue #13: skill eligibility gates vs the ungated baseline.

Deterministic, host-runnable. Drives the real ``src/skill_policy.py`` over the fixture
matrix (``skill_policy_fixtures.matrix``) and the real ``skill_loader.catalogue_block``
runtime path.

* **baseline** = `asi-alliance` behavior: no eligibility layer — every loaded skill is
  advertised regardless of OS/env/bin/config/toolset, so unrunnable skills reach the prompt
  (``false_eligible`` = every blocked fixture) and there is no remediation.
* **candidate** = per-fixture eligibility classification + a runtime prompt that advertises
  ONLY eligible skills, with secret-free remediation for the rest.

KPI gate (``sys.exit(1)`` on any failure):
  - classification accuracy == 1.0 (0 false-eligible, 0 false-blocked) on the matrix;
  - every blocked fixture carries actionable remediation;
  - 0 secret-value leaks into reasons or the runtime prompt;
  - the runtime catalogue advertises only eligible skills (baseline advertises all).

Writes ``skill_policy_results.{md,json}``. Run: ``python3 benchmarks/skill_policy_benchmark.py``
"""

import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_SRC, _REPO_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import skill_loader as sl  # noqa: E402
import skill_policy as sp  # noqa: E402
from skill_policy_fixtures import matrix, SECRET_VALUE  # noqa: E402


def evaluate():
    sp.reset_cache()
    fx = matrix()
    n = len(fx)
    false_eligible = false_blocked = missing_remediation = secret_leaks = 0
    baseline_false_eligible = 0

    for f in fx:
        e = sp.evaluate(f["skill"], f["cfg"], f["env"])
        # candidate correctness
        if e.eligible and not f["expect_eligible"]:
            false_eligible += 1
        if not e.eligible and f["expect_eligible"]:
            false_blocked += 1
        # every blocked fixture must carry actionable remediation + expected kinds
        if not e.eligible:
            if not e.reasons or any(not r.remediation.strip() for r in e.reasons):
                missing_remediation += 1
            kinds = {r.kind for r in e.reasons}
            if f["expect_kinds"] and not (f["expect_kinds"] & kinds):
                false_blocked += 1  # blocked, but for the wrong reason
        # secret value must never appear in any reason text
        blob = " ".join(r.detail + " " + r.remediation for r in e.reasons)
        if SECRET_VALUE in blob:
            secret_leaks += 1
        # baseline: no gate -> a skill that SHOULD be blocked is advertised anyway
        if not f["expect_eligible"]:
            baseline_false_eligible += 1

    # Runtime prompt path: catalogue advertises only eligible; secret never leaks into it.
    prompt_leak, prompt_only_eligible = _runtime_prompt_check()

    correct = n - false_eligible - false_blocked
    candidate = {
        "fixtures": n,
        "classification_accuracy": round(correct / n, 4) if n else 0.0,
        "false_eligible": false_eligible,
        "false_blocked": false_blocked,
        "missing_remediation": missing_remediation,
        "secret_leaks": secret_leaks + prompt_leak,
        "prompt_advertises_only_eligible": prompt_only_eligible,
    }
    baseline = {
        "fixtures": n,
        "classification_accuracy": round((n - baseline_false_eligible) / n, 4) if n else 0.0,
        "false_eligible": baseline_false_eligible,
        "false_blocked": 0,
        "missing_remediation": sum(1 for f in fx if not f["expect_eligible"]),
        "secret_leaks": 0,
        "prompt_advertises_only_eligible": False,
    }
    return {"baseline": baseline, "candidate": candidate}


def _runtime_prompt_check():
    """Build a real corpus on disk (one eligible, one blocked-needing-a-secret) and assert
    the runtime catalogue advertises only the eligible one and never prints the secret."""
    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "skills")
        for name, fm, body in [
            ("ok-runtime", "name: ok-runtime\ndescription: always runnable", "hi"),
            ("blocked-runtime",
             "name: blocked-runtime\ndescription: needs a secret\nrequired_environment_variables: [RUNTIME_SECRET]",
             "hi"),
        ]:
            sd = os.path.join(root, name)
            os.makedirs(sd)
            with open(os.path.join(sd, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write("---\n{}\n---\n{}\n".format(fm, body))
        cfg = {"version": 1, "roots": [root]}
        sl.reset_cache(); sp.reset_cache()
        # Set the required var to a SECRET value: the skill becomes eligible, and we assert
        # the value never appears in the prompt.
        os.environ["RUNTIME_SECRET"] = SECRET_VALUE
        try:
            block_all_eligible = sl.catalogue_block(cfg)
        finally:
            os.environ.pop("RUNTIME_SECRET", None)
        # Now WITHOUT the secret: blocked-runtime must not be advertised.
        sl.reset_cache(); sp.reset_cache()
        block = sl.catalogue_block(cfg)
        leak = 1 if (SECRET_VALUE in block_all_eligible or SECRET_VALUE in block) else 0
        only_eligible = ("- ok-runtime:" in block) and ("- blocked-runtime:" not in block) \
            and ("SKILL_UNAVAILABLE:" in block) and ("blocked-runtime" in block)
        sl.reset_cache(); sp.reset_cache()
        return leak, bool(only_eligible)


def render_md(s):
    b, c = s["baseline"], s["candidate"]
    rows = [
        ("Fixtures in matrix", "fixtures"),
        ("Classification accuracy (target 1.00)", "classification_accuracy"),
        ("False eligible (unrunnable advertised)", "false_eligible"),
        ("False blocked", "false_blocked"),
        ("Blocked without remediation", "missing_remediation"),
        ("Secret-value leaks (reasons + prompt)", "secret_leaks"),
        ("Prompt advertises only eligible skills", "prompt_advertises_only_eligible"),
    ]
    lines = [
        "# Skill-Eligibility KPI Benchmark — Issue #13",
        "",
        "Fixture matrix (`skill_policy_fixtures.matrix`) with one fixture per gate "
        "(OS / env / bins / anyBins / config / toolset) in both directions, plus the "
        "precedence cases (disabled / allowlist / entries / always) and a secret-value case, "
        "driven through the real `src/skill_policy.py` and the `skill_loader.catalogue_block` "
        "runtime path.",
        "",
        "- **baseline** = no eligibility layer: every loaded skill is advertised (unrunnable "
        "skills reach the prompt); no remediation.",
        "- **candidate** = per-fixture gating + prompt advertises only eligible skills, with "
        "secret-free remediation for the rest.",
        "",
        "| Metric | baseline | candidate |",
        "| --- | --- | --- |",
    ]
    for label, key in rows:
        lines.append("| {} | {} | {} |".format(label, b[key], c[key]))
    lines += [
        "",
        "Candidate classifies **{:.0%}** of the matrix correctly ({} false-eligible, {} "
        "false-blocked), attaches remediation to every blocked skill, leaks **{}** secret "
        "values, and advertises only eligible skills in the runtime prompt — vs the baseline "
        "advertising all {} blocked fixtures.".format(
            c["classification_accuracy"], c["false_eligible"], c["false_blocked"],
            c["secret_leaks"], b["false_eligible"]),
        "",
        "Reproduce: `python3 benchmarks/skill_policy_benchmark.py`",
        "",
    ]
    return "\n".join(lines)


def main():
    s = evaluate()
    with open(os.path.join(_HERE, "skill_policy_results.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    md = render_md(s)
    with open(os.path.join(_HERE, "skill_policy_results.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)

    c = s["candidate"]
    failures = []
    if c["classification_accuracy"] != 1.0:
        failures.append("classification accuracy {} != 1.0".format(c["classification_accuracy"]))
    if c["false_eligible"] or c["false_blocked"]:
        failures.append("misclassifications: {} false-eligible, {} false-blocked".format(
            c["false_eligible"], c["false_blocked"]))
    if c["missing_remediation"]:
        failures.append("{} blocked fixtures lack remediation".format(c["missing_remediation"]))
    if c["secret_leaks"]:
        failures.append("{} secret-value leaks".format(c["secret_leaks"]))
    if not c["prompt_advertises_only_eligible"]:
        failures.append("runtime prompt advertised non-eligible skills")
    if failures:
        print("\nKPI GATE: FAILED")
        for f in failures:
            print("  - " + f)
        sys.exit(1)
    print("\nKPI GATE: PASSED")


if __name__ == "__main__":
    main()
