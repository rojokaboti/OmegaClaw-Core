"""Install-source corpus for the Issue #12 benchmark: N local dirs + N temp git repos.

Git repos are created locally (real `git`, no network) so the "Git/ClawHub-compatible" half of
the KPI is deterministic. ClawHub (HTTP) is exercised by Autotests/test_skill_install.py against
a localhost fixture; if `git` is unavailable the git group is skipped and reported (never
silently dropped).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

N_LOCAL = 10
N_GIT = 10


def _bundle(root, name, version="1.0.0"):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\nname: {}\ndescription: fixture skill {}\nversion: {}\n---\n# {}\nbody\n".format(
            name, name, version, name))
    return d


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=cwd, check=True, capture_output=True, text=True)


def build_sources(base=None):
    """Return (sources, meta). Each source is (spec, expected_name)."""
    base = base or tempfile.mkdtemp(prefix="skill_install_corpus_")
    sources = []
    for i in range(N_LOCAL):
        name = "local-skill-{:02d}".format(i)
        src = os.path.join(base, "local", name + "-src")
        _bundle(src, name)
        sources.append(("local:" + src, name))

    git_available = shutil.which("git") is not None
    if git_available:
        for i in range(N_GIT):
            name = "git-skill-{:02d}".format(i)
            repo = os.path.join(base, "git", name + "-repo")
            _bundle(repo, name)
            _git(repo, "init", "-q")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", "init")
            sources.append(("git+" + repo, name))

    # one invalid source (no frontmatter) to exercise rollback
    bad = os.path.join(base, "bad-src", "bad")
    os.makedirs(bad, exist_ok=True)
    with open(os.path.join(bad, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("# no frontmatter\n")
    invalid_source = "local:" + os.path.join(base, "bad-src")

    return {
        "base": base,
        "sources": sources,
        "invalid_source": invalid_source,
        "n_local": N_LOCAL,
        "n_git": N_GIT if git_available else 0,
        "git_available": git_available,
    }


if __name__ == "__main__":
    info = build_sources()
    print("sources:", len(info["sources"]), "| local:", info["n_local"], "| git:", info["n_git"],
          "| git_available:", info["git_available"])
