"""Deterministic eligibility fixture matrix for the Issue #13 benchmark & tests.

Each fixture is a SKILL.md-shaped bundle with a KNOWN expected outcome under a controlled
(env, cfg) — one per gate (OS / env / bins / anyBins / config / toolset) in both the passing
and failing direction, plus the precedence cases (disabled / allowlist / entries / always) and
a secret-value-never-leaks case.

Binaries use ``sh`` (present on any POSIX host / the CI container) as the "present" case and a
nonsense name as the "absent" case, so the matrix is deterministic without mutating PATH.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
for _p in (_SRC, os.path.dirname(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import skill_loader  # noqa: E402
import skill_policy  # noqa: E402

_ABSENT_BIN = "omegaclaw-no-such-bin-zzz"
_PRESENT_BIN = "sh"
SECRET_VALUE = "sk-ant-fixture-SECRET-do-not-print-abc123"


def _skill(name, description="fixture skill", platforms=None, envs=None, metadata=None):
    return skill_loader.Skill(
        name=name, description=description, version="1.0.0",
        platforms=platforms or [], required_environment_variables=envs or [],
        metadata=metadata or {}, base_dir="/tmp/" + name, skill_file="/tmp/" + name + "/SKILL.md",
        body="body",
    )


def matrix():
    """Return a list of fixtures: {skill, cfg, env, expect_eligible, expect_kinds}."""
    cur = skill_policy.current_os()
    other = "windows" if cur != "windows" else "linux"
    oc = lambda req: {"openclaw": {"requires": req}}  # noqa: E731

    fx = [
        # --- passing direction (expect eligible) ---
        dict(skill=_skill("ok-none"), cfg={}, env={}, expect_eligible=True, expect_kinds=set()),
        dict(skill=_skill("ok-env", envs=["PRESENT_VAR"]), cfg={}, env={"PRESENT_VAR": "1"},
             expect_eligible=True, expect_kinds=set()),
        dict(skill=_skill("ok-bin", metadata=oc({"bins": [_PRESENT_BIN]})), cfg={}, env={},
             expect_eligible=True, expect_kinds=set()),
        dict(skill=_skill("ok-anybin", metadata=oc({"anyBins": [_ABSENT_BIN, _PRESENT_BIN]})), cfg={}, env={},
             expect_eligible=True, expect_kinds=set()),
        dict(skill=_skill("ok-os", platforms=[cur]), cfg={}, env={}, expect_eligible=True, expect_kinds=set()),
        dict(skill=_skill("ok-config", metadata=oc({"config": ["FEATURE_X"]})),
             cfg={"config": {"FEATURE_X": True}}, env={}, expect_eligible=True, expect_kinds=set()),
        dict(skill=_skill("ok-toolset", metadata={"hermes": {"requires_toolsets": ["files"]}}),
             cfg={}, env={}, expect_eligible=True, expect_kinds=set()),
        dict(skill=_skill("ok-always", envs=["MISSING_VAR"], metadata={"openclaw": {"always": True}}),
             cfg={}, env={}, expect_eligible=True, expect_kinds=set()),
        dict(skill=_skill("ok-entry"), cfg={"enabled": ["other"], "entries": {"ok-entry": {"enabled": True}}},
             env={}, expect_eligible=True, expect_kinds=set()),
        # secret value present -> eligible, and the VALUE must never leak (checked by benchmark)
        dict(skill=_skill("ok-secret", envs=["FIXTURE_SECRET"]), cfg={}, env={"FIXTURE_SECRET": SECRET_VALUE},
             expect_eligible=True, expect_kinds=set()),

        # --- failing direction (expect blocked, with the specific reason kind) ---
        dict(skill=_skill("no-env", envs=["MISSING_VAR"]), cfg={}, env={},
             expect_eligible=False, expect_kinds={skill_policy.MISSING_ENV}),
        dict(skill=_skill("no-bin", metadata=oc({"bins": [_ABSENT_BIN]})), cfg={}, env={},
             expect_eligible=False, expect_kinds={skill_policy.MISSING_BIN}),
        dict(skill=_skill("no-anybin", metadata=oc({"anyBins": [_ABSENT_BIN]})), cfg={}, env={},
             expect_eligible=False, expect_kinds={skill_policy.MISSING_ANYBIN}),
        dict(skill=_skill("no-os", platforms=[other]), cfg={}, env={},
             expect_eligible=False, expect_kinds={skill_policy.OS_MISMATCH}),
        dict(skill=_skill("no-config", metadata=oc({"config": ["FEATURE_X"]})), cfg={"config": {}}, env={},
             expect_eligible=False, expect_kinds={skill_policy.MISSING_CONFIG}),
        dict(skill=_skill("no-toolset", metadata={"hermes": {"requires_toolsets": ["quantum"]}}),
             cfg={}, env={}, expect_eligible=False, expect_kinds={skill_policy.MISSING_TOOLSET}),
        dict(skill=_skill("no-disabled"), cfg={"disabled": ["no-disabled"]}, env={},
             expect_eligible=False, expect_kinds={skill_policy.DISABLED}),
        dict(skill=_skill("no-allowlist"), cfg={"enabled": ["other"]}, env={},
             expect_eligible=False, expect_kinds={skill_policy.NOT_ALLOWLISTED}),
    ]
    return fx


if __name__ == "__main__":
    m = matrix()
    print("fixtures:", len(m), "| eligible:", sum(1 for f in m if f["expect_eligible"]),
          "| blocked:", sum(1 for f in m if not f["expect_eligible"]))
