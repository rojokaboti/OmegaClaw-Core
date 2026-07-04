"""Structured error recovery events (Issue #10).

Replaces the ad-hoc, string-only error feedback (``ERROR_FEEDBACK``,
``…NOTHING_WAS_DONE…``) with a single machine-readable error vocabulary shared
across the whole pipeline, so recovery is reliable and per-error metrics can be
collected across benchmark runs.

Every failure is classified into one of five canonical categories::

    parse_error              no JSON found / malformed JSON / MeTTa sread failure
    unknown_tool             the model named a tool that does not exist
    schema_validation_error  wrong/missing args, too many actions, bad envelope
    tool_policy_denied       a tool/target the policy refuses (not retryable)
    tool_runtime_error       a validated action raised while being evaluated

Consolidation, not reinvention: the JSON action protocol (Issue #1) and the tool
policy (Issue #2) already produce structured ``{"code", "message"}`` objects.
:data:`CODE_TO_TYPE` maps every one of those codes onto a category here, and the
two MeTTa-native failures (``sread`` parse, per-action ``eval``) are recorded via
the :func:`record_parse_error` / :func:`record_runtime_error` bridges the loop
calls with ``py-call``.

Emission reuses the reasoning trace (Issue #7): :func:`record_error` calls
``tracing.trace_error`` (previously unused) so each error becomes a JSONL event
under the current iteration's ``trace_id`` — the durable, benchmark-analyzable
metric surface. ``trace_id`` is read from the tracing context, never minted here.

Everything is best-effort: a tracing or classification failure must never break
the agent loop.
"""

from __future__ import annotations

from collections import Counter

# --------------------------------------------------------------------------- vocabulary

PARSE_ERROR = "parse_error"
UNKNOWN_TOOL = "unknown_tool"
SCHEMA_VALIDATION_ERROR = "schema_validation_error"
TOOL_POLICY_DENIED = "tool_policy_denied"
TOOL_RUNTIME_ERROR = "tool_runtime_error"

ERROR_TYPES = (
    PARSE_ERROR,
    UNKNOWN_TOOL,
    SCHEMA_VALIDATION_ERROR,
    TOOL_POLICY_DENIED,
    TOOL_RUNTIME_ERROR,
)

# Maps every structured code emitted by action_protocol / tool_policy onto a
# canonical category. Kept exhaustive on purpose: a test asserts that no
# action_protocol code is left unmapped, so this never silently drifts.
CODE_TO_TYPE = {
    # parse
    "no_json": PARSE_ERROR,
    # unknown tool
    "unknown_tool": UNKNOWN_TOOL,
    # schema / validation
    "missing_tool": SCHEMA_VALIDATION_ERROR,
    "missing_arg": SCHEMA_VALIDATION_ERROR,
    "bad_arg_type": SCHEMA_VALIDATION_ERROR,
    "bad_args": SCHEMA_VALIDATION_ERROR,
    "bad_action": SCHEMA_VALIDATION_ERROR,
    "too_many_actions": SCHEMA_VALIDATION_ERROR,
    "unsupported_version": SCHEMA_VALIDATION_ERROR,
    # policy
    "tool_disabled": TOOL_POLICY_DENIED,
    "policy_denied": TOOL_POLICY_DENIED,
}

# Whether re-attempting after a repair hint can plausibly succeed. A policy
# denial re-denies the identical action, so it is not retryable — the model must
# choose a *different* action, which the hint says.
RETRYABLE = {
    PARSE_ERROR: True,
    UNKNOWN_TOOL: True,
    SCHEMA_VALIDATION_ERROR: True,
    TOOL_POLICY_DENIED: False,
    TOOL_RUNTIME_ERROR: True,
}

# Concise, category-specific guidance fed back to the model. Deliberately short:
# this is what the LLM reads on the next turn, not the verbose OUTPUT_FORMAT block.
REPAIR_HINTS = {
    PARSE_ERROR: (
        "Your output was not valid. Reply with ONLY one JSON object: "
        '{"actions":[{"tool":"<name>","args":{...}}]} — no prose, no code fences.'
    ),
    UNKNOWN_TOOL: (
        "You named a tool that does not exist. Use only an allowed tool name, "
        "and keep the JSON action shape."
    ),
    SCHEMA_VALIDATION_ERROR: (
        "An action had wrong or missing arguments. Provide every required arg "
        'for the tool as strings, e.g. {"tool":"write-file","args":{"path":"..","content":".."}}.'
    ),
    TOOL_POLICY_DENIED: (
        "That action is not permitted by policy. Do not retry it — choose a "
        "different, allowed action to reach the goal."
    ),
    TOOL_RUNTIME_ERROR: (
        "The action was valid but failed while running. Check its arguments "
        "(paths, syntax) and retry, or try a different approach."
    ),
}


# --------------------------------------------------------------------------- counters

# In-process counters. Durable/analyzable metrics come from aggregating the
# emitted trace_error events (see scripts/omegaclaw-trace-summary); this counter
# is a lightweight per-process view for tests and quick introspection.
_counts = Counter()


def counts():
    """Return a plain dict of per-category error counts seen this process."""
    return dict(_counts)


def reset_counts():
    """Test helper: clear the in-process counters."""
    _counts.clear()


# --------------------------------------------------------------------------- classification

def type_for_code(code):
    """Map a structured action_protocol/tool_policy ``code`` to a category.

    Unrecognized codes fall back to ``schema_validation_error`` (a benign,
    retryable bucket) rather than an ``unknown`` bucket, so the machine-readable
    classification is always one of the five known categories.
    """
    return CODE_TO_TYPE.get(code, SCHEMA_VALIDATION_ERROR)


def _current_trace_id():
    """Read the current iteration's trace_id from tracing; None if unavailable."""
    try:  # tracing is best-effort and may be absent under some import paths
        import tracing
    except ImportError:  # pragma: no cover - alternate import path under pytest
        try:
            from src import tracing
        except ImportError:
            return None
    try:
        return tracing.current().get("trace_id")
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- build / record

def build_error(error_type, message, failed_action=None, code=None):
    """Build a structured error event matching the Issue #10 schema.

    Returns a dict with ``error_type``, ``message``, ``failed_action``,
    ``repair_hint``, ``trace_id`` and ``retryable``. Never raises.
    """
    if error_type not in ERROR_TYPES:
        error_type = SCHEMA_VALIDATION_ERROR
    return {
        "error_type": error_type,
        "message": message or "",
        "failed_action": failed_action,
        "repair_hint": REPAIR_HINTS[error_type],
        "trace_id": _current_trace_id(),
        "retryable": RETRYABLE[error_type],
        "code": code,
    }


def record_error(error_type, message, failed_action=None, code=None):
    """Build an error, emit it to the reasoning trace, and bump the counter.

    Best-effort: tracing/emit failures are swallowed so the agent loop is never
    broken by error bookkeeping. Returns the structured error dict.
    """
    err = build_error(error_type, message, failed_action=failed_action, code=code)
    _counts[err["error_type"]] += 1
    try:
        import tracing
    except ImportError:  # pragma: no cover - alternate import path under pytest
        try:
            from src import tracing
        except ImportError:
            tracing = None
    if tracing is not None:
        try:
            tracing.trace_error(stage=err["error_type"], code=err["error_type"],
                                message=err["message"])
        except Exception:  # noqa: BLE001 - tracing must never break the pipeline
            pass
    return err


def record_code(code, message, failed_action=None):
    """Record an error from a structured ``code`` (maps to a category first)."""
    return record_error(type_for_code(code), message, failed_action=failed_action, code=code)


def format_error_for_llm(error):
    """Return the concise repair hint to feed back to the model for ``error``.

    Accepts a full error dict (from :func:`build_error`) or a bare category
    string. Always returns a non-empty, ``(``-free string so the loop's
    re-prompt branch is preserved.
    """
    if isinstance(error, dict):
        return error.get("repair_hint") or REPAIR_HINTS.get(
            error.get("error_type"), REPAIR_HINTS[SCHEMA_VALIDATION_ERROR])
    return REPAIR_HINTS.get(error, REPAIR_HINTS[SCHEMA_VALIDATION_ERROR])


# --------------------------------------------------------------------------- MeTTa bridges

def record_parse_error(raw):
    """MeTTa bridge: an ``sread`` parse failure on the model's response.

    Records a ``parse_error`` (failed_action = the raw response text) and returns
    the concise repair hint string stored into ``&error`` / history feedback.
    """
    err = record_error(PARSE_ERROR, "response was not parseable as valid actions",
                        failed_action=(str(raw) if raw is not None else None))
    return format_error_for_llm(err)


def record_runtime_error(sexpr_repr):
    """MeTTa bridge: a validated action raised while being ``eval``-uated.

    Records a ``tool_runtime_error`` (failed_action = the failing s-expression)
    and returns the concise repair hint string.
    """
    err = record_error(TOOL_RUNTIME_ERROR, "action failed during execution",
                        failed_action=(str(sexpr_repr) if sexpr_repr is not None else None))
    return format_error_for_llm(err)


# --------------------------------------------------------------------------- self-test

def _selftest():
    """Lightweight self-tests runnable without pytest/Docker."""
    import os
    import tempfile

    # Every action_protocol/tool_policy code maps to a known category.
    try:
        import action_protocol as ap
    except ImportError:  # pragma: no cover
        from src import action_protocol as ap
    seen_codes = set()
    # Drive representative failures through the protocol and collect their codes.
    for raw in ('{not json',
                '{"actions":[{"tool":"rm-rf","args":{"text":"/"}}]}',
                '{"actions":[{"tool":"write-file","args":{"path":"t"}}]}',
                '{"actions":[{"tool":"metta"}]}'):
        r = ap.parse_actions(raw)
        for e in r.errors:
            seen_codes.add(e["code"])
    for code in seen_codes:
        assert code in CODE_TO_TYPE, "unmapped action_protocol code: {}".format(code)

    # Each category builds a complete schema.
    for t in ERROR_TYPES:
        e = build_error(t, "msg", failed_action={"tool": "x"})
        assert set(e) >= {"error_type", "message", "failed_action", "repair_hint",
                          "trace_id", "retryable", "code"}, e
        assert e["repair_hint"] and "(" not in format_error_for_llm(e)[:1], e

    # policy denial is not retryable; others are.
    assert build_error(TOOL_POLICY_DENIED, "x")["retryable"] is False
    assert build_error(PARSE_ERROR, "x")["retryable"] is True

    # code mapping
    assert type_for_code("unknown_tool") == UNKNOWN_TOOL
    assert type_for_code("missing_arg") == SCHEMA_VALIDATION_ERROR
    assert type_for_code("policy_denied") == TOOL_POLICY_DENIED
    assert type_for_code("no_json") == PARSE_ERROR
    assert type_for_code("totally_new_code") == SCHEMA_VALIDATION_ERROR

    # record_error emits a trace_error event and bumps the counter.
    with tempfile.TemporaryDirectory() as d:
        os.environ["OMEGACLAW_TRACE_PATH"] = os.path.join(d, "t.jsonl")
        os.environ.pop("OMEGACLAW_TRACE_DISABLE", None)
        try:
            import tracing
        except ImportError:  # pragma: no cover
            from src import tracing
        import json
        tracing.reset(); tracing.begin_session(); tracing.begin_iteration(1)
        reset_counts()
        record_error(UNKNOWN_TOOL, "unknown tool: 'rm-rf'")
        hint = record_parse_error('{bad')
        assert hint and "(" not in hint[:1]
        events = [json.loads(x) for x in open(os.environ["OMEGACLAW_TRACE_PATH"], encoding="utf-8") if x.strip()]
        errs = [e for e in events if e["phase"] == "error"]
        assert len(errs) == 2, errs
        assert {e["code"] for e in errs} == {UNKNOWN_TOOL, PARSE_ERROR}, errs
        # trace_id linkage: error events carry the iteration's trace_id
        assert all(e.get("trace_id") for e in errs), errs
        assert counts()[UNKNOWN_TOOL] == 1 and counts()[PARSE_ERROR] == 1, counts()
        tracing.reset()
        os.environ.pop("OMEGACLAW_TRACE_PATH", None)

    reset_counts()
    print("errors self-tests passed")


if __name__ == "__main__":
    _selftest()
