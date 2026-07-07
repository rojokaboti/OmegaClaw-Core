"""Unit tests for skill eligibility gates + readiness diagnostics (Issue #13).

Pure-Python (imports src/skill_policy.py + src/skill_loader.py directly). Runs under pytest
and standalone (`python3 Autotests/test_skill_policy.py`). Covers the gate matrix, precedence,
strict no-secret logging, the doctor report, and the catalogue_block eligibility integration
(only-eligible advertised + SKILL_UNAVAILABLE note + debug override).
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
import skill_policy as sp  # noqa: E402
from skill_policy_fixtures import matrix, SECRET_VALUE  # noqa: E402


def test_gate_matrix_classifies_perfectly():
    sp.reset_cache()
    for f in matrix():
        e = sp.evaluate(f["skill"], f["cfg"], f["env"])
        assert e.eligible == f["expect_eligible"], (f["skill"].name, e.eligible, [r.kind for r in e.reasons])
        if not e.eligible and f["expect_kinds"]:
            assert f["expect_kinds"] & {r.kind for r in e.reasons}, (f["skill"].name, [r.kind for r in e.reasons])


def test_every_blocked_reason_has_remediation():
    for f in matrix():
        e = sp.evaluate(f["skill"], f["cfg"], f["env"])
        if not e.eligible:
            assert e.reasons and all(r.remediation.strip() for r in e.reasons), f["skill"].name


def test_secret_value_never_leaks_in_reasons():
    # a present secret-valued var -> eligible, value never rendered
    e = sp.evaluate(_skill("s", envs=["FIXTURE_SECRET"]), {}, {"FIXTURE_SECRET": SECRET_VALUE})
    assert e.eligible
    # mixed: one present (secret value) + one missing -> blocked on the MISSING name only
    e2 = sp.evaluate(_skill("s2", envs=["FIXTURE_SECRET", "MISSING_NAME"]), {}, {"FIXTURE_SECRET": SECRET_VALUE})
    blob = " ".join(r.detail + " " + r.remediation for r in e2.reasons)
    assert SECRET_VALUE not in blob        # the value never leaks
    assert "MISSING_NAME" in blob          # the missing var NAME is the actionable info
    assert "FIXTURE_SECRET" not in blob    # a satisfied var isn't mentioned at all


def test_precedence_disabled_beats_allowlist_and_always():
    # disabled wins even if listed in enabled and marked always
    s = _skill("x", metadata={"openclaw": {"always": True}})
    e = sp.evaluate(s, {"enabled": ["x"], "disabled": ["x"]}, {})
    assert not e.eligible and e.reasons[0].kind == sp.DISABLED


def test_toolset_shell_gate_follows_disabled_tools_env():
    s = _skill("t", metadata={"hermes": {"requires_toolsets": ["shell"]}})
    sp.reset_cache()
    assert sp.evaluate(s, {}, {}).eligible                       # shell available by default
    os.environ["OMEGACLAW_DISABLED_TOOLS"] = "shell"
    try:
        sp.reset_cache()
        assert not sp.evaluate(s, {}, {}).eligible               # now blocked
    finally:
        os.environ.pop("OMEGACLAW_DISABLED_TOOLS", None)
        sp.reset_cache()


def _mk(root, name, fm, body="body"):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\n{}\n---\n{}\n".format(fm, body))


def test_catalogue_advertises_only_eligible_with_readiness_note():
    sl.reset_cache(); sp.reset_cache()
    root = os.path.join(tempfile.mkdtemp(prefix="skpol_"), "skills")
    os.makedirs(root)
    _mk(root, "runnable", "name: runnable\ndescription: always ok")
    _mk(root, "needs-env", "name: needs-env\ndescription: needs a var\nrequired_environment_variables: [NEEDS_ENV_XYZ]")
    cfg = {"version": 1, "roots": [root]}
    block = sl.catalogue_block(cfg)
    assert "- runnable: always ok" in block            # eligible advertised
    assert "- needs-env:" not in block                  # blocked NOT advertised
    assert "SKILL_UNAVAILABLE:" in block and "needs-env" in block  # but flagged for setup
    # debug flag advertises everything
    os.environ["OMEGACLAW_SKILLS_DEBUG"] = "1"
    try:
        sl.reset_cache(); sp.reset_cache()
        assert "- needs-env:" in sl.catalogue_block(cfg)
    finally:
        os.environ.pop("OMEGACLAW_SKILLS_DEBUG", None)
        sl.reset_cache(); sp.reset_cache()


def test_doctor_report_structure():
    sl.reset_cache(); sp.reset_cache()
    root = os.path.join(tempfile.mkdtemp(prefix="skdoc_"), "skills")
    os.makedirs(root)
    _mk(root, "good", "name: good\ndescription: fine")
    _mk(root, "blocked", "name: blocked\ndescription: needs bin\nmetadata:\n  openclaw:\n    requires:\n      bins: [omegaclaw-no-such-bin-zzz]")
    _mk(root, "bad", "no frontmatter at all")
    cfg = {"version": 1, "roots": [root]}
    rep = sp.doctor(cfg)
    assert "good" in rep["eligible"]
    assert any(b["name"] == "blocked" for b in rep["blocked"])
    assert rep["counts"] == {"eligible": 1, "blocked": 1, "invalid": 1}
    blk = [b for b in rep["blocked"] if b["name"] == "blocked"][0]
    assert blk["reasons"] and blk["reasons"][0]["remediation"]
    sl.reset_cache(); sp.reset_cache()


def test_classify_cache_invalidates_on_config_value_flip():
    """Regression (PR #35 review): the cache keyed on config KEYS only, so a value flip
    (FEATURE false->true) returned a stale decision."""
    s = _skill("z", metadata={"openclaw": {"requires": {"config": ["FEATURE"]}}})
    sp.reset_cache()
    assert sp.classify([s], {"config": {"FEATURE": False}})[0].eligible is False
    assert sp.classify([s], {"config": {"FEATURE": True}})[0].eligible is True


def test_classify_cache_invalidates_on_entry_value_flip():
    s = _skill("z", envs=["MISSING_ONLY"])                 # blocked unless overridden
    sp.reset_cache()
    assert sp.classify([s], {"entries": {"z": {"always": False}}})[0].eligible is False
    assert sp.classify([s], {"entries": {"z": {"always": True}}})[0].eligible is True


def test_doctor_reports_not_allowlisted_and_disabled():
    """Regression: with load_skills no longer pre-filtering, doctor sees + explains the
    denied/not-allowlisted skills instead of them silently disappearing."""
    sl.reset_cache(); sp.reset_cache()
    root = os.path.join(tempfile.mkdtemp(prefix="skdoc2_"), "skills")
    os.makedirs(root)
    for n in ("keep", "denied", "stranger"):
        _mk(root, n, "name: {}\ndescription: {}".format(n, n))
    cfg = {"version": 1, "roots": [root], "enabled": ["keep"], "disabled": ["denied"]}
    rep = sp.doctor(cfg)
    assert rep["eligible"] == ["keep"]
    kinds = {b["name"]: b["reasons"][0]["kind"] for b in rep["blocked"]}
    assert kinds == {"denied": "disabled", "stranger": "not_allowlisted"}
    for b in rep["blocked"]:
        assert b["reasons"][0]["remediation"]
    sl.reset_cache(); sp.reset_cache()


def _skill(name, description="fixture", platforms=None, envs=None, metadata=None):
    return sl.Skill(name=name, description=description, version="1.0.0",
                    platforms=platforms or [], required_environment_variables=envs or [],
                    metadata=metadata or {}, base_dir="/tmp/" + name,
                    skill_file="/tmp/" + name + "/SKILL.md", body="body")


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
    print(f"\nAll {len(fns)} skill_policy tests passed")
