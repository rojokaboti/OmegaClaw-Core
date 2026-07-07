"""KPI benchmark for Issue #12: skill install lifecycle vs the no-lifecycle baseline.

Deterministic, host-runnable. Installs a corpus of local + git sources through the real
``src/skill_install.py``, then exercises reinstall (idempotency), pin + update-all, verify, and
rollback on an invalid source.

* **baseline** = `asi-alliance`: no install lifecycle at all — 0 installs, no lockfile.
* **candidate** = install/update/list/remove/verify/pin with a lockfile recording
  source/ref/version/content_hash/trust for every skill.

KPI gate (``sys.exit(1)`` on failure): install success >= 0.95, lock-metadata coverage == 1.0,
0 duplicate directories after reinstall, pinned skills untouched by update-all, rollback leaves
the root unchanged.

Writes ``skill_install_results.{md,json}``. Run: ``python3 benchmarks/skill_install_benchmark.py``
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

import skill_install as si  # noqa: E402
from skill_install_fixtures import build_sources  # noqa: E402

_REQUIRED_LOCK_FIELDS = ("source_type", "source", "content_hash", "installed_at", "trust")


def evaluate():
    base = tempfile.mkdtemp(prefix="skill_install_bench_")
    info = build_sources(base)
    cfg = {"version": 1, "roots": [os.path.join(base, "installed")]}
    sources = info["sources"]

    # 1) install the whole corpus
    ok = 0
    for spec, name in sources:
        r = si.install(spec, cfg)
        if r.get("ok") and any(i["name"] == name for i in r["installed"]):
            ok += 1
    n = len(sources)
    install_success = ok / n if n else 0.0

    root = si.install_root(cfg)
    lock = si._load_lock(root)

    # 2) lock-metadata coverage: every installed skill has all required fields + version-or-ref
    covered = 0
    for name, e in lock["skills"].items():
        has_fields = all(e.get(k) for k in _REQUIRED_LOCK_FIELDS)
        has_ver_or_ref = bool(e.get("version") or e.get("ref"))
        if has_fields and has_ver_or_ref:
            covered += 1
    lock_coverage = covered / len(lock["skills"]) if lock["skills"] else 0.0

    # 3) idempotency: reinstall everything -> installed-dir count == skill count (no dupes)
    for spec, _ in sources:
        si.install(spec, cfg)
    skill_dirs = [d for d in os.listdir(root)
                  if os.path.isdir(os.path.join(root, d)) and
                  os.path.exists(os.path.join(root, d, "SKILL.md"))]
    duplicate_dirs = len(skill_dirs) - len(lock["skills"])

    # 4) pin protection: pin one, hash it, update --all, confirm untouched + skipped
    first = sorted(lock["skills"])[0]
    si.pin(first, cfg)
    pinned_hash = si._hash_dir(os.path.join(root, first))
    up = si.update(cfg=cfg, all_skills=True)
    pinned_skipped = any(u["name"] == first and u["status"] == "skipped_pinned" for u in up["updated"])
    pinned_unchanged = si._hash_dir(os.path.join(root, first)) == pinned_hash

    # 5) verify all OK
    verify_ok = si.verify(cfg=cfg)["ok"]

    # 6) rollback: an invalid source installs nothing and leaves the root unchanged
    before = sorted(os.listdir(root))
    rb = si.install(info["invalid_source"], cfg)
    rollback_ok = (not rb["ok"]) and sorted(os.listdir(root)) == before

    candidate = {
        "sources": n,
        "install_success": round(install_success, 4),
        "lock_coverage": round(lock_coverage, 4),
        "duplicate_dirs": duplicate_dirs,
        "pinned_skipped_by_update_all": pinned_skipped,
        "pinned_unchanged": pinned_unchanged,
        "verify_all_ok": verify_ok,
        "rollback_correct": rollback_ok,
        "git_sources": info["n_git"],
        "local_sources": info["n_local"],
    }
    baseline = {
        "sources": n, "install_success": 0.0, "lock_coverage": 0.0, "duplicate_dirs": 0,
        "pinned_skipped_by_update_all": False, "pinned_unchanged": False,
        "verify_all_ok": False, "rollback_correct": False,
        "git_sources": 0, "local_sources": 0,
    }
    return {"baseline": baseline, "candidate": candidate, "git_available": info["git_available"]}


def render_md(s):
    b, c = s["baseline"], s["candidate"]
    rows = [
        ("Install sources (local + git)", "sources"),
        ("Install success rate (target >= 0.95)", "install_success"),
        ("Lock-metadata coverage (target 1.00)", "lock_coverage"),
        ("Duplicate dirs after reinstall (target 0)", "duplicate_dirs"),
        ("Pinned skipped by update --all", "pinned_skipped_by_update_all"),
        ("Pinned bytes unchanged after update --all", "pinned_unchanged"),
        ("verify: all installed skills OK", "verify_all_ok"),
        ("Rollback leaves root unchanged on bad source", "rollback_correct"),
    ]
    lines = [
        "# Skill-Install KPI Benchmark — Issue #12",
        "",
        "Corpus: **{} local** + **{} git** sources (git repos created locally — real `git`, no "
        "network; ClawHub HTTP is covered by `Autotests/test_skill_install.py`), driven through "
        "the real `src/skill_install.py`.".format(c["local_sources"], c["git_sources"]),
        "" if s["git_available"] else "\n> NOTE: `git` unavailable on this host — git sources skipped (reported, not silently dropped).",
        "",
        "- **baseline** = no install lifecycle (0 installs, no lockfile).",
        "- **candidate** = fetch→validate→commit→lock with idempotent reinstall, pinning, verify, rollback.",
        "",
        "| Metric | baseline | candidate |",
        "| --- | --- | --- |",
    ]
    for label, key in rows:
        lines.append("| {} | {} | {} |".format(label, b[key], c[key]))
    lines += [
        "",
        "Candidate installs **{:.0%}** of the corpus, records complete lock metadata for "
        "**{:.0%}** of skills, produces **{}** duplicate dirs on reinstall, protects pinned "
        "skills from `update --all`, and rolls back cleanly on an invalid source — none of which "
        "the baseline can do.".format(c["install_success"], c["lock_coverage"], c["duplicate_dirs"]),
        "",
        "Reproduce: `python3 benchmarks/skill_install_benchmark.py`",
        "",
    ]
    return "\n".join(lines)


def main():
    s = evaluate()
    with open(os.path.join(_HERE, "skill_install_results.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    md = render_md(s)
    with open(os.path.join(_HERE, "skill_install_results.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)

    c = s["candidate"]
    failures = []
    if c["install_success"] < 0.95:
        failures.append("install success {} < 0.95".format(c["install_success"]))
    if c["lock_coverage"] != 1.0:
        failures.append("lock coverage {} != 1.0".format(c["lock_coverage"]))
    if c["duplicate_dirs"] != 0:
        failures.append("{} duplicate dirs after reinstall".format(c["duplicate_dirs"]))
    if not (c["pinned_skipped_by_update_all"] and c["pinned_unchanged"]):
        failures.append("pinned skill was not protected from update --all")
    if not c["verify_all_ok"]:
        failures.append("verify reported problems on a fresh install")
    if not c["rollback_correct"]:
        failures.append("rollback did not leave the root unchanged on a bad source")
    if failures:
        print("\nKPI GATE: FAILED")
        for f in failures:
            print("  - " + f)
        sys.exit(1)
    print("\nKPI GATE: PASSED")


if __name__ == "__main__":
    main()
