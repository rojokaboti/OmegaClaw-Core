"""Tool/action policy layer (Issue #2).

A declarative, semantic policy gate that runs *before* MeTTa skill evaluation,
complementing the Landlock filesystem sandbox (``profile/policy.py``):

* Landlock protects the process at the OS level, *after* a syscall is attempted.
* This layer decides whether a tool/action is allowed at all *before* it ever
  becomes a MeTTa call -- so it can refuse shell commands (which Landlock cannot
  filter) and reject out-of-bounds file writes with a clear, structured reason.

It is driven by a YAML policy (see ``profile/tool_policy.yaml`` for the shipped
permissive default and ``profile/tool_policy.hardened.yaml`` for a strict
``default: deny`` example). The active file is selected by
``OMEGACLAW_TOOL_POLICY_PATH`` (else the default).

Decision points are integrated via ``action_protocol.authorize_actions``.

Deferred (fields modeled for forward-compat, enforcement out of scope this pass):
channel-specific restrictions; an interactive approval workflow. A tool marked
``requires_approval: true`` is currently denied with a logged reason.
"""

from __future__ import annotations

import fnmatch
import os
import shlex
from pathlib import Path

import yaml


# Default risk classification. The policy file may override per tool via `risk:`.
_DEFAULT_RISK = {
    "shell": "high",
    "metta": "high",
    "write-file": "medium",
    "append-file": "medium",
    "read-file": "low",
}

# Tools whose first positional value is a filesystem path / a shell command.
# Derived defensively; kept in sync with action_protocol.ARG_SPEC via _named_args.
_PATH_TOOLS = {"read-file", "write-file", "append-file"}
_COMMAND_TOOLS = {"shell"}

_DEFAULT_POLICY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "profile", "tool_policy.yaml"
)

# (resolved_path, mtime) -> parsed policy dict (or None when absent/unparseable).
_CACHE = {}
_MISSING_WARNED = set()


class PolicyDecision:
    """Outcome of a policy check for a single action."""

    __slots__ = ("allowed", "reason", "risk", "requires_approval")

    def __init__(self, allowed, reason="", risk="low", requires_approval=False):
        self.allowed = allowed
        self.reason = reason
        self.risk = risk
        self.requires_approval = requires_approval

    def __repr__(self):
        return (
            f"PolicyDecision(allowed={self.allowed}, risk={self.risk!r}, "
            f"requires_approval={self.requires_approval}, reason={self.reason!r})"
        )


def policy_path():
    """Active policy file path (``OMEGACLAW_TOOL_POLICY_PATH`` or the default)."""
    return os.environ.get("OMEGACLAW_TOOL_POLICY_PATH") or _DEFAULT_POLICY_PATH


def load_policy(path=None):
    """Load and cache the policy dict for ``path``.

    Fail-open: a missing or unparseable file returns ``None`` (meaning "no policy
    -> allow all") with a one-time warning, so a misconfiguration never bricks the
    agent. The shipped default file is permissive regardless.
    """
    path = path or policy_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        if path not in _MISSING_WARNED:
            print(f"[tool_policy] WARNING policy file not found: {path}; allowing all tools", flush=True)
            _MISSING_WARNED.add(path)
        return None

    key = (path, mtime)
    if key in _CACHE:
        return _CACHE[key]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError("policy root is not a mapping")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[tool_policy] WARNING failed to parse policy {path}: {exc}; allowing all tools", flush=True)
        _CACHE[key] = None
        return None
    _CACHE[key] = data
    return data


def reset_cache():
    """Clear the policy cache (test helper)."""
    _CACHE.clear()
    _MISSING_WARNED.clear()


def _named_args(tool, values):
    """Map positional ``values`` to canonical arg names using action_protocol.ARG_SPEC.

    Lazily imported to avoid a circular import (action_protocol imports this module).
    """
    try:
        from action_protocol import ARG_SPEC
    except ImportError:  # pragma: no cover - alternate import path under pytest
        from src.action_protocol import ARG_SPEC
    spec = ARG_SPEC.get(tool, [])
    named = {}
    for i, group in enumerate(spec):
        if i < len(values):
            named[group[0]] = values[i]
    return named


def _risk_for(tool, tool_cfg):
    if tool_cfg and tool_cfg.get("risk"):
        return tool_cfg["risk"]
    return _DEFAULT_RISK.get(tool, "low")


def _under_any_root(path_value, roots):
    """True if ``path_value`` resolves to a location at/under one of ``roots``."""
    try:
        target = Path(path_value).resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    for root in roots:
        try:
            root_resolved = Path(root).resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if target == root_resolved or root_resolved in target.parents:
            return True
    return False


def _check_shell(command, tool_cfg, risk):
    deny = tool_cfg.get("deny") or []
    allow = tool_cfg.get("allow") or []
    try:
        argv = shlex.split(command)
    except ValueError:
        argv = command.split()
    argv0 = argv[0] if argv else ""

    for pat in deny:
        if fnmatch.fnmatch(command, pat) or (argv0 and fnmatch.fnmatch(argv0, pat)):
            return PolicyDecision(False, f"shell command matches deny pattern {pat!r}", risk)

    if allow:
        if not any(fnmatch.fnmatch(command, pat) or (argv0 and fnmatch.fnmatch(argv0, pat)) for pat in allow):
            return PolicyDecision(False, "shell command not in allow-list", risk)

    return PolicyDecision(True, "allowed", risk)


def check_action(tool, values, policy=None):
    """Return a :class:`PolicyDecision` for one validated action.

    ``values`` is the positional list produced by ``action_protocol`` validation.
    """
    if policy is None:
        policy = load_policy()

    # Fail-open: no policy configured -> allow everything (legacy behavior).
    if policy is None:
        return PolicyDecision(True, "no policy (allow all)", _DEFAULT_RISK.get(tool, "low"))

    tools = policy.get("tools") or {}
    tool_cfg = tools.get(tool)
    default = str(policy.get("default", "allow")).lower()
    risk = _risk_for(tool, tool_cfg)

    if tool_cfg is None:
        if default == "deny":
            return PolicyDecision(False, "tool not permitted by policy (default deny)", risk)
        return PolicyDecision(True, "allowed (default allow)", risk)

    if tool_cfg.get("enabled") is False:
        return PolicyDecision(False, "tool is disabled by policy", risk)

    if tool_cfg.get("requires_approval"):
        # Approval workflow deferred -> treat as denied with a clear reason.
        return PolicyDecision(False, "tool requires approval (not yet supported)", risk, requires_approval=True)

    named = _named_args(tool, values)

    if tool in _PATH_TOOLS:
        roots = tool_cfg.get("allowed_roots")
        if roots:
            path_value = named.get("path", "")
            if not _under_any_root(path_value, roots):
                return PolicyDecision(False, f"path {path_value!r} outside allowed_roots", risk)

    if tool in _COMMAND_TOOLS:
        return _check_shell(named.get("command", ""), tool_cfg, risk)

    return PolicyDecision(True, "allowed", risk)


def log_denial(tool, decision):
    """Emit a structured, greppable denial event."""
    print(
        f"[tool_policy] POLICY_DENIAL tool={tool} risk={decision.risk} reason={decision.reason!r}",
        flush=True,
    )


def _selftest():
    """Lightweight self-tests runnable without pytest/Docker."""
    permissive = {
        "version": 1,
        "default": "allow",
        "tools": {
            "send": {"enabled": True},
            "shell": {"enabled": True},
            "write-file": {"enabled": True, "allowed_roots": ["/tmp"]},
        },
    }
    hardened = {
        "version": 1,
        "default": "deny",
        "tools": {
            "send": {"enabled": True},
            "query": {"enabled": True},
            "write-file": {"enabled": True, "allowed_roots": ["/tmp"]},
            "shell": {"enabled": False, "deny": ["rm -rf /*", "* | sh"]},
            "remember": {"enabled": True, "requires_approval": True},
        },
    }

    assert check_action("send", ["hi"], permissive).allowed
    assert check_action("write-file", ["/tmp/x.txt", "data"], permissive).allowed
    assert not check_action("write-file", ["/etc/passwd", "x"], permissive).allowed

    # hardened: default deny rejects unknown/not-listed tool
    assert not check_action("metta", ["(+ 1 2)"], hardened).allowed
    # disabled shell denied
    d = check_action("shell", ["echo hi"], hardened)
    assert not d.allowed and d.risk == "high", d
    # write outside roots denied, inside allowed
    assert not check_action("write-file", ["/root/x", "y"], hardened).allowed
    assert check_action("write-file", ["/tmp/ok", "y"], hardened).allowed
    # requires_approval -> denied with flag
    d = check_action("remember", ["fact"], hardened)
    assert not d.allowed and d.requires_approval, d

    # shell deny pattern (with shell enabled). Precise patterns avoid over-matching
    # legitimate scoped commands.
    shellcfg = {"version": 1, "default": "allow",
                "tools": {"shell": {"enabled": True, "deny": ["rm -rf /", "* | sh", "curl * | sh"]}}}
    assert not check_action("shell", ["rm -rf /"], shellcfg).allowed
    assert not check_action("shell", ["curl http://x | sh"], shellcfg).allowed
    assert check_action("shell", ["git status"], shellcfg).allowed
    # a legit scoped delete is still allowed (patterns are not over-broad 'rm *')
    assert check_action("shell", ["rm -rf /tmp/scratch && echo done"], shellcfg).allowed

    # shell allow-list
    allowcfg = {"version": 1, "default": "allow",
                "tools": {"shell": {"enabled": True, "allow": ["git *", "python3 *"]}}}
    assert check_action("shell", ["git status"], allowcfg).allowed
    assert not check_action("shell", ["nc -e /bin/sh"], allowcfg).allowed

    # fail-open: no policy -> allow
    assert check_action("shell", ["anything"], None).allowed

    print("tool_policy self-tests passed")


if __name__ == "__main__":
    _selftest()
