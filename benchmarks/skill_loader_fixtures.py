"""Representative SKILL.md corpus for the Issue #11 loader benchmark & tests.

No real OpenClaw/Hermes clone exists on this machine, so (per the plan, user-approved)
this generator materializes a deterministic corpus that faithfully models the real
format: a bundle directory with a ``SKILL.md`` (YAML frontmatter + Markdown body) plus
optional ``scripts/`` / ``references/`` / ``templates/`` support files, OpenClaw- and
Hermes-style metadata gates, and ``required_environment_variables``.

``build_corpus(root)`` writes:
- **25 valid bundles** (``N_VALID``) — the KPI corpus, including 5 with support files
  and metadata gates.
- a **failure matrix** (``INVALID_CASES``) — malformed frontmatter, missing ``name``,
  missing ``description``, a duplicate name, a ``..``-traversal name, and (where the OS
  supports it) a file-level symlink escaping the root.

It is a generator (like the other ``benchmarks/*_fixtures.py``), not checked-in data,
so the corpus is reproducible and diffable.
"""

from __future__ import annotations

import os

N_VALID = 25

# Invalid fixtures that MUST each surface an actionable error (never silent omission).
# 'symlink' is added at build time only when the platform supports os.symlink.
INVALID_CASES = [
    ("no-frontmatter", "malformed: SKILL.md with no YAML frontmatter fence"),
    ("missing-name", "frontmatter missing required 'name'"),
    ("missing-description", "frontmatter missing required 'description'"),
    ("dup-name", "second bundle claiming an already-taken name (first-wins)"),
    ("dot-dot-traversal", "name/dir attempting '..' traversal is contained"),
]

# A body that embeds a secret-looking token, to prove redaction on rendered output.
_SECRET_TOKEN = "sk-ant-DEADBEEFdeadbeef01234567"


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _valid_skill_md(i):
    """Vary style/metadata across the 25 valid bundles."""
    name = f"skill-{i:02d}"
    desc = f"Deterministic fixture skill number {i} that automates a representative task"
    lines = ["---", f"name: {name}", f"description: {desc}", f"version: 1.{i}.0"]
    # Alternate OpenClaw-style and Hermes-style metadata.
    if i % 3 == 0:
        lines += [
            "metadata:",
            "  openclaw:",
            "    requires:",
            "      bins: [git]",
            "platforms: [linux, darwin]",
        ]
    elif i % 3 == 1:
        lines += [
            "metadata:",
            "  hermes:",
            "    requires_toolsets: [files]",
            "required_environment_variables: [FIXTURE_TOKEN]",
        ]
    lines += ["---", f"# {name}", "", f"Steps for task {i}:", "",
              "1. Read the input.", "2. Transform it.", "3. Report the result."]
    # A few bundles reference support files via {baseDir}.
    if i % 5 == 0:
        lines += ["", f"Run `{{baseDir}}/scripts/run.py` and read `{{baseDir}}/references/spec.md`."]
    return name, "\n".join(lines) + "\n"


def build_corpus(root):
    """Materialize the corpus under ``root``. Returns a dict describing expectations."""
    os.makedirs(root, exist_ok=True)
    valid_names = []
    for i in range(N_VALID):
        name, md = _valid_skill_md(i)
        d = os.path.join(root, name)
        _write(os.path.join(d, "SKILL.md"), md)
        valid_names.append(name)
        # Support files for the multiples-of-5 bundles.
        if i % 5 == 0:
            _write(os.path.join(d, "scripts", "run.py"), "print('ok')\n")
            _write(os.path.join(d, "references", "spec.md"), "# spec\n")
            _write(os.path.join(d, "templates", "out.tmpl"), "{{result}}\n")

    # One valid bundle whose body embeds a secret-looking token (redaction target).
    _write(os.path.join(root, "secret-body", "SKILL.md"),
           "---\nname: secret-body\ndescription: skill whose body accidentally embeds a token\n"
           f"---\n# secret-body\nDo NOT do this, but here is a token: {_SECRET_TOKEN}\n")
    valid_names.append("secret-body")

    # ---- failure matrix ----
    _write(os.path.join(root, "no-frontmatter", "SKILL.md"),
           "# just markdown, no frontmatter\nnothing here\n")
    _write(os.path.join(root, "missing-name", "SKILL.md"),
           "---\ndescription: has a description but no name\n---\nbody\n")
    _write(os.path.join(root, "missing-description", "SKILL.md"),
           "---\nname: nodesc\n---\nbody\n")
    # duplicate name: two bundles both named 'skill-00' (first-wins, second -> error)
    _write(os.path.join(root, "zzz-dup", "SKILL.md"),
           "---\nname: skill-00\ndescription: a duplicate of skill-00\n---\nbody\n")
    # a name that tries to traverse — loader must never let a name escape the root
    _write(os.path.join(root, "dot-dot-traversal", "SKILL.md"),
           "---\nname: ../escaped\ndescription: attempts path traversal via name\n---\nbody\n")

    has_symlink = False
    outside = os.path.join(os.path.dirname(root), "corpus_outside")
    os.makedirs(outside, exist_ok=True)
    _write(os.path.join(outside, "SKILL.md"),
           "---\nname: symlink-escape\ndescription: outside the root\n---\nbody\n")
    sneaky_dir = os.path.join(root, "sneaky")
    os.makedirs(sneaky_dir, exist_ok=True)
    try:
        os.symlink(os.path.join(outside, "SKILL.md"), os.path.join(sneaky_dir, "SKILL.md"))
        has_symlink = True
    except OSError:
        pass  # platform without symlink support

    return {
        "root": root,
        "n_valid_expected": len(valid_names),
        "valid_names": valid_names,
        "n_invalid_expected": len(INVALID_CASES) + (1 if has_symlink else 0),
        "has_symlink": has_symlink,
        "secret_token": _SECRET_TOKEN,
        "must_not_load": ["symlink-escape", "../escaped", "nodesc"],
    }


if __name__ == "__main__":
    import tempfile
    info = build_corpus(os.path.join(tempfile.mkdtemp(prefix="skill_corpus_"), "skills"))
    print("built corpus:", {k: v for k, v in info.items() if k != "valid_names"})
