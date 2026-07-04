"""Structured JSON action protocol (Issue #1).

Inserts a validated boundary between raw LLM output and MeTTa skill evaluation.
The LLM is asked to emit JSON of the shape::

    {"version": 1, "actions": [{"tool": "send", "args": {"text": "Done"}}]}

(The ``version`` field is optional; a bare ``{"actions": [...]}`` or a bare list
is treated as version 1.)

This module runs an explicit pipeline:

    parse  -> _try_json / parse_actions   (raw text -> structured actions)
    validate -> validate_action           (known tool + per-tool arg schema)
    authorize -> authorize_actions         (policy gate, e.g. disabled tools)
    render -> actions_to_metta             (-> the s-expression ``sread`` expects)

Execution semantics:

* Actions render (and are evaluated by the loop) in the order listed.
* **All-or-nothing**: in strict ``json`` mode, if *any* action fails validation
  or authorization, or the batch exceeds ``OMEGACLAW_MAX_ACTIONS``, the entire
  batch is rejected with a structured error and the model is re-prompted. There
  is no partial execution and no silent truncation.
* Every failure produces a structured error ``{"code", "message"}``.

The legacy heuristic parser ``helper.balance_parentheses`` is retained and used
only when the operating mode is ``legacy`` or ``auto`` (and no JSON is present).

Environment:

* ``OMEGACLAW_ACTION_PROTOCOL`` = ``json`` (default, strict) | ``auto`` (JSON,
  else legacy fallback) | ``legacy`` (legacy parser only).
* ``OMEGACLAW_MAX_ACTIONS`` = max actions per turn (default 5).
* ``OMEGACLAW_DISABLED_TOOLS`` = comma-separated tool names to refuse (default
  none = allow all). Intended to gate high-risk escape hatches (``shell``,
  ``metta``) in restricted deployments or tests.
"""

from __future__ import annotations

import json
import os
import re

try:  # registered to MeTTa as a flat module ("helper"), but also a package member
    from helper import LLM_COMMANDS, balance_parentheses
except ImportError:  # pragma: no cover - import path differs under pytest from repo root
    from src.helper import LLM_COMMANDS, balance_parentheses

try:  # tool/action policy gate (Issue #2). Lazy-safe: no circular import at load.
    import tool_policy
except ImportError:  # pragma: no cover - alternate import path under pytest
    from src import tool_policy


# Protocol envelope version understood by this implementation.
PROTOCOL_VERSION = 1

# Single source of truth for valid tool names.
ALLOWED_TOOLS = set(LLM_COMMANDS)

# Escape-hatch tools that bypass much of the structured-action safety benefit.
# They remain available by default but are flagged in the prompt and are the
# primary candidates for OMEGACLAW_DISABLED_TOOLS in restricted deployments.
# The session tools reach the same sread/eval path as `metta` (infer evaluates,
# add stores expressions that infer later evaluates), so they share metta's risk.
_METTA_EVAL_TOOLS = {"metta-session-infer", "metta-session-add"}
HIGH_RISK_TOOLS = {"shell", "metta"} | _METTA_EVAL_TOOLS


def _max_actions():
    """Max actions accepted per turn (OMEGACLAW_MAX_ACTIONS, default 5)."""
    raw = os.environ.get("OMEGACLAW_MAX_ACTIONS")
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return n
        except ValueError:
            pass
    return 5


# Preserves the historical "Up to 5 lines" guidance from the old text protocol.
MAX_ACTIONS = _max_actions()

# Ordered argument keys per tool, with accepted aliases. The first name in each
# list is the canonical key advertised in the prompt; later names are tolerated
# so a slightly-off model output still validates instead of being rejected.
ARG_SPEC = {
    "send": [("text",)],
    "query": [("text",)],
    "query-claims": [("text",)],
    "search": [("text",)],
    "remember": [("text",)],
    "remember-claim": [("claim", "text")],
    "pin": [("text",)],
    "tavily-search": [("text", "query")],
    "technical-analysis": [("ticker", "symbol", "text")],
    "episodes": [("time", "text")],
    "read-file": [("path", "file", "filename")],
    "shell": [("command", "cmd", "text")],
    "metta": [("expr", "code", "text")],
    "write-file": [("path", "file", "filename"), ("content", "text", "str")],
    "append-file": [("path", "file", "filename"), ("content", "text", "str")],
    # FreeCiv benchmark tools (Issue #6). observe takes no args (reads current game state);
    # action takes a single JSON string describing the candidate move.
    "freeciv-observe": [],
    "freeciv-action": [("action", "action_json", "text")],
    # Session-scoped reasoning (Issue #8). create/clear/snapshot take a session id;
    # add/infer take a session id + a premise/query expression.
    "metta-session-create": [("session", "sid", "text")],
    "metta-session-clear": [("session", "sid", "text")],
    "metta-session-snapshot": [("session", "sid", "text")],
    "metta-session-add": [("session", "sid"), ("expr", "code", "text")],
    "metta-session-infer": [("session", "sid"), ("expr", "code", "text")],
}

DEFAULT_MODE = "json"
_VALID_MODES = {"json", "auto", "legacy"}

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _err(code, message, warning=False):
    """Build a structured error object. ``warning`` errors are non-fatal."""
    e = {"code": code, "message": message}
    if warning:
        e["warning"] = True
    return e


def _messages(errors):
    """Join structured errors into a single human-readable string."""
    return "; ".join(e["message"] for e in errors)


class ActionParseResult:
    """Outcome of parsing raw LLM output into validated actions.

    Attributes:
        ok: True when a complete, valid batch of >=1 action was produced.
        actions: list of validated ``{"tool", "values"}`` dicts (empty if not ok).
        errors: list of structured ``{"code", "message"[, "warning"]}`` objects.
        source: one of ``"json"``, ``"json-fenced"``, ``"none"``.
        version: the envelope version seen (``None`` if not specified).
    """

    __slots__ = ("ok", "actions", "errors", "source", "version")

    def __init__(self, ok, actions, errors, source, version=None):
        self.ok = ok
        self.actions = actions
        self.errors = errors
        self.source = source
        self.version = version

    def __repr__(self):
        return (
            f"ActionParseResult(ok={self.ok}, source={self.source!r}, "
            f"version={self.version!r}, actions={self.actions!r}, errors={self.errors!r})"
        )


def _coerce_actions_container(data):
    """Return ``(actions_list_or_None, version_or_None)`` from a parsed document.

    Accepts ``{"version": N, "actions": [...]}``, ``{"actions": [...]}`` (implicit
    version), or a bare top-level list (implicit version).
    """
    if isinstance(data, dict) and isinstance(data.get("actions"), list):
        return data["actions"], data.get("version")
    if isinstance(data, list):
        return data, None
    return None, None


def _try_json(raw):
    """Extract ``(actions_or_None, source, version)`` from ``raw``.

    Tries a strict parse first, then a fenced ```json``` block. ``source`` is
    ``"json"``, ``"json-fenced"`` or ``"none"``.
    """
    text = raw.strip() if isinstance(raw, str) else ""
    if text:
        try:
            container, version = _coerce_actions_container(json.loads(text))
            if container is not None:
                return container, "json", version
        except (ValueError, TypeError):
            pass

    for match in _FENCE_RE.finditer(raw or ""):
        try:
            container, version = _coerce_actions_container(json.loads(match.group(1)))
            if container is not None:
                return container, "json-fenced", version
        except (ValueError, TypeError):
            continue

    return None, "none", None


def validate_action(action):
    """Validate a single raw action dict.

    Returns ``(ordered_args, None)`` on success where ``ordered_args`` is the
    list of string argument values in tool-declared order, or
    ``(None, error_obj)`` with a structured error on failure.
    """
    if not isinstance(action, dict):
        return None, _err("bad_action", f"action is not an object: {action!r}")

    tool = action.get("tool")
    if not isinstance(tool, str) or not tool:
        return None, _err("missing_tool", "action missing string 'tool'")
    if tool not in ALLOWED_TOOLS:
        return None, _err("unknown_tool", f"unknown tool: {tool!r}")

    args = action.get("args", {})
    if not isinstance(args, dict):
        return None, _err("bad_args", f"{tool}: 'args' must be an object")

    ordered = []
    for alias_group in ARG_SPEC[tool]:
        value = None
        found = False
        for key in alias_group:
            if key in args:
                value = args[key]
                found = True
                break
        if not found:
            return None, _err("missing_arg", f"{tool}: missing required arg '{alias_group[0]}'")
        if not isinstance(value, str):
            return None, _err("bad_arg_type", f"{tool}: arg '{alias_group[0]}' must be a string")
        ordered.append(value)

    return ordered, None


def parse_actions(raw):
    """Parse raw LLM output into an :class:`ActionParseResult` (all-or-nothing).

    Only JSON sources are considered here; legacy fallback is the caller's
    responsibility (see :func:`parse_and_render_metta`). A single invalid action,
    or a batch larger than the action cap, rejects the whole batch.
    """
    container, source, version = _try_json(raw)
    if container is None:
        return ActionParseResult(False, [], [_err("no_json", "no JSON actions found")], "none", None)

    errors = []
    if version is not None and str(version) != str(PROTOCOL_VERSION):
        # Lenient: record a warning but still process (avoids breaking otherwise
        # valid output when the schema version drifts).
        errors.append(_err(
            "unsupported_version",
            f"protocol version {version!r} not recognized (supported: {PROTOCOL_VERSION}); processing leniently",
            warning=True,
        ))

    limit = _max_actions()
    if len(container) > limit:
        errors.append(_err("too_many_actions", f"{len(container)} actions exceeds max {limit}"))

    actions = []
    for item in container:
        ordered, err = validate_action(item)
        if err is not None:
            errors.append(err)
            continue
        actions.append({"tool": item["tool"], "values": ordered})

    # All-or-nothing: any non-warning error invalidates the entire batch.
    hard_errors = [e for e in errors if not e.get("warning")]
    ok = not hard_errors and bool(actions)
    if not ok:
        actions = []
    return ActionParseResult(ok, actions, errors, source, version)


def _disabled_tools():
    """Set of tool names refused by policy (OMEGACLAW_DISABLED_TOOLS)."""
    raw = os.environ.get("OMEGACLAW_DISABLED_TOOLS") or ""
    return {t.strip() for t in raw.split(",") if t.strip()}


def authorize_actions(actions):
    """Policy gate run after validation, before rendering.

    Two layers, all-or-nothing (any denial refuses the whole batch):

    1. ``OMEGACLAW_DISABLED_TOOLS`` -- a quick env allowlist override.
    2. The declarative tool/action policy (``tool_policy.check_action``) which
       enforces per-tool enable/disable, file ``allowed_roots``, and shell
       allow/deny. The shipped default policy is permissive, so behavior is
       preserved unless an operator selects a stricter ``OMEGACLAW_TOOL_POLICY_PATH``.

    Returns ``(authorized_actions, errors)``.
    """
    disabled = _disabled_tools()
    # Disabling `metta` also disables the evaluator-bearing session tools: they reach the
    # same sread/eval surface, so a deployment that gates `metta` must not be bypassed
    # through metta-session-infer / metta-session-add.
    if "metta" in disabled:
        disabled = disabled | _METTA_EVAL_TOOLS
    errors = []
    for a in actions:
        if a["tool"] in disabled:
            errors.append(_err("tool_disabled", f"tool {a['tool']!r} is disabled by policy"))
            try:  # reasoning trace (Issue #7); best-effort
                import tracing
                tracing.trace_policy(a["tool"], allowed=False,
                                     reason="disabled by OMEGACLAW_DISABLED_TOOLS", risk="n/a")
            except Exception:  # noqa: BLE001
                pass
            continue
        decision = tool_policy.check_action(a["tool"], a["values"])
        if not decision.allowed:
            tool_policy.log_denial(a["tool"], decision)
            errors.append(_err(
                "policy_denied",
                f"{a['tool']}: {decision.reason} (risk={decision.risk})",
            ))
    if errors:
        return [], errors
    return actions, []


def actions_to_metta(actions):
    """Render validated actions (from :func:`parse_actions`) into the
    ``sread``-shaped s-expression string, e.g. ``((send "hi") (pin "x"))``.

    String arguments are emitted via ``json.dumps`` so quoting and ``\\n``/``\\"``
    escaping (and unicode) match exactly what ``helper.balance_parentheses`` produces.
    """
    sexprs = []
    for action in actions:
        rendered_args = " ".join(
            json.dumps(value, ensure_ascii=False) for value in action["values"]
        )
        if rendered_args:
            sexprs.append(f"({action['tool']} {rendered_args})")
        else:
            sexprs.append(f"({action['tool']})")
    return "(" + " ".join(sexprs) + ")"


def get_mode():
    """Return the active protocol mode, defaulting to ``json``."""
    mode = (os.environ.get("OMEGACLAW_ACTION_PROTOCOL") or DEFAULT_MODE).strip().lower()
    return mode if mode in _VALID_MODES else DEFAULT_MODE


def output_format_block():
    """Return the OUTPUT_FORMAT instruction text for the system prompt.

    Generated from :data:`ARG_SPEC` so the advertised tool keys never drift from
    what the parser actually validates.
    """
    lines = []
    for tool in sorted(ALLOWED_TOOLS):
        keys = ", ".join(group[0] for group in ARG_SPEC[tool])
        lines.append(f"{tool}{{{keys}}}")
    tools = "; ".join(lines)
    high_risk = ", ".join(sorted(HIGH_RISK_TOOLS))
    return (
        "OUTPUT_FORMAT: Reply with ONLY a single JSON object, no prose, no code fences, "
        f"at most {_max_actions()} actions: "
        '{"version":1,"actions":[{"tool":"<name>","args":{...}}]} . '
        f"Allowed tools and their args: {tools}. "
        f"High-risk tools (use only when necessary): {high_risk}."
    )


def _log_fallback(raw):
    """Emit a greppable marker so legacy-fallback usage can be counted/trended."""
    snippet = (raw or "")[:60].replace("\n", " ")
    print(f"[action_protocol] FALLBACK_TO_LEGACY no JSON detected; raw[:60]={snippet!r}", flush=True)


def _log_warnings(errors):
    for e in errors:
        if e.get("warning"):
            print(f"[action_protocol] WARNING {e['code']}: {e['message']}", flush=True)


def _emit_parse_trace(result):
    """Emit an ``action_parse`` reasoning-trace event (Issue #7). Best-effort."""
    try:
        import tracing
        tools = [a.get("tool") for a in (result.actions or [])]
        codes = [e.get("code") for e in (result.errors or []) if not e.get("warning")]
        tracing.trace_parse(ok=result.ok, source=result.source, version=result.version,
                            tools=tools, error_codes=codes)
    except Exception:  # noqa: BLE001 - tracing must never break the action pipeline
        pass


def parse_and_render_metta(raw):
    """MeTTa entry point: turn raw provider output into an s-expression string.

    Pipeline: parse -> validate -> authorize -> render. Behavior by mode:

    * ``legacy``  -> always use ``helper.balance_parentheses``.
    * ``auto``    -> JSON when a valid+authorized batch is found; legacy ONLY when
      no JSON object/block is present at all.
    * ``json``    -> strict. An explicit empty action list renders ``"()"`` (nothing
      to do). Any parse/validation/authorization failure returns a non-``(``-prefixed
      structured-error string so the loop re-prompts the model.
    """
    mode = get_mode()

    if mode == "legacy":
        return balance_parentheses(raw)

    result = parse_actions(raw)
    _emit_parse_trace(result)

    if result.ok:
        _log_warnings(result.errors)
        authorized, auth_errors = authorize_actions(result.actions)
        if auth_errors:
            # Authorization failures are never leaked to the legacy parser.
            return _error_string(_messages(auth_errors))
        return actions_to_metta(authorized)

    if result.source == "none":
        # No JSON found at all. In auto mode, defer to the legacy heuristic
        # parser; in strict json mode, re-prompt for JSON.
        if mode == "auto":
            _log_fallback(raw)
            return balance_parentheses(raw)
        return _error_string("no JSON actions found")

    # JSON *was* detected but yielded no valid batch. Honor JSON semantics in both
    # json and auto mode -- never fall back to legacy here, otherwise an unknown
    # tool in clearly-JSON output would leak through balance_parentheses.
    if not result.errors:
        # Well-formed JSON with an explicit empty actions list: nothing to do.
        return "()"
    return _error_string(_messages(result.errors))


def _error_string(detail):
    """Non-``(``-prefixed string so the loop's else-branch re-prompts the model."""
    return f"ACTION_PROTOCOL_ERROR: {detail}. Reply with ONLY valid JSON: " + output_format_block()


def _selftest():
    """Lightweight self-tests runnable without pytest/Docker."""
    def msgs(r):
        return _messages(r.errors)

    # Basic render shape.
    r = parse_actions('{"actions":[{"tool":"send","args":{"text":"Done"}}]}')
    assert r.ok and r.source == "json", r
    assert actions_to_metta(r.actions) == '((send "Done"))', actions_to_metta(r.actions)

    # Version envelope accepted.
    r = parse_actions('{"version":1,"actions":[{"tool":"pin","args":{"text":"x"}}]}')
    assert r.ok and r.version == 1, r

    # Unknown version -> lenient (still ok, warning recorded).
    r = parse_actions('{"version":99,"actions":[{"tool":"pin","args":{"text":"x"}}]}')
    assert r.ok and any(e.get("warning") for e in r.errors), r

    # Multiline send preserved exactly (escaped, like balance_parentheses).
    r = parse_actions('{"actions":[{"tool":"send","args":{"text":"a\\nb"}}]}')
    assert actions_to_metta(r.actions) == '((send "a\\nb"))', actions_to_metta(r.actions)

    # Unicode preserved literally.
    r = parse_actions('{"actions":[{"tool":"send","args":{"text":"caf\\u00e9 \\u2014 18\\u00b0C"}}]}')
    assert actions_to_metta(r.actions) == '((send "café — 18°C"))', actions_to_metta(r.actions)

    # write-file path + content, in order.
    r = parse_actions('{"actions":[{"tool":"write-file","args":{"path":"t.txt","content":"hi"}}]}')
    assert actions_to_metta(r.actions) == '((write-file "t.txt" "hi"))', actions_to_metta(r.actions)

    # write-file missing content -> whole batch rejected.
    r = parse_actions('{"actions":[{"tool":"write-file","args":{"path":"t.txt"}}]}')
    assert not r.ok and "content" in msgs(r), r

    # metta requires expr.
    r = parse_actions('{"actions":[{"tool":"metta","args":{}}]}')
    assert not r.ok and "expr" in msgs(r), r

    # metta-session-* (Issue #8): create takes a session; add takes session + expr.
    r = parse_actions('{"actions":[{"tool":"metta-session-create","args":{"session":"g1"}}]}')
    assert r.ok and actions_to_metta(r.actions) == '((metta-session-create "g1"))', actions_to_metta(r.actions)
    r = parse_actions('{"actions":[{"tool":"metta-session-add","args":{"session":"g1","expr":"(f)"}}]}')
    assert r.ok and actions_to_metta(r.actions) == '((metta-session-add "g1" "(f)"))', actions_to_metta(r.actions)
    r = parse_actions('{"actions":[{"tool":"metta-session-add","args":{"session":"g1"}}]}')
    assert not r.ok and "expr" in msgs(r), r

    # freeciv-observe: zero-arg tool renders bare (no args required).
    r = parse_actions('{"actions":[{"tool":"freeciv-observe","args":{}}]}')
    assert r.ok and actions_to_metta(r.actions) == '((freeciv-observe))', actions_to_metta(r.actions)

    # freeciv-action: single JSON-string arg passed through verbatim.
    r = parse_actions('{"actions":[{"tool":"freeciv-action","args":{"action":"{\\"type\\":\\"end_turn\\"}"}}]}')
    assert r.ok and actions_to_metta(r.actions) == '((freeciv-action "{\\"type\\":\\"end_turn\\"}"))', actions_to_metta(r.actions)

    # freeciv-action missing its arg -> whole batch rejected.
    r = parse_actions('{"actions":[{"tool":"freeciv-action","args":{}}]}')
    assert not r.ok and "action" in msgs(r), r

    # Unknown tool rejected.
    r = parse_actions('{"actions":[{"tool":"rm-rf","args":{"text":"/"}}]}')
    assert not r.ok and "unknown tool" in msgs(r), r

    # All-or-nothing: one bad action rejects the whole batch.
    r = parse_actions('{"actions":[{"tool":"rm-rf","args":{"text":"/"}},{"tool":"send","args":{"text":"ok"}}]}')
    assert not r.ok and r.actions == [], r

    # Malformed JSON -> source none.
    r = parse_actions('{not json')
    assert not r.ok and r.source == "none", r

    # Fenced block extraction.
    r = parse_actions('here:\n```json\n{"actions":[{"tool":"pin","args":{"text":"x"}}]}\n```')
    assert r.ok and r.source == "json-fenced", r

    # Exceeding the action cap rejects the batch (no silent truncation).
    many = ",".join('{"tool":"pin","args":{"text":"%d"}}' % i for i in range(8))
    r = parse_actions('{"actions":[' + many + "]}")
    assert not r.ok and "exceeds max" in msgs(r), r

    # Bare top-level list accepted.
    r = parse_actions('[{"tool":"query","args":{"text":"db"}}]')
    assert r.ok and actions_to_metta(r.actions) == '((query "db"))', r

    # multi-action render order preserved.
    r = parse_actions(
        '{"actions":[{"tool":"shell","args":{"command":"ls"}},'
        '{"tool":"send","args":{"text":"done"}}]}'
    )
    assert actions_to_metta(r.actions) == '((shell "ls") (send "done"))', actions_to_metta(r.actions)

    # Authorization: disabled tool refuses the batch.
    os.environ["OMEGACLAW_DISABLED_TOOLS"] = "shell"
    try:
        r = parse_actions('{"actions":[{"tool":"shell","args":{"command":"ls"}}]}')
        authd, errs = authorize_actions(r.actions)
        assert authd == [] and errs and errs[0]["code"] == "tool_disabled", (authd, errs)
    finally:
        os.environ.pop("OMEGACLAW_DISABLED_TOOLS", None)

    # Disabling `metta` also disables the evaluator-bearing session tools (Issue #8):
    # they must not be a bypass of the metta sread/eval gate.
    os.environ["OMEGACLAW_DISABLED_TOOLS"] = "metta"
    try:
        for tool, args in (("metta-session-infer", {"session": "g", "expr": "(f)"}),
                           ("metta-session-add", {"session": "g", "expr": "(f)"})):
            r = parse_actions(json.dumps({"actions": [{"tool": tool, "args": args}]}))
            authd, errs = authorize_actions(r.actions)
            assert authd == [] and errs and errs[0]["code"] == "tool_disabled", (tool, authd, errs)
        # non-evaluator session tools remain available (no eval surface)
        r = parse_actions('{"actions":[{"tool":"metta-session-create","args":{"session":"g"}}]}')
        authd, errs = authorize_actions(r.actions)
        assert errs == [] and len(authd) == 1, (authd, errs)
    finally:
        os.environ.pop("OMEGACLAW_DISABLED_TOOLS", None)

    # Authorization default allows everything.
    r = parse_actions('{"actions":[{"tool":"shell","args":{"command":"ls"}}]}')
    authd, errs = authorize_actions(r.actions)
    assert errs == [] and len(authd) == 1, (authd, errs)

    print("action_protocol self-tests passed")


if __name__ == "__main__":
    _selftest()
