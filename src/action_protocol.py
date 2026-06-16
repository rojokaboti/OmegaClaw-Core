"""Structured JSON action protocol (Issue #1).

Inserts a validated boundary between raw LLM output and MeTTa skill evaluation.
The LLM is asked to emit JSON of the shape::

    {"actions": [{"tool": "send", "args": {"text": "Done"}}]}

This module parses that JSON, validates each action against the known tool set,
and renders it into the exact s-expression shape ``sread`` already expects in
``src/loop.metta`` -- e.g. ``((send "Done") (query "food shortage"))``.

The legacy heuristic parser ``helper.balance_parentheses`` is retained and used
only when the operating mode is ``legacy`` or ``auto`` (and JSON is absent).

Operating mode is selected by the ``OMEGACLAW_ACTION_PROTOCOL`` environment
variable: ``json`` (default, strict), ``auto`` (JSON, else legacy fallback) or
``legacy`` (legacy parser only).
"""

from __future__ import annotations

import json
import os
import re

try:  # registered to MeTTa as a flat module ("helper"), but also a package member
    from helper import LLM_COMMANDS, balance_parentheses
except ImportError:  # pragma: no cover - import path differs under pytest from repo root
    from src.helper import LLM_COMMANDS, balance_parentheses


# Single source of truth for valid tool names.
ALLOWED_TOOLS = set(LLM_COMMANDS)

# Maximum number of actions accepted in one turn. Preserves the historical
# "Up to 5 lines" guidance from the old text protocol. Configurable via
# OMEGACLAW_MAX_ACTIONS so deployments that legitimately need longer action
# chains in a single turn can raise it without code changes.
def _max_actions():
    raw = os.environ.get("OMEGACLAW_MAX_ACTIONS")
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return n
        except ValueError:
            pass
    return 5


MAX_ACTIONS = _max_actions()

# Ordered argument keys per tool, with accepted aliases. The first name in each
# list is the canonical key advertised in the prompt; later names are tolerated
# so a slightly-off model output still validates instead of being rejected.
ARG_SPEC = {
    "send": [("text",)],
    "query": [("text",)],
    "search": [("text",)],
    "remember": [("text",)],
    "pin": [("text",)],
    "tavily-search": [("text", "query")],
    "technical-analysis": [("ticker", "symbol", "text")],
    "episodes": [("time", "text")],
    "read-file": [("path", "file", "filename")],
    "shell": [("command", "cmd", "text")],
    "metta": [("expr", "code", "text")],
    "write-file": [("path", "file", "filename"), ("content", "text", "str")],
    "append-file": [("path", "file", "filename"), ("content", "text", "str")],
}

DEFAULT_MODE = "json"
_VALID_MODES = {"json", "auto", "legacy"}

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


class ActionParseResult:
    """Outcome of parsing raw LLM output into validated actions.

    Attributes:
        ok: True when at least one valid action was produced.
        actions: list of validated ``{"tool", "args"}`` dicts.
        errors: list of human-readable validation/parse error strings.
        source: one of ``"json"``, ``"json-fenced"``, ``"none"``.
    """

    __slots__ = ("ok", "actions", "errors", "source")

    def __init__(self, ok, actions, errors, source):
        self.ok = ok
        self.actions = actions
        self.errors = errors
        self.source = source

    def __repr__(self):
        return (
            f"ActionParseResult(ok={self.ok}, source={self.source!r}, "
            f"actions={self.actions!r}, errors={self.errors!r})"
        )


def _coerce_actions_container(data):
    """Return the list of raw action dicts from a parsed JSON document.

    Accepts either ``{"actions": [...]}`` or a bare top-level list. Returns
    ``None`` when the document is not a recognized action container.
    """
    if isinstance(data, dict) and isinstance(data.get("actions"), list):
        return data["actions"]
    if isinstance(data, list):
        return data
    return None


def _try_json(raw):
    """Attempt to extract a list of raw action dicts from ``raw``.

    Tries a strict parse first, then a fenced ```json``` block. Returns a tuple
    ``(actions_or_None, source)`` where source is ``"json"``, ``"json-fenced"``
    or ``"none"``.
    """
    text = raw.strip() if isinstance(raw, str) else ""
    if text:
        try:
            container = _coerce_actions_container(json.loads(text))
            if container is not None:
                return container, "json"
        except (ValueError, TypeError):
            pass

    for match in _FENCE_RE.finditer(raw or ""):
        try:
            container = _coerce_actions_container(json.loads(match.group(1)))
            if container is not None:
                return container, "json-fenced"
        except (ValueError, TypeError):
            continue

    return None, "none"


def validate_action(action):
    """Validate a single raw action dict.

    Returns ``(ordered_args, None)`` on success where ``ordered_args`` is the
    list of string argument values in tool-declared order, or ``(None, error)``
    with a human-readable error string on failure.
    """
    if not isinstance(action, dict):
        return None, f"action is not an object: {action!r}"

    tool = action.get("tool")
    if not isinstance(tool, str) or not tool:
        return None, "action missing string 'tool'"
    if tool not in ALLOWED_TOOLS:
        return None, f"unknown tool: {tool!r}"

    args = action.get("args", {})
    if not isinstance(args, dict):
        return None, f"{tool}: 'args' must be an object"

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
            return None, f"{tool}: missing required arg '{alias_group[0]}'"
        if not isinstance(value, str):
            return None, f"{tool}: arg '{alias_group[0]}' must be a string"
        ordered.append(value)

    return ordered, None


def parse_actions(raw):
    """Parse raw LLM output into an :class:`ActionParseResult`.

    Only JSON sources are considered here; legacy fallback is the caller's
    responsibility (see :func:`parse_and_render_metta`).
    """
    container, source = _try_json(raw)
    if container is None:
        return ActionParseResult(False, [], ["no JSON actions found"], "none")

    actions = []
    errors = []
    for idx, item in enumerate(container):
        ordered, err = validate_action(item)
        if err is not None:
            errors.append(err)
            continue
        actions.append({"tool": item["tool"], "values": ordered})
        if len(actions) >= MAX_ACTIONS:
            remaining = len(container) - idx - 1
            if remaining > 0:
                errors.append(f"truncated to {MAX_ACTIONS} actions ({remaining} dropped)")
            break

    return ActionParseResult(bool(actions), actions, errors, source)


def actions_to_metta(actions):
    """Render validated actions (from :func:`parse_actions`) into the
    ``sread``-shaped s-expression string, e.g. ``((send "hi") (pin "x"))``.

    String arguments are emitted via ``json.dumps`` so quoting and ``\\n``/``\\"``
    escaping match exactly what ``helper.balance_parentheses`` produces.
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
    return (
        "OUTPUT_FORMAT: Reply with ONLY a single JSON object, no prose, no code fences, "
        f"at most {MAX_ACTIONS} actions: "
        '{"actions":[{"tool":"<name>","args":{...}}]} . '
        f"Allowed tools and their args: {tools}"
    )


def parse_and_render_metta(raw):
    """MeTTa entry point: turn raw provider output into an s-expression string.

    Behavior depends on :func:`get_mode`:

    * ``legacy``  -> always use ``helper.balance_parentheses``.
    * ``auto``    -> use JSON when at least one valid action is found, else legacy.
    * ``json``    -> strict JSON. An explicit empty action list renders ``"()"``
      (nothing to do). A hard parse/validation failure returns a non-``(``-prefixed
      error string so the loop's existing else-branch re-prompts the model.
    """
    mode = get_mode()

    if mode == "legacy":
        return balance_parentheses(raw)

    result = parse_actions(raw)

    if result.ok:
        return actions_to_metta(result.actions)

    if result.source == "none":
        # No JSON was found at all. In auto mode, defer to the legacy heuristic
        # parser; in strict json mode, re-prompt for JSON.
        if mode == "auto":
            return balance_parentheses(raw)
        return _error_string("no JSON actions found")

    # JSON *was* detected but yielded no valid actions. Honor JSON semantics in
    # both json and auto mode -- never fall back to legacy here, otherwise an
    # unknown tool in clearly-JSON output would leak through balance_parentheses.
    if not result.errors:
        # Well-formed JSON with an explicit empty actions list: nothing to do.
        return "()"
    return _error_string("; ".join(result.errors))


def _error_string(detail):
    """Non-``(``-prefixed string so the loop's else-branch re-prompts the model."""
    return f"ACTION_PROTOCOL_ERROR: {detail}. Reply with ONLY valid JSON: " + output_format_block()


def _selftest():
    """Lightweight self-tests runnable without pytest/Docker."""
    # Basic render shape.
    r = parse_actions('{"actions":[{"tool":"send","args":{"text":"Done"}}]}')
    assert r.ok and r.source == "json", r
    assert actions_to_metta(r.actions) == '((send "Done"))', actions_to_metta(r.actions)

    # Multiline send preserved exactly (escaped, like balance_parentheses).
    r = parse_actions('{"actions":[{"tool":"send","args":{"text":"a\\nb"}}]}')
    assert actions_to_metta(r.actions) == '((send "a\\nb"))', actions_to_metta(r.actions)

    # write-file path + content, in order.
    r = parse_actions('{"actions":[{"tool":"write-file","args":{"path":"t.txt","content":"hi"}}]}')
    assert actions_to_metta(r.actions) == '((write-file "t.txt" "hi"))', actions_to_metta(r.actions)

    # write-file missing content -> rejected.
    r = parse_actions('{"actions":[{"tool":"write-file","args":{"path":"t.txt"}}]}')
    assert not r.ok and any("content" in e for e in r.errors), r

    # metta requires expr.
    r = parse_actions('{"actions":[{"tool":"metta","args":{}}]}')
    assert not r.ok and any("expr" in e for e in r.errors), r

    # Unknown tool rejected.
    r = parse_actions('{"actions":[{"tool":"rm-rf","args":{"text":"/"}}]}')
    assert not r.ok and any("unknown tool" in e for e in r.errors), r

    # Malformed JSON -> source none.
    r = parse_actions('{not json')
    assert not r.ok and r.source == "none", r

    # Fenced block extraction.
    r = parse_actions('here:\n```json\n{"actions":[{"tool":"pin","args":{"text":"x"}}]}\n```')
    assert r.ok and r.source == "json-fenced", r

    # Max 5 actions enforced.
    many = ",".join('{"tool":"pin","args":{"text":"%d"}}' % i for i in range(8))
    r = parse_actions('{"actions":[' + many + "]}")
    assert len(r.actions) == MAX_ACTIONS, r
    assert any("truncated" in e for e in r.errors), r

    # Bare top-level list accepted.
    r = parse_actions('[{"tool":"query","args":{"text":"db"}}]')
    assert r.ok and actions_to_metta(r.actions) == '((query "db"))', r

    # multi-action render order preserved.
    r = parse_actions(
        '{"actions":[{"tool":"shell","args":{"command":"ls"}},'
        '{"tool":"send","args":{"text":"done"}}]}'
    )
    assert actions_to_metta(r.actions) == '((shell "ls") (send "done"))', actions_to_metta(r.actions)

    print("action_protocol self-tests passed")


if __name__ == "__main__":
    _selftest()
