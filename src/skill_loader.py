"""Filesystem SKILL.md loader & prompt compiler (Issue #11).

OpenClaw and Hermes ship skills as portable filesystem bundles: a directory with a
``SKILL.md`` (YAML frontmatter + Markdown instructions) plus optional ``scripts/``,
``references/`` and ``templates/`` support files. OmegaClaw previously exposed skills
only as hardcoded MeTTa equations + a static ``getSkills`` prose tuple, so none of that
ecosystem could be consumed without a rewrite. This module discovers, validates and
compiles external ``SKILL.md`` bundles into the agent prompt.

Design (verified against the runtime, see rojo-docs/issue-11-skill-loader.md):

- A ``SKILL.md`` is a **procedural instruction playbook the agent follows using the
  existing primitive tools** (shell / read-file / send / metta / …), NOT a new atomic
  tool with its own executable body. So loaded skills are injected as *guidance*, and
  the single static ``use-skill <name>`` tool performs progressive disclosure (returns
  the full body on demand). This avoids the per-skill 4-surface coupling
  (``getSkills`` + ``helper.LLM_COMMANDS`` + ``action_protocol.ARG_SPEC`` + MeTTa body)
  that adding a real tool requires.
- Stdlib + PyYAML only, import-light, host-unit-testable, mirroring
  ``provider_config.py`` / ``channel_registry.py``: repo-root-relative env-overridable
  paths, mtime-signature cache + ``reset_cache()``, warn-once, best-effort side effects,
  ``redaction.redact_secrets`` on any logged/rendered text, ``_selftest()`` under
  ``__main__``.
- **Fail-open + fail-safe:** zero configured/discovered skills => empty catalogue =>
  the loop is unchanged. A malformed bundle is skipped **with an actionable error**
  (never a silent omission, never a crash).
- **Path containment (down-payment on #19):** every discovered ``SKILL.md`` and its
  resolved base dir must ``realpath`` to a location under the configured root, so a
  symlink or ``..`` escape is rejected.

#11 only *parses* the eligibility metadata fields (``metadata.openclaw`` /
``metadata.hermes`` / ``platforms`` / ``required_environment_variables``); actual
eligibility gating (OS / env / bins / config / toolsets) is Issue #13.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

try:  # pytest-from-root vs in-package import
    from redaction import redact_secrets
except ImportError:  # pragma: no cover - import shim
    from src.redaction import redact_secrets

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_CONFIG_PATH = os.path.join(_REPO_ROOT, "profile", "skills.yaml")

# Frontmatter fields we recognize (others are preserved under .metadata untouched).
_SKILL_FILENAMES = ("SKILL.md", "skill.md")

# Prompt-overhead knobs (also settable via profile/skills.yaml).
_DEFAULT_MAX_DESCRIPTION_CHARS = 220
_DEFAULT_BODY_MAX_CHARS = int(os.environ.get("OMEGACLAW_SKILL_BODY_MAX_CHARS", "20000"))

# Built-in default config: fail-open net AND canonical mirror of the shipped
# profile/skills.yaml. Empty roots => no external skills => loop unchanged.
_BUILTIN_DEFAULTS: Dict[str, Any] = {
    "version": 1,
    "roots": ["skills"],          # repo-root-relative; absent dir is simply skipped
    "enabled": None,              # optional allowlist of skill names (None => all)
    "disabled": [],               # denylist of skill names
    "max_description_chars": _DEFAULT_MAX_DESCRIPTION_CHARS,
}

_CACHE: Dict[Any, Any] = {}
_WARNED: set = set()


def _warn_once(message: str) -> None:
    if message not in _WARNED:
        print(f"[skill_loader] {message}", flush=True)
        _WARNED.add(message)


# --------------------------------------------------------------------------- config

def config_path() -> str:
    """Active config path. A relative ``OMEGACLAW_SKILLS_CONFIG_PATH`` resolves against
    the install root (not the process CWD), so it works wherever the agent runs."""
    env = os.environ.get("OMEGACLAW_SKILLS_CONFIG_PATH")
    if not env:
        return _DEFAULT_CONFIG_PATH
    return env if os.path.isabs(env) else os.path.join(_REPO_ROOT, env)


def builtin_defaults() -> Dict[str, Any]:
    import copy
    return copy.deepcopy(_BUILTIN_DEFAULTS)


def validate_config(cfg: Any) -> Optional[str]:
    """Return an error string if ``cfg`` is structurally invalid, else None."""
    if not isinstance(cfg, dict):
        return "config root is not a mapping"
    roots = cfg.get("roots", [])
    if roots is not None and not isinstance(roots, list):
        return "'roots' must be a list"
    for name in ("enabled", "disabled"):
        val = cfg.get(name)
        if val is not None and not isinstance(val, list):
            return f"'{name}' must be a list or null"
    return None


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Load + validate the skills config, caching by (path, mtime).

    Fail-open: a missing/unparseable/invalid config logs a warning and falls back to
    the built-in defaults (empty external-skill set is a safe no-op), so the agent
    never bricks on a misconfiguration.
    """
    path = path or config_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return builtin_defaults()

    key = ("config", path, mtime)
    if key in _CACHE:
        return _CACHE[key]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as exc:  # noqa: BLE001 - never let config break the loop
        _warn_once(f"WARNING failed to parse {path}: {exc}; using built-in defaults")
        _CACHE[key] = builtin_defaults()
        return _CACHE[key]

    if data is None:
        data = builtin_defaults()
    err = validate_config(data)
    if err:
        _warn_once(f"WARNING invalid config {path}: {err}; using built-in defaults")
        _CACHE[key] = builtin_defaults()
        return _CACHE[key]

    # Fill defaults for absent knobs.
    merged = builtin_defaults()
    merged.update({k: v for k, v in data.items() if v is not None or k in data})
    _CACHE[key] = merged
    return merged


# --------------------------------------------------------------------------- model

@dataclass
class Skill:
    """A discovered, validated filesystem skill bundle."""
    name: str
    description: str
    version: str = ""
    platforms: List[str] = field(default_factory=list)
    required_environment_variables: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    base_dir: str = ""       # absolute path of the skill directory
    skill_file: str = ""     # absolute path of the SKILL.md
    body: str = ""           # Markdown instructions (frontmatter stripped)


@dataclass
class SkillError:
    """An actionable, non-fatal problem with one bundle (never silently dropped)."""
    path: str
    message: str

    def __str__(self) -> str:  # for terse logging / test readability
        return f"{self.path}: {self.message}"


# --------------------------------------------------------------------------- discovery

def _roots(cfg: Dict[str, Any]) -> List[str]:
    out = []
    for r in (cfg.get("roots") or []):
        if not isinstance(r, str) or not r.strip():
            continue
        out.append(r if os.path.isabs(r) else os.path.join(_REPO_ROOT, r))
    return out


def _iter_skill_files(root_abs: str):
    """Yield SKILL.md paths under ``root_abs`` (one per bundle directory)."""
    for dirpath, dirnames, filenames in os.walk(root_abs, followlinks=False):
        for fname in _SKILL_FILENAMES:
            if fname in filenames:
                yield os.path.join(dirpath, fname)
                break  # one skill file per directory


def _contained(child_abs: str, root_abs: str) -> bool:
    """True iff ``child_abs`` realpath-resolves to a location under ``root_abs``.

    Rejects symlink escapes and ``..`` traversal — the loader's local down-payment on
    the full install trust boundary (Issue #19).
    """
    root_real = os.path.realpath(root_abs)
    child_real = os.path.realpath(child_abs)
    root_prefix = root_real.rstrip(os.sep) + os.sep
    return child_real == root_real or child_real.startswith(root_prefix)


def _split_frontmatter(text: str) -> Tuple[Optional[str], str]:
    """Split a SKILL.md into (frontmatter_yaml, body). Frontmatter is the block between
    a leading ``---`` line and the next ``---`` line. Returns (None, text) if absent."""
    stripped = text.lstrip("﻿")  # tolerate a BOM
    lines = stripped.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm = "".join(lines[1:i])
            body = "".join(lines[i + 1:])
            return fm, body
    return None, text  # unterminated fence => treat as no frontmatter


def _as_str_list(val: Any) -> List[str]:
    if val is None:
        return []
    if isinstance(val, str):
        return [val]
    if isinstance(val, list):
        return [str(v) for v in val]
    return [str(val)]


def _parse_skill(skill_file: str, root_abs: str) -> Tuple[Optional[Skill], Optional[SkillError]]:
    """Parse+validate one SKILL.md. Returns (skill, None) or (None, error)."""
    rel = os.path.relpath(skill_file, root_abs)
    base_dir = os.path.dirname(skill_file)

    # Containment: both the file and its base dir must stay under the root.
    if not (_contained(skill_file, root_abs) and _contained(base_dir, root_abs)):
        return None, SkillError(rel, "path escapes its skill root (symlink or '..' traversal) — rejected")

    try:
        with open(skill_file, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        return None, SkillError(rel, f"could not read SKILL.md: {exc}")

    fm_text, body = _split_frontmatter(text)
    if fm_text is None:
        return None, SkillError(rel, "missing YAML frontmatter (expected a leading '---' block)")
    try:
        fm = yaml.safe_load(fm_text)
    except Exception as exc:  # noqa: BLE001
        return None, SkillError(rel, f"unparseable YAML frontmatter: {exc}")
    if not isinstance(fm, dict):
        return None, SkillError(rel, "frontmatter is not a YAML mapping")

    name_raw = fm.get("name")
    if not isinstance(name_raw, str) or not name_raw.strip():
        return None, SkillError(rel, "frontmatter missing required non-empty 'name'")
    name_stripped = name_raw.strip()
    # A skill name is an identifier used for lookup (and, in #12, as an install dir
    # name), so it must be path-safe: no separators or '..' traversal.
    if (name_stripped in (".", "..") or ".." in name_stripped
            or "/" in name_stripped or "\\" in name_stripped):
        return None, SkillError(rel, f"unsafe skill name {name_stripped!r} (no path separators or '..')")
    description = fm.get("description")
    if not isinstance(description, str) or not description.strip():
        return None, SkillError(rel, f"skill {name_stripped!r} missing required non-empty 'description'")

    metadata = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    return Skill(
        name=name_stripped,
        description=description.strip(),
        version=str(fm.get("version", "")).strip(),
        platforms=_as_str_list(fm.get("platforms")),
        required_environment_variables=_as_str_list(fm.get("required_environment_variables")),
        metadata=metadata,
        base_dir=os.path.abspath(base_dir),
        skill_file=os.path.abspath(skill_file),
        body=body,
    ), None


# --------------------------------------------------------------------------- loading

def _discovery_signature(cfg: Dict[str, Any]) -> Tuple:
    """A cache key that changes when any config/root/skill-file mtime changes."""
    sig: List[Tuple] = []
    try:
        sig.append(("cfg", config_path(), os.path.getmtime(config_path())))
    except OSError:
        sig.append(("cfg", config_path(), None))
    for root_abs in _roots(cfg):
        if not os.path.isdir(root_abs):
            sig.append((root_abs, None))
            continue
        for sf in sorted(_iter_skill_files(root_abs)):
            try:
                sig.append((sf, os.path.getmtime(sf)))
            except OSError:
                sig.append((sf, None))
    return tuple(sig)


def load_skills(cfg: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Skill], List[SkillError]]:
    """Discover + validate ALL configured skills (no allow/deny filtering here).

    Returns ``(skills_by_name, errors)`` for every valid discovered bundle. First-wins on
    duplicate names (a second bundle claiming a taken name becomes an actionable
    ``SkillError``, not a silent overwrite). Cached by an mtime signature over the config +
    every discovered SKILL.md.

    NB (Issue #13): allow/deny/eligibility is the SINGLE responsibility of
    ``skill_policy`` — ``load_skills`` deliberately does **not** pre-filter by
    ``enabled``/``disabled``, so ``skill_policy`` sees the full set and can (a) honor
    ``entries`` overrides that force-include a skill past an allowlist miss, and (b) report
    ``disabled``/``not_allowlisted`` skills with remediation via ``skills doctor``.
    """
    cfg = cfg if cfg is not None else load_config()
    key = ("skills", tuple(_roots(cfg)), _discovery_signature(cfg))
    if key in _CACHE:
        return _CACHE[key]

    skills: Dict[str, Skill] = {}
    errors: List[SkillError] = []
    for root_abs in _roots(cfg):
        if not os.path.isdir(root_abs):
            continue
        for skill_file in sorted(_iter_skill_files(root_abs)):
            skill, err = _parse_skill(skill_file, root_abs)
            if err is not None:
                errors.append(err)
                continue
            assert skill is not None
            if skill.name in skills:
                errors.append(SkillError(
                    os.path.relpath(skill_file, root_abs),
                    f"duplicate skill name {skill.name!r} (first-wins; kept "
                    f"{os.path.relpath(skills[skill.name].skill_file, root_abs)})",
                ))
                continue
            skills[skill.name] = skill

    result = (skills, errors)
    _CACHE[key] = result
    return result


# --------------------------------------------------------------------------- prompt

def _max_description_chars(cfg: Dict[str, Any]) -> int:
    val = cfg.get("max_description_chars", _DEFAULT_MAX_DESCRIPTION_CHARS)
    try:
        return max(16, int(val))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_DESCRIPTION_CHARS


def catalogue_line(name: str, description: str, limit: int) -> str:
    """One compact catalogue line: ``- <name>: <description>`` (description clipped).

    Kept close to the OpenClaw name/description formula so per-skill prompt overhead
    stays within the KPI's 20% band; the ``use-skill`` instruction is a single shared
    header (see :func:`catalogue_block`), not repeated per line.
    """
    desc = description.strip().replace("\n", " ")
    if len(desc) > limit:
        desc = desc[: limit - 1].rstrip() + "…"
    return redact_secrets(f"- {name}: {desc}")


# Max invalid-bundle errors surfaced inline in the prompt (the rest are counted;
# the full list is always in the operator log). Keeps prompt overhead bounded.
_MAX_PROMPT_ERRORS = 5


def _error_segment(errors: List["SkillError"]) -> str:
    """Compact, bounded ``SKILL_LOAD_ERRORS`` prompt segment (redacted)."""
    shown = errors[:_MAX_PROMPT_ERRORS]
    parts = [redact_secrets("{}: {}".format(e.path, e.message)) for e in shown]
    extra = len(errors) - len(shown)
    if extra > 0:
        parts.append("(+{} more; see logs)".format(extra))
    return "SKILL_LOAD_ERRORS: {} bundle(s) skipped — ".format(len(errors)) + "; ".join(parts)


def _classify(skills, cfg):
    """Best-effort eligibility classification (Issue #13). Returns list[Eligibility] or
    None when skill_policy is unavailable (fail-open: everything is treated eligible)."""
    try:
        import skill_policy
    except ImportError:  # pragma: no cover
        try:
            from src import skill_policy
        except ImportError:
            return None
    try:
        return skill_policy.classify(list(skills.values()), cfg)
    except Exception as exc:  # noqa: BLE001 - eligibility must never break the prompt
        _warn_once("WARNING eligibility classification failed: {}".format(exc))
        return None


def eligible_skills(cfg: Optional[Dict[str, Any]] = None):
    """Return ``(eligible_by_name, blocked, errors)`` for the configured skills.

    ``blocked`` is a list of ``skill_policy.Eligibility`` (secret-free reasons + remediation).
    Fail-open: if eligibility can't be evaluated, every loaded skill is treated as eligible.
    """
    cfg = cfg if cfg is not None else load_config()
    skills, errors = load_skills(cfg)
    elig = _classify(skills, cfg)
    if elig is None:
        return dict(skills), [], errors
    eligible_names = {e.name for e in elig if e.eligible}
    eligible = {n: s for n, s in skills.items() if n in eligible_names}
    blocked = [e for e in elig if not e.eligible]
    return eligible, blocked, errors


def _unavailable_segment(blocked) -> str:
    """Compact, bounded ``SKILL_UNAVAILABLE`` note (kinds only — never secret values)."""
    shown = blocked[:_MAX_PROMPT_ERRORS]
    parts = []
    for e in shown:
        kinds = ", ".join(dict.fromkeys(r.kind for r in e.reasons)) or "blocked"
        parts.append(redact_secrets("{} ({})".format(e.name, kinds)))
    extra = len(blocked) - len(shown)
    if extra > 0:
        parts.append("(+{} more)".format(extra))
    return ("SKILL_UNAVAILABLE: {} skill(s) need setup — ".format(len(blocked))
            + "; ".join(parts) + " — run 'skills doctor' for remediation")


def catalogue_block(cfg: Optional[Dict[str, Any]] = None) -> str:
    """Render the ``LOADED_SKILLS`` (+ ``SKILL_LOAD_ERRORS``) prompt segment.

    Called from ``getContext`` in ``src/loop.metta`` via ``py-call``, mirroring how
    ``action_protocol.output_format_block()`` is injected. Best-effort: any failure
    yields an empty string so the loop is never broken.

    Invalid bundles are **never silently dropped** from the runtime path (Issue #11):
    every :class:`SkillError` is logged (operator channel, deduped by ``_warn_once``)
    AND, when any exist, summarized in a bounded ``SKILL_LOAD_ERRORS`` segment so the
    failure is visible in-band. Returns ``""`` only when there are no skills AND no
    errors (the empty-config no-op).
    """
    try:
        cfg = cfg if cfg is not None else load_config()
        skills, errors = load_skills(cfg)
        # Operator-visible log for every invalid bundle (deduped), so a malformed skill
        # is surfaced even when nothing valid loaded.
        for e in errors:
            _warn_once("SKILL_LOAD_ERROR " + redact_secrets(str(e)))

        # Eligibility gating (Issue #13): only advertise skills that can actually run
        # here, unless OMEGACLAW_SKILLS_DEBUG is set (then show all, incl. blocked).
        eligible, blocked, _ = eligible_skills(cfg)
        debug = (os.environ.get("OMEGACLAW_SKILLS_DEBUG") or "").strip().lower() in {"1", "true", "yes", "on"}
        shown = skills if debug else eligible

        segments = []
        if shown:
            limit = _max_description_chars(cfg)
            header = (
                "LOADED_SKILLS: filesystem skills available (procedural playbooks you follow "
                "using your existing tools). To read a skill's full instructions before using "
                "it, call use-skill with its name."
            )
            lines = [catalogue_line(s.name, s.description, limit)
                     for s in sorted(shown.values(), key=lambda s: s.name)]
            segments.append(header + " " + " ".join(lines))
        if blocked:
            segments.append(_unavailable_segment(blocked))
        if errors:
            segments.append(_error_segment(errors))
        return "  ".join(segments)
    except Exception as exc:  # noqa: BLE001 - never break the prompt build
        _warn_once(f"WARNING catalogue_block failed: {exc}")
        return ""


def _resolve_placeholders(body: str, skill: Skill) -> str:
    """Substitute ``{baseDir}``/``{skillDir}`` with the skill's absolute directory so
    the agent can reference support files (``scripts/``, ``references/``, …)."""
    return body.replace("{baseDir}", skill.base_dir).replace("{skillDir}", skill.base_dir)


def get_skill_body(name: str) -> str:
    """MeTTa bridge for the ``use-skill`` tool: return a skill's full instructions.

    Progressive disclosure — the compact catalogue advertises names+descriptions; this
    returns the full body on demand with ``{baseDir}``/``{skillDir}`` resolved. Returns
    an actionable message (never raises) when the skill is unknown, so a bad name
    surfaces as feedback instead of a crash.
    """
    try:
        name = (name or "").strip()
        skills, _errors = load_skills()
        skill = skills.get(name)
        if skill is None:
            available = ", ".join(sorted(skills)) or "(none loaded)"
            return f"USE-SKILL-ERROR: unknown skill {name!r}. Available: {available}"
        # Redact the AUTHORED body first (catches a secret-shaped token accidentally
        # embedded in a skill — the body flows into the prompt as LAST_SKILL_USE_RESULTS),
        # THEN substitute the trusted {baseDir}. Doing it in this order avoids the
        # long-base64 redaction rule mangling the resolved absolute path (which contains
        # '/'), while still scrubbing untrusted skill content.
        body = _resolve_placeholders(redact_secrets(skill.body), skill).strip()
        if len(body) > _DEFAULT_BODY_MAX_CHARS:
            body = body[:_DEFAULT_BODY_MAX_CHARS].rstrip() + "\n…[truncated]"
        header = f"SKILL {skill.name}"
        if skill.version:
            header += f" (v{skill.version})"
        header += f" — files under: {skill.base_dir}"
        return f"{header}\n\n{body}"
    except Exception as exc:  # noqa: BLE001
        return f"USE-SKILL-ERROR: {exc}"


def list_skills() -> List[str]:
    """Sorted names of currently-loaded skills (convenience for CLI/tests)."""
    skills, _errors = load_skills()
    return sorted(skills)


def reset_cache() -> None:
    """Test helper: drop the config + discovery caches and warn-once memory."""
    _CACHE.clear()
    _WARNED.clear()


# --------------------------------------------------------------------------- selftest

def _selftest() -> None:
    """Lightweight self-tests runnable without pytest/Docker."""
    import tempfile

    reset_cache()
    tmp = tempfile.mkdtemp(prefix="skill_loader_selftest_")
    root = os.path.join(tmp, "skills")
    os.makedirs(root)

    def _write(dirname, content):
        d = os.path.join(root, dirname)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(content)
        return d

    # A valid OpenClaw-style skill with a support-file reference.
    _write("pdf-fill", (
        "---\n"
        "name: pdf-fill\n"
        "description: Fill PDF forms from a data file\n"
        "version: 1.2.0\n"
        "metadata:\n  openclaw:\n    requires:\n      bins: [pdftk]\n"
        "required_environment_variables: [PDF_LICENSE_KEY]\n"
        "---\n"
        "# PDF fill\nRun the helper at {baseDir}/scripts/fill.py to populate the form.\n"
    ))
    # A no-requirement skill — always eligible, so it appears in the catalogue.
    _write("greet", "---\nname: greet\ndescription: Greet the user\n---\n# greet\nSay hi.\n")
    # A malformed skill (no frontmatter) — must produce an actionable error, not vanish.
    _write("broken", "# no frontmatter here\njust text\n")

    cfg = {"version": 1, "roots": [root], "enabled": None, "disabled": []}
    skills, errors = load_skills(cfg)
    assert "pdf-fill" in skills, skills
    assert skills["pdf-fill"].version == "1.2.0"
    assert skills["pdf-fill"].required_environment_variables == ["PDF_LICENSE_KEY"]
    assert any("broken" in e.path and "frontmatter" in e.message for e in errors), errors

    # Catalogue (Issue #13 eligibility): the always-eligible skill is advertised; pdf-fill
    # (needs PDF_LICENSE_KEY, unset) is NOT advertised but flagged as needing setup; the
    # secret env-var value is never rendered; invalid bundles are surfaced (never silent).
    block = catalogue_block(cfg)
    assert "- greet: Greet the user" in block, block
    assert "- pdf-fill:" not in block, block                       # blocked -> not advertised
    assert "SKILL_UNAVAILABLE:" in block and "pdf-fill" in block, block
    assert "PDF_LICENSE_KEY" not in block                          # neither value NOR name leaked
    assert "SKILL_LOAD_ERRORS:" in block and "broken/SKILL.md" in block, block

    # Debug flag advertises everything, including blocked skills.
    os.environ["OMEGACLAW_SKILLS_DEBUG"] = "1"
    try:
        dbg = catalogue_block(cfg)
        assert "- pdf-fill:" in dbg, dbg
    finally:
        os.environ.pop("OMEGACLAW_SKILLS_DEBUG", None)

    # Per-skill overhead within 20% of the bare name/description formula.
    name, desc = "pdf-fill", "Fill PDF forms from a data file"
    line = catalogue_line(name, desc, 220)
    baseline = len(f"{name}: {desc}")
    assert len(line) <= baseline * 1.2 + 2, (len(line), baseline)

    # Progressive disclosure resolves {baseDir}; unknown name is actionable.
    os.environ["OMEGACLAW_SKILLS_CONFIG_PATH"] = os.path.join(tmp, "skills.yaml")
    with open(os.environ["OMEGACLAW_SKILLS_CONFIG_PATH"], "w", encoding="utf-8") as f:
        yaml.safe_dump({"version": 1, "roots": [root]}, f)
    reset_cache()
    body = get_skill_body("pdf-fill")
    assert "/scripts/fill.py" in body and "{baseDir}" not in body, body
    assert get_skill_body("nope").startswith("USE-SKILL-ERROR:")
    del os.environ["OMEGACLAW_SKILLS_CONFIG_PATH"]

    # Symlink escape is rejected. A symlinked *directory* is simply never descended
    # into (os.walk followlinks=False), so the real escape vector is a file-level
    # SKILL.md symlink pointing outside the root — realpath containment catches it.
    reset_cache()
    outside = os.path.join(tmp, "outside")
    os.makedirs(outside)
    with open(os.path.join(outside, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\nname: evil\ndescription: escape\n---\nbody\n")
    sneaky = os.path.join(root, "sneaky")
    os.makedirs(sneaky)
    try:
        os.symlink(os.path.join(outside, "SKILL.md"), os.path.join(sneaky, "SKILL.md"))
        skills2, errors2 = load_skills({"version": 1, "roots": [root]})
        assert "evil" not in skills2, "symlink escape must be rejected"
        assert any("escapes" in e.message for e in errors2), errors2
    except OSError:
        pass  # platform without symlink support — skip that assertion

    # Empty config is a safe no-op.
    reset_cache()
    assert catalogue_block({"version": 1, "roots": []}) == ""

    reset_cache()
    print("skill_loader self-tests passed")


if __name__ == "__main__":
    _selftest()
