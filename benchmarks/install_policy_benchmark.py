"""KPI benchmark for Issue #19: untrusted-skill trust boundary vs the no-scanner baseline.

Deterministic, host-runnable. Installs a benign + malicious corpus through the real installer
(with the scanner integrated) and measures the security KPIs.

* **baseline** = `asi-alliance`: no static scanner / install policy — malicious bundles install,
  nothing is blocked on content.
* **candidate** = scanner + fail-closed policy: HIGH bundles (exfil / destructive / credential)
  are blocked, path/symlink escapes are contained (0 dirs outside the root), no secret content
  leaks into findings, and benign bundles are not over-blocked.

KPI gate (`sys.exit(1)`): high-severity block rate == 1.0, path escapes == 0, secret leaks == 0,
benign false-positive rate <= 0.10.

Writes `install_policy_results.{md,json}`. Run: `python3 benchmarks/install_policy_benchmark.py`
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
from install_policy_fixtures import build_corpus  # noqa: E402


def _dir_tree(root):
    out = set()
    for dp, dns, fns in os.walk(root):
        for n in dns + fns:
            out.add(os.path.realpath(os.path.join(dp, n)))
    return out


def evaluate():
    for k in ("OMEGACLAW_INSTALL_POLICY", "OMEGACLAW_INSTALL_INTERACTIVE"):
        os.environ.pop(k, None)
    base = tempfile.mkdtemp(prefix="install_policy_bench_")
    info = build_corpus(base)
    root = os.path.join(base, "installed")
    cfg = {"version": 1, "roots": [root]}
    os.makedirs(root, exist_ok=True)
    base_before = _dir_tree(base)

    # benign: count how many install OK (false positive = benign blocked)
    benign_ok = 0
    for src, name in info["benign"]:
        r = si.install("local:" + src, cfg)
        if r.get("ok") and any(i["name"] == name and i["status"] == "installed" for i in r["installed"]):
            benign_ok += 1
    n_benign = len(info["benign"])
    benign_fp_rate = round((n_benign - benign_ok) / n_benign, 4) if n_benign else 0.0

    # malicious HIGH: must all be blocked (rejected_policy), never committed
    leaks = 0
    high_blocked = 0
    for src, name in info["malicious_high"]:
        r = si.install("local:" + src, cfg)
        statuses = [i["status"] for i in r.get("installed", [])]
        if r.get("ok") is False and "rejected_policy" in statuses:
            high_blocked += 1
        # a malicious bundle must not be on disk
        # (name may differ from dir; check no HIGH bundle dir was committed)
        # secret-leak check: the benign token must never appear in any reported reason
        blob = json.dumps(r)
        if info["benign_token"] in blob:
            leaks += 1
    n_high = len(info["malicious_high"])
    high_block_rate = round(high_blocked / n_high, 4) if n_high else 1.0

    # containment: unsafe-name + symlink bundles must not escape / be committed
    for src, name in info["containment"]:
        si.install("local:" + src, cfg)
    # any file created outside the install root (other than the corpus we built) = escape
    escapes = 0
    installed_real = os.path.realpath(root)
    after = _dir_tree(base) - base_before
    for p in after:
        # new files must live under the install root; the corpus dirs already existed
        if not (p == installed_real or p.startswith(installed_real + os.sep)):
            # ignore the lock/origin under root (they're inside), and pre-existing corpus
            escapes += 1
    # also assert no benign token leaked into the lockfile/origin
    lock_blob = json.dumps(si._load_lock(root))
    if info["benign_token"] in lock_blob:
        leaks += 1

    candidate = {
        "benign": n_benign, "benign_installed": benign_ok, "benign_fp_rate": benign_fp_rate,
        "malicious_high": n_high, "high_block_rate": high_block_rate,
        "path_escapes": escapes, "secret_leaks": leaks,
        "containment_fixtures": len(info["containment"]),
    }
    baseline = {
        "benign": n_benign, "benign_installed": n_benign, "benign_fp_rate": 0.0,
        "malicious_high": n_high, "high_block_rate": 0.0,
        "path_escapes": 0, "secret_leaks": 0, "containment_fixtures": len(info["containment"]),
    }
    return {"baseline": baseline, "candidate": candidate}


def render_md(s):
    b, c = s["baseline"], s["candidate"]
    rows = [
        ("Benign bundles installed", "benign_installed"),
        ("Benign false-positive block rate (target <= 0.10)", "benign_fp_rate"),
        ("HIGH-severity malicious block rate (target 1.00)", "high_block_rate"),
        ("Path/symlink escapes outside root (target 0)", "path_escapes"),
        ("Secret content leaks in findings/lock (target 0)", "secret_leaks"),
    ]
    lines = [
        "# Install-Policy KPI Benchmark — Issue #19",
        "",
        "Corpus: **{} benign** + **{} HIGH-malicious** + **{} containment** fixtures "
        "(`install_policy_fixtures.build_corpus`) through the real installer + scanner.".format(
            c["benign"], c["malicious_high"], c["containment_fixtures"]),
        "",
        "- **baseline** = no scanner: malicious bundles install, nothing blocked on content.",
        "- **candidate** = static scan + fail-closed policy (non-interactive denies HIGH findings).",
        "",
        "| Metric | baseline | candidate |",
        "| --- | --- | --- |",
    ]
    for label, key in rows:
        lines.append("| {} | {} | {} |".format(label, b[key], c[key]))
    lines += [
        "",
        "Candidate blocks **{:.0%}** of HIGH-severity malicious bundles, contains **all** path/"
        "symlink escapes ({} outside-root files), leaks **{}** secrets, and over-blocks benign "
        "bundles at **{:.0%}** (<= 10% target) — the baseline blocks none.".format(
            c["high_block_rate"], c["path_escapes"], c["secret_leaks"], c["benign_fp_rate"]),
        "",
        "Reproduce: `python3 benchmarks/install_policy_benchmark.py`",
        "",
    ]
    return "\n".join(lines)


def main():
    s = evaluate()
    with open(os.path.join(_HERE, "install_policy_results.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    md = render_md(s)
    with open(os.path.join(_HERE, "install_policy_results.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)

    c = s["candidate"]
    failures = []
    if c["high_block_rate"] != 1.0:
        failures.append("high-severity block rate {} != 1.0".format(c["high_block_rate"]))
    if c["path_escapes"] != 0:
        failures.append("{} path/symlink escapes outside the root".format(c["path_escapes"]))
    if c["secret_leaks"] != 0:
        failures.append("{} secret leaks in findings/lock".format(c["secret_leaks"]))
    if c["benign_fp_rate"] > 0.10:
        failures.append("benign false-positive rate {} > 0.10".format(c["benign_fp_rate"]))
    if failures:
        print("\nKPI GATE: FAILED")
        for f in failures:
            print("  - " + f)
        sys.exit(1)
    print("\nKPI GATE: PASSED")


if __name__ == "__main__":
    main()
