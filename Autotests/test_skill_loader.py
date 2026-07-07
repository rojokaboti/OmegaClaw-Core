"""Unit tests for the filesystem SKILL.md loader (Issue #11).

Pure-Python, no Docker/real-channel deps (imports ``src/skill_loader.py`` directly).
Runs under pytest and standalone (``python3 Autotests/test_skill_loader.py``). Covers
discovery, frontmatter parse/validate, path/symlink containment, duplicate handling,
``{baseDir}`` substitution, catalogue shape/overhead, progressive disclosure, secret
redaction, and the empty-config no-op.
"""
import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO_ROOT, "src")
_BENCH = os.path.join(_REPO_ROOT, "benchmarks")
for _p in (_SRC, _REPO_ROOT, _BENCH):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import skill_loader as sl  # noqa: E402


def _mk(root, dirname, content):
    d = os.path.join(root, dirname)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(content)
    return d


def _root():
    root = os.path.join(tempfile.mkdtemp(prefix="skl_test_"), "skills")
    os.makedirs(root)
    return root


def test_discovery_and_frontmatter_parse():
    sl.reset_cache()
    root = _root()
    _mk(root, "greet", "---\nname: greet\ndescription: Greet the user warmly\nversion: 2.0.1\n"
                        "platforms: [linux]\nrequired_environment_variables: [GREET_KEY]\n---\n# body\n")
    skills, errors = sl.load_skills({"version": 1, "roots": [root]})
    assert list(skills) == ["greet"], skills
    s = skills["greet"]
    assert s.description == "Greet the user warmly"
    assert s.version == "2.0.1"
    assert s.platforms == ["linux"]
    assert s.required_environment_variables == ["GREET_KEY"]
    assert errors == []


def test_missing_fields_are_actionable_errors_not_silent():
    sl.reset_cache()
    root = _root()
    _mk(root, "noname", "---\ndescription: has no name\n---\nb\n")
    _mk(root, "nodesc", "---\nname: nodesc\n---\nb\n")
    _mk(root, "nofm", "# no frontmatter\n")
    skills, errors = sl.load_skills({"version": 1, "roots": [root]})
    assert skills == {}
    msgs = " | ".join(e.message for e in errors)
    assert "name" in msgs and "description" in msgs and "frontmatter" in msgs, msgs
    assert len(errors) == 3


def test_unsafe_name_rejected():
    sl.reset_cache()
    root = _root()
    _mk(root, "evil", "---\nname: ../escaped\ndescription: traversal via name\n---\nb\n")
    skills, errors = sl.load_skills({"version": 1, "roots": [root]})
    assert skills == {}
    assert any("unsafe skill name" in e.message for e in errors), errors


def test_symlink_escape_rejected():
    sl.reset_cache()
    tmp = tempfile.mkdtemp(prefix="skl_link_")
    root = os.path.join(tmp, "skills")
    os.makedirs(root)
    outside = os.path.join(tmp, "outside")
    os.makedirs(outside)
    with open(os.path.join(outside, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\nname: evil\ndescription: outside\n---\nb\n")
    sneaky = os.path.join(root, "sneaky")
    os.makedirs(sneaky)
    try:
        os.symlink(os.path.join(outside, "SKILL.md"), os.path.join(sneaky, "SKILL.md"))
    except OSError:
        return  # platform without symlink support
    skills, errors = sl.load_skills({"version": 1, "roots": [root]})
    assert "evil" not in skills
    assert any("escapes" in e.message for e in errors), errors


def test_duplicate_name_first_wins_with_error():
    sl.reset_cache()
    root = _root()
    _mk(root, "a-first", "---\nname: dup\ndescription: first definition\n---\nb\n")
    _mk(root, "b-second", "---\nname: dup\ndescription: second definition\n---\nb\n")
    skills, errors = sl.load_skills({"version": 1, "roots": [root]})
    assert list(skills) == ["dup"]
    assert skills["dup"].description == "first definition"  # a-first sorts before b-second
    assert any("duplicate" in e.message for e in errors), errors


def test_load_skills_does_not_prefilter_allow_deny():
    """load_skills discovers ALL valid bundles; allow/deny is skill_policy's job (Issue #13).

    (Regression for the PR #35 review: load_skills used to pre-filter by enabled/disabled,
    which killed entries overrides and hid disabled/not_allowlisted skills from doctor.)
    """
    sl.reset_cache()
    root = _root()
    for n in ("one", "two", "three"):
        _mk(root, n, f"---\nname: {n}\ndescription: skill {n}\n---\nb\n")
    loaded, _ = sl.load_skills({"version": 1, "roots": [root], "enabled": ["one"], "disabled": ["two"]})
    assert set(loaded) == {"one", "two", "three"}          # everything is loaded
    # filtering happens in the eligibility layer instead:
    eligible, blocked, _ = sl.eligible_skills({"version": 1, "roots": [root],
                                               "enabled": ["one"], "disabled": ["two"]})
    assert set(eligible) == {"one"}                         # only the allowlisted one
    assert {b.name for b in blocked} == {"two", "three"}    # denied + not-allowlisted, with reasons


def test_entries_override_forces_include_past_allowlist():
    """entries.<name>.enabled: true must force-include a skill past an allowlist miss."""
    sl.reset_cache()
    root = _root()
    for n in ("a", "b"):
        _mk(root, n, f"---\nname: {n}\ndescription: skill {n}\n---\nb\n")
    eligible, _, _ = sl.eligible_skills(
        {"version": 1, "roots": [root], "enabled": ["a"], "entries": {"b": {"enabled": True}}})
    assert set(eligible) == {"a", "b"}


def test_catalogue_block_shape_and_overhead():
    sl.reset_cache()
    root = _root()
    _mk(root, "alpha", "---\nname: alpha\ndescription: Does the alpha thing\n---\nb\n")
    cfg = {"version": 1, "roots": [root]}
    block = sl.catalogue_block(cfg)
    assert block.startswith("LOADED_SKILLS:")
    assert "use-skill" in block
    assert "- alpha: Does the alpha thing" in block
    # per-skill overhead within 20% of the bare "name: description" formula
    line = sl.catalogue_line("alpha", "Does the alpha thing", 220)
    baseline = len("alpha: Does the alpha thing")
    assert len(line) <= baseline * 1.2 + 2


def test_get_skill_body_resolves_basedir_and_handles_unknown():
    sl.reset_cache()
    root = _root()
    d = _mk(root, "withref", "---\nname: withref\ndescription: references support files\n---\n"
                             "Run {baseDir}/scripts/go.py now.\n")
    os.makedirs(os.path.join(d, "scripts"), exist_ok=True)
    cfg_path = os.path.join(os.path.dirname(root), "skills.yaml")
    import yaml
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"version": 1, "roots": [root]}, f)
    os.environ["OMEGACLAW_SKILLS_CONFIG_PATH"] = cfg_path
    sl.reset_cache()
    try:
        body = sl.get_skill_body("withref")
        assert "{baseDir}" not in body and d + "/scripts/go.py" in body, body
        assert sl.get_skill_body("nope").startswith("USE-SKILL-ERROR:")
    finally:
        os.environ.pop("OMEGACLAW_SKILLS_CONFIG_PATH", None)
        sl.reset_cache()


def test_secret_in_body_is_redacted():
    sl.reset_cache()
    root = _root()
    _mk(root, "leaky", "---\nname: leaky\ndescription: embeds a token\n---\n"
                       "token sk-ant-DEADBEEFdeadbeef01234567 here\n")
    cfg_path = os.path.join(os.path.dirname(root), "skills.yaml")
    import yaml
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"version": 1, "roots": [root]}, f)
    os.environ["OMEGACLAW_SKILLS_CONFIG_PATH"] = cfg_path
    sl.reset_cache()
    try:
        body = sl.get_skill_body("leaky")
        assert "sk-ant-DEADBEEF" not in body and "[REDACTED:" in body, body
    finally:
        os.environ.pop("OMEGACLAW_SKILLS_CONFIG_PATH", None)
        sl.reset_cache()


def test_catalogue_block_surfaces_invalid_skills():
    """The RUNTIME prompt path (catalogue_block) must not silently drop invalid bundles.

    Regression for the PR #33 review: the direct load_skills() tests pass errors back, but
    catalogue_block() — the function injected into getContext — previously discarded them,
    so a malformed bundle vanished from the operator/agent view.
    """
    sl.reset_cache()
    root = _root()
    _mk(root, "bad", "# just markdown, no frontmatter\n")
    block = sl.catalogue_block({"version": 1, "roots": [root]})
    assert block, "malformed-only root must not yield an empty (silent) catalogue"
    assert "SKILL_LOAD_ERRORS:" in block
    assert "bad/SKILL.md" in block and "frontmatter" in block, block


def test_catalogue_block_shows_valid_and_invalid_together():
    sl.reset_cache()
    root = _root()
    _mk(root, "good", "---\nname: good\ndescription: a valid skill\n---\nbody\n")
    _mk(root, "bad", "---\nname: bad\n---\nno description\n")
    block = sl.catalogue_block({"version": 1, "roots": [root]})
    assert "- good: a valid skill" in block                           # valid catalogue present
    assert "SKILL_LOAD_ERRORS:" in block and "bad/SKILL.md" in block   # and errors surfaced


def test_use_skill_renders_through_action_protocol():
    """Drift guard: the use-skill tool validates + renders end-to-end (Issue #11)."""
    try:
        import action_protocol as ap
    except ImportError:
        from src import action_protocol as ap
    out = ap.parse_and_render_metta('{"actions":[{"tool":"use-skill","args":{"name":"demo"}}]}')
    assert out == '((use-skill "demo"))', out


def test_empty_config_is_noop():
    sl.reset_cache()
    assert sl.catalogue_block({"version": 1, "roots": []}) == ""
    skills, errors = sl.load_skills({"version": 1, "roots": []})
    assert skills == {} and errors == []


def test_representative_corpus_loads():
    """The full benchmark corpus loads 25+ valid bundles with zero core edits."""
    sl.reset_cache()
    from skill_loader_fixtures import build_corpus
    root = os.path.join(tempfile.mkdtemp(prefix="skl_corpus_"), "skills")
    info = build_corpus(root)
    skills, errors = sl.load_skills({"version": 1, "roots": [root]})
    assert len(skills) >= 25, len(skills)
    for banned in info["must_not_load"]:
        assert banned not in skills
    assert len(errors) == info["n_invalid_expected"], (len(errors), info["n_invalid_expected"])


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok:", fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL:", fn.__name__, e)
    if failed:
        sys.exit(1)
    print(f"\nAll {len(fns)} skill_loader tests passed")
