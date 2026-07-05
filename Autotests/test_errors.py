"""Unit tests for structured error recovery events (Issue #10).

Pure-Python, no Docker/LLM/MeTTa. Runs under pytest and standalone
(`python3 Autotests/test_errors.py`). Covers: the five canonical categories and
their schema, exhaustive code->category mapping (drift guard), classification of
each real failure through the action protocol, concise repair hints, retryability,
trace_error emission + per-type counters, and the trace-summary aggregation of
error events. Also guards the loop's re-prompt contract (error strings stay
non-``(``-prefixed) and backward-compat of the action protocol.
"""
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_SRC, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import errors  # noqa: E402
import action_protocol as ap  # noqa: E402
import tracing  # noqa: E402


def _fresh_trace(tmp, name="t.jsonl"):
    path = os.path.join(tmp, name)
    os.environ["OMEGACLAW_TRACE_PATH"] = path
    for k in ("OMEGACLAW_TRACE_DISABLE", "OMEGACLAW_TRACE_BODIES", "OMEGACLAW_DEBUG_LLM_RAW"):
        os.environ.pop(k, None)
    tracing.reset()
    tracing.begin_session()
    tracing.begin_iteration(1)
    errors.reset_counts()
    return path


def _read(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


# --- vocabulary + schema ---------------------------------------------------

def test_five_categories_exist():
    assert set(errors.ERROR_TYPES) == {
        "parse_error", "unknown_tool", "schema_validation_error",
        "tool_policy_denied", "tool_runtime_error"}


def test_every_category_serializes_full_schema():
    for t in errors.ERROR_TYPES:
        e = errors.build_error(t, "some message", failed_action={"tool": "x"})
        assert set(e) >= {"error_type", "message", "failed_action", "repair_hint",
                          "trace_id", "retryable", "code"}
        assert e["error_type"] == t
        assert e["message"] == "some message"
        assert e["failed_action"] == {"tool": "x"}
        assert isinstance(e["repair_hint"], str) and e["repair_hint"]


def test_repair_hint_is_concise_and_non_paren():
    for t in errors.ERROR_TYPES:
        hint = errors.format_error_for_llm(t)
        assert hint and not hint.startswith("(")  # preserves loop re-prompt contract
        assert len(hint) < 300  # concise, not the full OUTPUT_FORMAT dump


def test_policy_denied_not_retryable_others_are():
    assert errors.build_error("tool_policy_denied", "x")["retryable"] is False
    for t in ("parse_error", "unknown_tool", "schema_validation_error", "tool_runtime_error"):
        assert errors.build_error(t, "x")["retryable"] is True


# --- classification / mapping ---------------------------------------------

def test_code_to_type_covers_every_action_protocol_code():
    """Drift guard: every structured code action_protocol can emit is mapped."""
    seen = set()
    raws = [
        '{not json',                                                    # no_json
        '{"actions":[{"tool":"rm-rf","args":{"text":"/"}}]}',           # unknown_tool
        '{"actions":[{"tool":"write-file","args":{"path":"t"}}]}',      # missing_arg
        '{"actions":[{"tool":"metta"}]}',                               # missing_arg
        '{"actions":[{"tool":"send","args":"nope"}]}',                  # bad_args
        '{"actions":[{"tool":"send","args":{"text":5}}]}',              # bad_arg_type
        '{"actions":["notdict"]}',                                      # bad_action
        '{"actions":[{"noname":1}]}',                                   # missing_tool
        '{"version":9,"actions":[{"tool":"send","args":{"text":"x"}}]}',  # unsupported_version (warning)
        '{"actions":[' + ",".join('{"tool":"pin","args":{"text":"%d"}}' % i for i in range(9)) + ']}',  # too_many_actions
    ]
    for raw in raws:
        for e in ap.parse_actions(raw).errors:
            seen.add(e["code"])
    # authorize codes
    os.environ["OMEGACLAW_DISABLED_TOOLS"] = "shell"
    try:
        r = ap.parse_actions('{"actions":[{"tool":"shell","args":{"command":"ls"}}]}')
        _, errs = ap.authorize_actions(r.actions)
        for e in errs:
            seen.add(e["code"])
    finally:
        os.environ.pop("OMEGACLAW_DISABLED_TOOLS", None)
    assert "unsupported_version" in seen  # sanity: fixtures actually exercised codes
    for code in seen:
        assert code in errors.CODE_TO_TYPE, "unmapped action_protocol code: {}".format(code)


def test_type_for_code_categories():
    assert errors.type_for_code("no_json") == "parse_error"
    assert errors.type_for_code("unknown_tool") == "unknown_tool"
    assert errors.type_for_code("missing_arg") == "schema_validation_error"
    assert errors.type_for_code("too_many_actions") == "schema_validation_error"
    assert errors.type_for_code("tool_disabled") == "tool_policy_denied"
    assert errors.type_for_code("policy_denied") == "tool_policy_denied"
    # unknown code falls into a benign known bucket, never an "unknown" bucket
    assert errors.type_for_code("brand_new_code") in errors.ERROR_TYPES


# --- emission + counters ---------------------------------------------------

def test_record_error_emits_trace_and_counts():
    with tempfile.TemporaryDirectory() as d:
        path = _fresh_trace(d)
        errors.record_error("unknown_tool", "unknown tool: 'rm-rf'")
        errors.record_code("policy_denied", "shell: denied (risk=high)")
        evs = [e for e in _read(path) if e["phase"] == "error"]
        assert len(evs) == 2
        # categories under error_type; original protocol code preserved under code
        assert {e["error_type"] for e in evs} == {"unknown_tool", "tool_policy_denied"}
        by_type = {e["error_type"]: e for e in evs}
        assert by_type["tool_policy_denied"]["code"] == "policy_denied"  # granular, not the category
        assert all(e.get("trace_id") for e in evs)  # linked to the iteration
        assert errors.counts() == {"unknown_tool": 1, "tool_policy_denied": 1}
        tracing.reset()
        os.environ.pop("OMEGACLAW_TRACE_PATH", None)


def test_emitted_event_carries_full_schema():
    """The DURABLE trace event (not just build_error) must carry the schema fields."""
    with tempfile.TemporaryDirectory() as d:
        path = _fresh_trace(d)
        errors.record_code("missing_arg", "write-file: missing required arg 'content'",
                           failed_action={"tool": "write-file", "values": ["/tmp/x"]})
        ev = [e for e in _read(path) if e["phase"] == "error"][0]
        assert ev["error_type"] == "schema_validation_error"  # category
        assert ev["code"] == "missing_arg"                    # original granular code preserved
        assert ev["retryable"] is True
        assert ev["repair_hint"] and not ev["repair_hint"].startswith("(")
        # failed action referenceable by default (privacy: sha, not body)
        assert ev.get("failed_action_sha") and ev.get("failed_action_chars")
        assert "failed_action" not in ev  # body withheld unless bodies mode
        tracing.reset()
        os.environ.pop("OMEGACLAW_TRACE_PATH", None)


def test_failed_action_body_recoverable_under_bodies_mode():
    with tempfile.TemporaryDirectory() as d:
        path = _fresh_trace(d)
        os.environ["OMEGACLAW_TRACE_BODIES"] = "1"
        try:
            errors.record_runtime_error('(write-file "/tmp/x" "secret-ish content")')
            ev = [e for e in _read(path) if e["phase"] == "error"][0]
            assert ev["error_type"] == "tool_runtime_error"
            assert ev.get("failed_action") and "write-file" in ev["failed_action"]
        finally:
            os.environ.pop("OMEGACLAW_TRACE_BODIES", None)
            tracing.reset()
            os.environ.pop("OMEGACLAW_TRACE_PATH", None)


def test_metta_bridges_record_and_return_hint():
    with tempfile.TemporaryDirectory() as d:
        path = _fresh_trace(d)
        h1 = errors.record_parse_error("{bad json")
        h2 = errors.record_runtime_error('(write-file "x" "y")')
        assert h1 and not h1.startswith("(")
        assert h2 and not h2.startswith("(")
        evs = [e for e in _read(path) if e["phase"] == "error"]
        assert {e["code"] for e in evs} == {"parse_error", "tool_runtime_error"}
        assert errors.counts()["parse_error"] == 1
        assert errors.counts()["tool_runtime_error"] == 1
        tracing.reset()
        os.environ.pop("OMEGACLAW_TRACE_PATH", None)


# --- action protocol integration (each failure -> a category + emission) ---

def _categories_from_render(raw):
    """Drive parse_and_render_metta and return the error categories it recorded."""
    with tempfile.TemporaryDirectory() as d:
        path = _fresh_trace(d)
        os.environ["OMEGACLAW_ACTION_PROTOCOL"] = "json"
        try:
            out = ap.parse_and_render_metta(raw)
        finally:
            os.environ.pop("OMEGACLAW_ACTION_PROTOCOL", None)
        evs = [e for e in _read(path) if e["phase"] == "error"]
        cats = [e["error_type"] for e in evs]
        tracing.reset()
        os.environ.pop("OMEGACLAW_TRACE_PATH", None)
        return out, cats


def test_render_parse_error_classified():
    out, cats = _categories_from_render("totally not json")
    assert not out.startswith("(") and "ACTION_PROTOCOL_ERROR" in out
    assert cats == ["parse_error"]


def test_render_unknown_tool_classified():
    out, cats = _categories_from_render('{"actions":[{"tool":"rm-rf","args":{"text":"/"}}]}')
    assert not out.startswith("(")
    assert "unknown_tool" in cats


def test_render_schema_error_classified():
    out, cats = _categories_from_render('{"actions":[{"tool":"write-file","args":{"path":"t"}}]}')
    assert not out.startswith("(")
    assert "schema_validation_error" in cats


def test_render_policy_denied_classified():
    os.environ["OMEGACLAW_DISABLED_TOOLS"] = "shell"
    try:
        out, cats = _categories_from_render('{"actions":[{"tool":"shell","args":{"command":"ls"}}]}')
    finally:
        os.environ.pop("OMEGACLAW_DISABLED_TOOLS", None)
    assert not out.startswith("(") and "disabled" in out
    assert "tool_policy_denied" in cats


def test_valid_action_records_no_error():
    _, cats = _categories_from_render('{"actions":[{"tool":"send","args":{"text":"hi"}}]}')
    assert cats == []


# --- summary aggregator ----------------------------------------------------

def _load_summary_module():
    loader = importlib.machinery.SourceFileLoader(
        "trace_summary", os.path.join(_REPO_ROOT, "scripts", "omegaclaw-trace-summary"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("trace_summary", loader))
    loader.exec_module(mod)
    return mod


def test_summary_counts_errors_by_type():
    ts = _load_summary_module()
    with tempfile.TemporaryDirectory() as d:
        path = _fresh_trace(d)
        errors.record_error("parse_error", "a")
        errors.record_error("parse_error", "b")
        errors.record_error("tool_runtime_error", "c")
        events, malformed = ts.load_events(path)
        s = ts.summarize(events, malformed)
        assert s["error_events"] == 3
        assert s["errors_by_type"] == {"parse_error": 2, "tool_runtime_error": 1}
        tracing.reset()
        os.environ.pop("OMEGACLAW_TRACE_PATH", None)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print("\nAll {} errors tests passed".format(len(fns)))
