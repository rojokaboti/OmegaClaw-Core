"""Skill eligibility gates & readiness diagnostics (Issue #13).

#11 loads every valid ``SKILL.md`` bundle and only *parses* its eligibility metadata.
This module decides whether a loaded skill is actually **runnable here** — matching how
OpenClaw and Hermes gate skills on OS, required binaries, env/config presence, tool
availability, and per-agent allow/deny — so the agent is only ever advertised skills it
can use (no "advertise a tool that can't run" failures), with actionable setup guidance
for the ones it cannot.

OpenClaw and Hermes metadata are normalized into ONE internal requirement schema:

  OpenClaw (``metadata.openclaw``):
    requires.env: [NAME, …]      -> every env var set & non-empty
    requires.bins: [bin, …]      -> every binary on PATH
    requires.anyBins: [bin, …]   -> at least one binary on PATH
    requires.config: [key, …]    -> every key present in the skills-config ``config`` map
    os: [linux|darwin|windows]   -> current OS in the list
    always: true                 -> always eligible (skip gates)
  Hermes:
    platforms: [linux|darwin|…]  -> merged into ``os``
    required_environment_variables: [NAME, …]  -> merged into ``requires.env``
    metadata.hermes.requires_toolsets: [name, …] -> every toolset's tools permitted

Precedence (highest first): ``disabled`` denylist -> per-skill ``entries[name].enabled:false``
-> ``enabled`` allowlist miss -> requirement gates (short-circuited by ``always`` / an
``entries[name].always`` override).

**Secret safety (KPI):** only env-var *names* and presence booleans ever appear in reasons,
logs, or cache keys — never a value.
"""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:  # secret-safe logging; and the tool availability gate reuses tool_policy
    from redaction import redact_secrets
except ImportError:  # pragma: no cover
    from src.redaction import redact_secrets

try:
    import tool_policy as _tool_policy
except ImportError:  # pragma: no cover
    try:
        from src import tool_policy as _tool_policy
    except ImportError:  # pragma: no cover
        _tool_policy = None

# Named toolsets -> the OmegaClaw tools (helper.LLM_COMMANDS) they stand for. A toolset is
# satisfied when ALL its tools are permitted under the active tool policy. Unknown toolset
# names are treated as unsatisfied (fail-safe: don't advertise a skill needing an unknown
# capability) with an explicit remediation.
_TOOLSETS: Dict[str, tuple] = {
    "files": ("read-file", "write-file", "append-file"),
    "filesystem": ("read-file", "write-file", "append-file"),
    "shell": ("shell",),
    "web": ("search", "tavily-search"),
    "search": ("search",),
    "memory": ("remember", "query", "remember-claim", "query-claims", "episodes", "pin"),
    "reasoning": ("metta", "metta-session-create", "metta-session-add", "metta-session-infer"),
}

# Reason kinds (stable identifiers for tests / doctor rendering).
MISSING_ENV = "missing_env"
MISSING_BIN = "missing_bin"
MISSING_ANYBIN = "missing_anybin"
MISSING_CONFIG = "missing_config"
OS_MISMATCH = "os_mismatch"
MISSING_TOOLSET = "missing_toolset"
DISABLED = "disabled"
NOT_ALLOWLISTED = "not_allowlisted"


@dataclass
class Reason:
    kind: str
    detail: str          # human-readable, secret-free
    remediation: str     # actionable, secret-free


@dataclass
class Eligibility:
    name: str
    eligible: bool
    reasons: List[Reason] = field(default_factory=list)


# --------------------------------------------------------------------------- OS

def current_os() -> str:
    sysname = platform.system().lower()
    if sysname.startswith("darwin"):
        return "darwin"
    if sysname.startswith("win"):
        return "windows"
    if sysname.startswith("linux"):
        return "linux"
    return sysname or "unknown"


def _norm_os_token(tok: str) -> str:
    t = str(tok).strip().lower()
    return "darwin" if t in ("macos", "mac", "osx", "darwin") else ("windows" if t in ("win", "windows") else t)


# --------------------------------------------------------------------------- requirements

def _as_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [str(v)]


def normalize_requirements(skill) -> Dict[str, Any]:
    """Merge OpenClaw + Hermes metadata on a Skill into one requirement dict."""
    md = skill.metadata if isinstance(getattr(skill, "metadata", None), dict) else {}
    oc = md.get("openclaw") if isinstance(md.get("openclaw"), dict) else {}
    hm = md.get("hermes") if isinstance(md.get("hermes"), dict) else {}
    oc_req = oc.get("requires") if isinstance(oc.get("requires"), dict) else {}

    os_list = [_norm_os_token(x) for x in (_as_list(oc.get("os")) + list(getattr(skill, "platforms", []) or []))]
    env = _as_list(oc_req.get("env")) + list(getattr(skill, "required_environment_variables", []) or [])
    toolsets = _as_list(hm.get("requires_toolsets")) + _as_list(oc_req.get("toolsets"))
    always = bool(oc.get("always") or hm.get("always"))

    return {
        "os": [o for o in os_list if o],
        "env": list(dict.fromkeys(env)),          # de-dup, order-preserving
        "bins": _as_list(oc_req.get("bins")),
        "anyBins": _as_list(oc_req.get("anyBins")),
        "config": _as_list(oc_req.get("config")),
        "toolsets": list(dict.fromkeys(toolsets)),
        "always": always,
    }


def _env_present(name: str, env: Dict[str, str]) -> bool:
    return bool((env.get(name) or "").strip())


def _tool_available(tool: str) -> bool:
    """Whether an OmegaClaw tool is permitted under the active tool policy.

    Mirrors tool_policy's enabled/default decision (without the path/shell arg specifics,
    since this is an availability check, not an action check)."""
    disabled = {t.strip() for t in (os.environ.get("OMEGACLAW_DISABLED_TOOLS") or "").split(",") if t.strip()}
    if tool in disabled:
        return False
    if _tool_policy is None:
        return True
    policy = _tool_policy.load_policy()
    if policy is None:
        return True                       # no policy configured -> allow all
    if policy is getattr(_tool_policy, "_DENY_ALL", object()):
        return False                      # explicit-but-unloadable -> fail closed
    tools = policy.get("tools") if isinstance(policy, dict) else None
    entry = tools.get(tool) if isinstance(tools, dict) else None
    if isinstance(entry, dict):
        return entry.get("enabled", True) is not False
    return str(policy.get("default", "allow")).lower() != "deny"


def _config_map(cfg: Dict[str, Any]) -> Dict[str, Any]:
    c = cfg.get("config") if isinstance(cfg, dict) else None
    return c if isinstance(c, dict) else {}


def evaluate(skill, cfg: Optional[Dict[str, Any]] = None, env: Optional[Dict[str, str]] = None) -> Eligibility:
    """Evaluate one skill's eligibility. Pure w.r.t. (skill, cfg, env); no side effects."""
    cfg = cfg or {}
    env = env if env is not None else os.environ
    name = skill.name

    # ---- precedence: denylist / entry override / allowlist (before requirement gates) ----
    entries = cfg.get("entries") if isinstance(cfg.get("entries"), dict) else {}
    entry = entries.get(name) if isinstance(entries.get(name), dict) else {}
    disabled = set(cfg.get("disabled") or [])
    enabled = cfg.get("enabled")

    if name in disabled or entry.get("enabled") is False:
        return Eligibility(name, False, [Reason(
            DISABLED, "skill is disabled by policy",
            "remove '{}' from 'disabled' (or set entries.{}.enabled: true) in profile/skills.yaml".format(name, name))])
    if isinstance(enabled, list) and name not in enabled and not entry.get("enabled"):
        return Eligibility(name, False, [Reason(
            NOT_ALLOWLISTED, "skill is not in the 'enabled' allowlist",
            "add '{}' to 'enabled' in profile/skills.yaml".format(name))])

    # ---- always override skips requirement gates ----
    req = normalize_requirements(skill)
    if req["always"] or entry.get("always"):
        return Eligibility(name, True, [])

    reasons: List[Reason] = []

    # OS
    if req["os"]:
        cur = current_os()
        if cur not in req["os"]:
            reasons.append(Reason(OS_MISMATCH,
                                   "requires OS {}, current is {}".format(req["os"], cur),
                                   "run on one of: {}".format(", ".join(req["os"]))))
    # required env (names + presence only; never values)
    missing_env = [n for n in req["env"] if not _env_present(n, env)]
    if missing_env:
        reasons.append(Reason(MISSING_ENV,
                              "missing/empty env var(s): {}".format(", ".join(missing_env)),
                              "set: {}".format(", ".join(missing_env))))
    # required bins
    missing_bins = [b for b in req["bins"] if shutil.which(b) is None]
    if missing_bins:
        reasons.append(Reason(MISSING_BIN,
                              "missing binary(ies) on PATH: {}".format(", ".join(missing_bins)),
                              "install / add to PATH: {}".format(", ".join(missing_bins))))
    # anyBins: at least one present
    if req["anyBins"] and not any(shutil.which(b) is not None for b in req["anyBins"]):
        reasons.append(Reason(MISSING_ANYBIN,
                              "none of these binaries on PATH: {}".format(", ".join(req["anyBins"])),
                              "install at least one of: {}".format(", ".join(req["anyBins"]))))
    # required config keys
    conf = _config_map(cfg)
    missing_cfg = [k for k in req["config"] if not conf.get(k)]
    if missing_cfg:
        reasons.append(Reason(MISSING_CONFIG,
                              "missing config key(s): {}".format(", ".join(missing_cfg)),
                              "set under 'config' in profile/skills.yaml: {}".format(", ".join(missing_cfg))))
    # toolsets
    bad_toolsets = []
    for ts in req["toolsets"]:
        tools = _TOOLSETS.get(str(ts).strip().lower())
        if tools is None:
            reasons.append(Reason(MISSING_TOOLSET,
                                  "unknown toolset '{}'".format(ts),
                                  "map toolset '{}' or remove it from the skill".format(ts)))
        elif not all(_tool_available(t) for t in tools):
            unavailable = [t for t in tools if not _tool_available(t)]
            bad_toolsets.append(ts)
            reasons.append(Reason(MISSING_TOOLSET,
                                  "toolset '{}' unavailable (need tools: {})".format(ts, ", ".join(unavailable)),
                                  "enable tool(s): {} (tool policy / OMEGACLAW_DISABLED_TOOLS)".format(", ".join(unavailable))))

    return Eligibility(name, not reasons, reasons)


# --------------------------------------------------------------------------- doctor / caching

def _relevant_env_fingerprint(skills, env: Dict[str, str]):
    """A cache fingerprint over ONLY the presence of env vars any skill requires (never
    values), so eligibility invalidates when a relevant env var appears/disappears."""
    names = set()
    for s in skills:
        names.update(normalize_requirements(s)["env"])
    return tuple(sorted((n, _env_present(n, env)) for n in names))


_CACHE: Dict[Any, Any] = {}


def classify(skills: List[Any], cfg: Optional[Dict[str, Any]] = None,
             env: Optional[Dict[str, str]] = None) -> List[Eligibility]:
    """Evaluate a list of skills. Cached by (skill names, relevant-env presences, a config
    policy fingerprint) so repeated prompt builds are cheap but invalidate on real change."""
    cfg = cfg or {}
    env = env if env is not None else os.environ
    key = (
        tuple(sorted(s.name for s in skills)),
        _relevant_env_fingerprint(skills, env),
        tuple(sorted((cfg.get("enabled") or []))) if isinstance(cfg.get("enabled"), list) else None,
        tuple(sorted(cfg.get("disabled") or [])),
        tuple(sorted((cfg.get("config") or {}).keys())),
        tuple(sorted((cfg.get("entries") or {}).keys())),
        os.environ.get("OMEGACLAW_DISABLED_TOOLS", ""),
    )
    if key in _CACHE:
        return _CACHE[key]
    result = [evaluate(s, cfg, env) for s in sorted(skills, key=lambda s: s.name)]
    _CACHE[key] = result
    return result


def reset_cache() -> None:
    _CACHE.clear()


def doctor(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Structured readiness report over all discovered skills (for the `skills doctor` CLI).

    Loads skills via skill_loader, classifies them, and returns eligible/blocked (with
    secret-free reasons + remediation) plus invalid-bundle parse errors."""
    try:
        import skill_loader
    except ImportError:  # pragma: no cover
        from src import skill_loader
    cfg = cfg if cfg is not None else skill_loader.load_config()
    skills, errors = skill_loader.load_skills(cfg)
    elig = classify(list(skills.values()), cfg)
    eligible = [e.name for e in elig if e.eligible]
    blocked = [{"name": e.name,
                "reasons": [{"kind": r.kind, "detail": r.detail, "remediation": r.remediation} for r in e.reasons]}
               for e in elig if not e.eligible]
    return {
        "os": current_os(),
        "eligible": sorted(eligible),
        "blocked": blocked,
        "errors": [{"path": err.path, "message": err.message} for err in errors],
        "counts": {"eligible": len(eligible), "blocked": len(blocked), "invalid": len(errors)},
    }


# --------------------------------------------------------------------------- selftest

def _selftest() -> None:
    from dataclasses import dataclass as _dc

    @_dc
    class _S:
        name: str
        platforms: list
        required_environment_variables: list
        metadata: dict

    def mk(name, platforms=None, envs=None, md=None):
        return _S(name, platforms or [], envs or [], md or {})

    reset_cache()
    os.environ.pop("OMEGACLAW_DISABLED_TOOLS", None)
    env = {"PRESENT_KEY": "x"}

    # env gate: missing var blocks, name (not value) in reason; present var passes.
    e = evaluate(mk("a", envs=["MISSING_KEY"]), {}, env)
    assert not e.eligible and e.reasons[0].kind == MISSING_ENV and "MISSING_KEY" in e.reasons[0].detail
    e = evaluate(mk("b", envs=["PRESENT_KEY"]), {}, env)
    assert e.eligible, e

    # secret safety: a secret VALUE never leaks into reasons.
    e = evaluate(mk("c", envs=["SECRET_TOKEN"]), {}, {"OTHER": "y"})
    blob = " ".join(r.detail + r.remediation for r in e.reasons)
    assert "SECRET_TOKEN" in blob                       # the NAME is fine
    e2 = evaluate(mk("c", envs=["SECRET_TOKEN"]), {}, {"SECRET_TOKEN": "sk-ant-abcd1234efgh5678"})
    assert e2.eligible  # present -> eligible, and we never render the value anywhere

    # bins: missing binary blocks.
    e = evaluate(mk("d", md={"openclaw": {"requires": {"bins": ["definitely-not-a-real-bin-xyz"]}}}), {}, env)
    assert not e.eligible and e.reasons[0].kind == MISSING_BIN
    # a bin that exists (python3) passes.
    e = evaluate(mk("e", md={"openclaw": {"requires": {"bins": ["sh"]}}}), {}, env)
    assert e.eligible, e
    # anyBins: at least one present.
    e = evaluate(mk("f", md={"openclaw": {"requires": {"anyBins": ["sh", "nope-xyz"]}}}), {}, env)
    assert e.eligible, e

    # os gate.
    other = "windows" if current_os() != "windows" else "linux"
    assert not evaluate(mk("g", platforms=[other]), {}, env).eligible
    assert evaluate(mk("h", platforms=[current_os()]), {}, env).eligible

    # config gate.
    e = evaluate(mk("i", md={"openclaw": {"requires": {"config": ["FEATURE_X"]}}}), {"config": {}}, env)
    assert not e.eligible and e.reasons[0].kind == MISSING_CONFIG
    assert evaluate(mk("i", md={"openclaw": {"requires": {"config": ["FEATURE_X"]}}}),
                    {"config": {"FEATURE_X": True}}, env).eligible

    # toolsets: files available by default; unknown toolset blocks.
    assert evaluate(mk("j", md={"hermes": {"requires_toolsets": ["files"]}}), {}, env).eligible
    e = evaluate(mk("k", md={"hermes": {"requires_toolsets": ["quantum"]}}), {}, env)
    assert not e.eligible and e.reasons[0].kind == MISSING_TOOLSET
    # disabling shell blocks the shell toolset.
    os.environ["OMEGACLAW_DISABLED_TOOLS"] = "shell"
    try:
        assert not evaluate(mk("l", md={"hermes": {"requires_toolsets": ["shell"]}}), {}, env).eligible
    finally:
        os.environ.pop("OMEGACLAW_DISABLED_TOOLS", None)

    # precedence: disabled beats everything; allowlist miss blocks; always overrides gates.
    assert not evaluate(mk("m"), {"disabled": ["m"]}, env).eligible
    assert not evaluate(mk("n"), {"enabled": ["other"]}, env).eligible
    assert evaluate(mk("o", envs=["MISSING_KEY"], md={"openclaw": {"always": True}}), {}, env).eligible
    # entries override forces enable past an allowlist miss.
    assert evaluate(mk("p"), {"enabled": ["other"], "entries": {"p": {"enabled": True}}}, env).eligible

    reset_cache()
    print("skill_policy self-tests passed")


if __name__ == "__main__":
    _selftest()
