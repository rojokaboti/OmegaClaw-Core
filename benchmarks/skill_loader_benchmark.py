"""KPI benchmark for Issue #11: filesystem SKILL.md loader vs the hardcoded baseline.

Deterministic and host-runnable (no MeTTa/LLM/Docker). Builds the representative
corpus (`skill_loader_fixtures.build_corpus`) and drives the REAL production module
`src/skill_loader.py` over it.

* **baseline** = `asi-alliance/OmegaClaw-Core@main` behavior: skills are hardcoded in
  `src/skills.metta` + `getSkills`, so the number of external filesystem SKILL.md
  bundles loadable **without editing core files is 0**, and malformed bundles produce
  no diagnostic.
* **candidate** = the loader discovers/validates the corpus with zero core edits: it
  loads every valid bundle, emits an actionable error for every invalid one (no silent
  omission), rejects path/symlink escapes, keeps per-skill prompt overhead within 20%
  of the name/description formula, and leaks no secret into the catalogue/body.

Writes `skill_loader_results.{md,json}`; exits non-zero if the KPI gate fails.
Run: `python3 benchmarks/skill_loader_benchmark.py`
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
from skill_loader_fixtures import build_corpus, INVALID_CASES  # noqa: E402


def evaluate():
    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "skills")
        info = build_corpus(root)
        cfg = {"version": 1, "roots": [root], "enabled": None, "disabled": []}

        sl.reset_cache()
        skills, errors = sl.load_skills(cfg)
        block = sl.catalogue_block(cfg)

        # Point get_skill_body at the same corpus (it reads the configured roots).
        os.environ["OMEGACLAW_SKILLS_CONFIG_PATH"] = os.path.join(d, "skills.yaml")
        import yaml
        with open(os.environ["OMEGACLAW_SKILLS_CONFIG_PATH"], "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f)
        sl.reset_cache()

        # --- metrics ---
        n_valid_loaded = len(skills)

        # every invalid fixture surfaced an actionable, non-empty error
        n_invalid_expected = info["n_invalid_expected"]
        n_errors = len([e for e in errors if e.message.strip()])

        # zero path/symlink escapes reached the loaded set
        escapes = [name for name in info["must_not_load"] if name in skills]

        # per-skill prompt overhead vs the bare "name: description" formula
        worst_ratio = 0.0
        for s in skills.values():
            line = sl.catalogue_line(s.name, s.description, 220)
            baseline_len = len(f"{s.name}: {s.description}") or 1
            worst_ratio = max(worst_ratio, len(line) / baseline_len)

        # no secret leaks: the embedded token must not appear in the catalogue, and a
        # use-skill body fetch of that bundle must redact it.
        token = info["secret_token"]
        body = sl.get_skill_body("secret-body")
        secret_in_catalogue = token in block
        secret_in_body = token in body

        # progressive disclosure works: {baseDir} resolved, unknown name is actionable
        body_with_support = sl.get_skill_body("skill-00")
        resolves_basedir = "{baseDir}" not in body_with_support and "/scripts/run.py" in body_with_support
        unknown_actionable = sl.get_skill_body("does-not-exist").startswith("USE-SKILL-ERROR:")

        os.environ.pop("OMEGACLAW_SKILLS_CONFIG_PATH", None)
        sl.reset_cache()

    candidate = {
        "external_bundles_loaded": n_valid_loaded,
        "invalid_with_actionable_error": n_errors,
        "invalid_expected": n_invalid_expected,
        "silent_omissions": max(0, n_invalid_expected - n_errors),
        "path_escapes": len(escapes),
        "worst_per_skill_overhead_ratio": round(worst_ratio, 3),
        "secret_in_catalogue": secret_in_catalogue,
        "secret_in_use_skill_body": secret_in_body,
        "basedir_resolved": resolves_basedir,
        "unknown_skill_actionable": unknown_actionable,
    }
    baseline = {
        "external_bundles_loaded": 0,
        "invalid_with_actionable_error": 0,
        "invalid_expected": n_invalid_expected,
        "silent_omissions": n_invalid_expected,
        "path_escapes": 0,
        "worst_per_skill_overhead_ratio": 0.0,
        "secret_in_catalogue": False,
        "secret_in_use_skill_body": False,
        "basedir_resolved": False,
        "unknown_skill_actionable": False,
    }
    return {"baseline": baseline, "candidate": candidate,
            "n_valid_expected": info["n_valid_expected"]}


def render_md(s):
    b, c = s["baseline"], s["candidate"]
    rows = [
        ("External SKILL.md bundles loaded (no core edits)", "external_bundles_loaded"),
        ("Invalid fixtures with an actionable error", "invalid_with_actionable_error"),
        ("Silent omissions (invalid dropped with no error)", "silent_omissions"),
        ("Path/symlink escapes reaching the loaded set", "path_escapes"),
        ("Worst per-skill prompt overhead ratio (target ≤ 1.20)", "worst_per_skill_overhead_ratio"),
        ("Secret leaked into catalogue", "secret_in_catalogue"),
        ("Secret leaked into use-skill body", "secret_in_use_skill_body"),
        ("{baseDir} resolved in use-skill body", "basedir_resolved"),
        ("Unknown skill name is actionable", "unknown_skill_actionable"),
    ]
    lines = [
        "# Skill-Loader KPI Benchmark — Issue #11",
        "",
        f"Corpus: **{c['external_bundles_loaded']} valid bundles** loaded + "
        f"**{c['invalid_expected']} invalid fixtures** "
        "(`skill_loader_fixtures.build_corpus`), driven through the real "
        "`src/skill_loader.py`.",
        "",
        "- **baseline** = `asi-alliance` hardcoded skills (`src/skills.metta` + `getSkills`): "
        "0 external filesystem bundles loadable without editing core files; no diagnostics for "
        "malformed bundles.",
        "- **candidate** = discovery + validation + compact catalogue + progressive disclosure, "
        "with zero core edits.",
        "",
        "| Metric | baseline | candidate |",
        "| --- | --- | --- |",
    ]
    for label, key in rows:
        lines.append(f"| {label} | {b[key]} | {c[key]} |")
    lines += [
        "",
        f"Loaded **{c['external_bundles_loaded']}** valid bundles (≥ 25 target) with zero "
        "hardcoded edits; every one of the "
        f"{c['invalid_expected']} invalid fixtures produced an actionable error "
        f"({c['silent_omissions']} silent omissions); **{c['path_escapes']}** path/symlink "
        "escapes reached the loaded set; worst per-skill catalogue overhead was "
        f"**{c['worst_per_skill_overhead_ratio']}×** the bare name/description formula "
        "(≤ 1.20 target); no secret leaked into the catalogue or a use-skill body.",
        "",
        "Reproduce: `python3 benchmarks/skill_loader_benchmark.py`",
        "",
    ]
    return "\n".join(lines)


def main():
    s = evaluate()
    with open(os.path.join(_HERE, "skill_loader_results.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    md = render_md(s)
    with open(os.path.join(_HERE, "skill_loader_results.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)

    c = s["candidate"]
    failures = []
    if c["external_bundles_loaded"] < 25:
        failures.append(f"loaded {c['external_bundles_loaded']} bundles (expected >= 25)")
    if c["silent_omissions"] != 0:
        failures.append(f"{c['silent_omissions']} invalid fixtures were silently omitted")
    if c["path_escapes"] != 0:
        failures.append(f"{c['path_escapes']} path/symlink escapes reached the loaded set")
    if c["worst_per_skill_overhead_ratio"] > 1.20:
        failures.append(f"per-skill overhead {c['worst_per_skill_overhead_ratio']} exceeds 1.20")
    if c["secret_in_catalogue"] or c["secret_in_use_skill_body"]:
        failures.append("a secret leaked into the catalogue or a use-skill body")
    if not (c["basedir_resolved"] and c["unknown_skill_actionable"]):
        failures.append("progressive disclosure ({baseDir} / unknown-name handling) regressed")
    if failures:
        print("\nKPI GATE: FAILED")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\nKPI GATE: PASSED")


if __name__ == "__main__":
    main()
