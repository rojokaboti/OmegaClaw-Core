"""Unit tests for the JSON action protocol (Issue #1).

Pure-Python: no Docker, no hyperon, no chromadb. Runs under pytest in CI and as
a standalone script locally (``python3 Autotests/test_action_protocol.py``),
which is handy because the CI host installs pytest/chromadb but a dev box may
not.
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_SRC, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import action_protocol as ap  # noqa: E402


def _set_env(name, value):
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def _msgs(result):
    """Joined error messages for a result."""
    return "; ".join(e["message"] for e in result.errors)


# --- parsing & rendering -------------------------------------------------

def test_valid_single_action_renders_sread_shape():
    r = ap.parse_actions('{"actions":[{"tool":"send","args":{"text":"Done"}}]}')
    assert r.ok and r.source == "json"
    assert ap.actions_to_metta(r.actions) == '((send "Done"))'


def test_multiple_actions_preserve_order():
    raw = (
        '{"actions":[{"tool":"query","args":{"text":"food shortage"}},'
        '{"tool":"send","args":{"text":"Done"}}]}'
    )
    r = ap.parse_actions(raw)
    assert ap.actions_to_metta(r.actions) == '((query "food shortage") (send "Done"))'


def test_bare_top_level_list_accepted():
    r = ap.parse_actions('[{"tool":"pin","args":{"text":"x"}}]')
    assert r.ok and ap.actions_to_metta(r.actions) == '((pin "x"))'


def test_malformed_json_returns_structured_error():
    r = ap.parse_actions("{ this is not json")
    assert not r.ok
    assert r.source == "none"
    assert r.errors and r.errors[0]["code"] == "no_json"


# --- versioning ----------------------------------------------------------

def test_version_envelope_accepted():
    r = ap.parse_actions('{"version":1,"actions":[{"tool":"pin","args":{"text":"x"}}]}')
    assert r.ok and r.version == 1
    assert ap.actions_to_metta(r.actions) == '((pin "x"))'


def test_unknown_version_is_lenient():
    # An unrecognized version must NOT hard-reject otherwise-valid actions.
    r = ap.parse_actions('{"version":99,"actions":[{"tool":"pin","args":{"text":"x"}}]}')
    assert r.ok and r.version == 99
    assert any(e.get("warning") and e["code"] == "unsupported_version" for e in r.errors)
    assert ap.actions_to_metta(r.actions) == '((pin "x"))'


def test_output_format_block_advertises_version_envelope():
    block = ap.output_format_block()
    assert '"version":1' in block


# --- validation & all-or-nothing -----------------------------------------

def test_unknown_tool_rejected():
    r = ap.parse_actions('{"actions":[{"tool":"rm-rf","args":{"text":"/"}}]}')
    assert not r.ok
    assert any(e["code"] == "unknown_tool" for e in r.errors)


def test_one_invalid_action_rejects_whole_batch():
    # A valid send mixed with an unknown tool -> entire batch rejected.
    raw = (
        '{"actions":[{"tool":"rm-rf","args":{"text":"/"}},'
        '{"tool":"send","args":{"text":"ok"}}]}'
    )
    r = ap.parse_actions(raw)
    assert not r.ok
    assert r.actions == []
    assert ap.actions_to_metta(r.actions) == "()"  # nothing rendered for eval


def test_multiline_send_preserved_exactly():
    r = ap.parse_actions(
        '{"actions":[{"tool":"send","args":{"text":"Here are the planets:\\n1. Mercury\\n2. Venus"}}]}'
    )
    assert ap.actions_to_metta(r.actions) == '((send "Here are the planets:\\n1. Mercury\\n2. Venus"))'


def test_send_with_embedded_quotes_escaped():
    r = ap.parse_actions('{"actions":[{"tool":"send","args":{"text":"say \\"hi\\" now"}}]}')
    assert ap.actions_to_metta(r.actions) == '((send "say \\"hi\\" now"))'


def test_unicode_preserved_exactly():
    r = ap.parse_actions('{"actions":[{"tool":"send","args":{"text":"caf\\u00e9 \\u2014 18\\u00b0C \\ud83d\\ude80"}}]}')
    assert r.ok
    assert ap.actions_to_metta(r.actions) == '((send "café — 18°C 🚀"))'


def test_write_file_requires_path_and_content():
    ok = ap.parse_actions('{"actions":[{"tool":"write-file","args":{"path":"t.txt","content":"hi"}}]}')
    assert ok.ok and ap.actions_to_metta(ok.actions) == '((write-file "t.txt" "hi"))'

    no_content = ap.parse_actions('{"actions":[{"tool":"write-file","args":{"path":"t.txt"}}]}')
    assert not no_content.ok and "content" in _msgs(no_content)

    no_path = ap.parse_actions('{"actions":[{"tool":"write-file","args":{"content":"hi"}}]}')
    assert not no_path.ok and "path" in _msgs(no_path)


def test_append_file_requires_path_and_content():
    r = ap.parse_actions('{"actions":[{"tool":"append-file","args":{"path":"log.txt","content":"line"}}]}')
    assert r.ok and ap.actions_to_metta(r.actions) == '((append-file "log.txt" "line"))'


def test_metta_requires_expr():
    bad = ap.parse_actions('{"actions":[{"tool":"metta","args":{}}]}')
    assert not bad.ok and "expr" in _msgs(bad)

    good = ap.parse_actions('{"actions":[{"tool":"metta","args":{"expr":"(|- a b)"}}]}')
    assert good.ok and ap.actions_to_metta(good.actions) == '((metta "(|- a b)"))'


def test_non_string_arg_rejected():
    r = ap.parse_actions('{"actions":[{"tool":"send","args":{"text":123}}]}')
    assert not r.ok and any(e["code"] == "bad_arg_type" for e in r.errors)


def test_args_must_be_object():
    r = ap.parse_actions('{"actions":[{"tool":"send","args":"hi"}]}')
    assert not r.ok and any(e["code"] == "bad_args" for e in r.errors)


def test_fenced_json_block_extracted():
    raw = 'Sure, here you go:\n```json\n{"actions":[{"tool":"pin","args":{"text":"x"}}]}\n```\nthanks'
    r = ap.parse_actions(raw)
    assert r.ok and r.source == "json-fenced"
    assert ap.actions_to_metta(r.actions) == '((pin "x"))'


def test_too_many_actions_rejects_batch():
    items = ",".join('{"tool":"pin","args":{"text":"%d"}}' % i for i in range(8))
    r = ap.parse_actions('{"actions":[' + items + "]}")
    assert not r.ok
    assert any(e["code"] == "too_many_actions" for e in r.errors)
    assert r.actions == []


def test_arg_aliases_tolerated():
    r = ap.parse_actions(
        '{"actions":[{"tool":"tavily-search","args":{"query":"btc"}},'
        '{"tool":"shell","args":{"cmd":"ls"}}]}'
    )
    assert ap.actions_to_metta(r.actions) == '((tavily-search "btc") (shell "ls"))'


# --- authorization / tool gating -----------------------------------------

def test_disabled_tools_allow_all_by_default():
    _set_env("OMEGACLAW_DISABLED_TOOLS", None)
    r = ap.parse_actions('{"actions":[{"tool":"shell","args":{"command":"ls"}}]}')
    authd, errs = ap.authorize_actions(r.actions)
    assert errs == [] and len(authd) == 1


def test_shell_gated_when_disabled():
    _set_env("OMEGACLAW_DISABLED_TOOLS", "shell")
    try:
        r = ap.parse_actions('{"actions":[{"tool":"shell","args":{"command":"rm -rf /"}}]}')
        authd, errs = ap.authorize_actions(r.actions)
        assert authd == [] and errs and errs[0]["code"] == "tool_disabled"
    finally:
        _set_env("OMEGACLAW_DISABLED_TOOLS", None)


def test_metta_gated_when_disabled():
    _set_env("OMEGACLAW_DISABLED_TOOLS", "metta")
    try:
        r = ap.parse_actions('{"actions":[{"tool":"metta","args":{"expr":"(+ 1 2)"}}]}')
        authd, errs = ap.authorize_actions(r.actions)
        assert authd == [] and errs and errs[0]["code"] == "tool_disabled"
    finally:
        _set_env("OMEGACLAW_DISABLED_TOOLS", None)


def test_disabled_tool_rejects_whole_batch_in_render():
    _set_env("OMEGACLAW_ACTION_PROTOCOL", "json")
    _set_env("OMEGACLAW_DISABLED_TOOLS", "shell")
    try:
        out = ap.parse_and_render_metta(
            '{"actions":[{"tool":"shell","args":{"command":"ls"}},'
            '{"tool":"send","args":{"text":"hi"}}]}'
        )
        assert not out.startswith("(")  # re-prompt, nothing executed
        assert "disabled" in out
    finally:
        _set_env("OMEGACLAW_ACTION_PROTOCOL", None)
        _set_env("OMEGACLAW_DISABLED_TOOLS", None)


def test_high_risk_tools_listed_in_prompt():
    block = ap.output_format_block()
    assert "shell" in block and "metta" in block
    assert "High-risk" in block


# --- mode dispatch (parse_and_render_metta) ------------------------------

def test_json_mode_renders_valid_actions():
    _set_env("OMEGACLAW_ACTION_PROTOCOL", "json")
    try:
        out = ap.parse_and_render_metta('{"actions":[{"tool":"send","args":{"text":"hi"}}]}')
        assert out == '((send "hi"))'
    finally:
        _set_env("OMEGACLAW_ACTION_PROTOCOL", None)


def test_json_mode_empty_actions_is_nothing():
    _set_env("OMEGACLAW_ACTION_PROTOCOL", "json")
    try:
        assert ap.parse_and_render_metta('{"actions":[]}') == "()"
    finally:
        _set_env("OMEGACLAW_ACTION_PROTOCOL", None)


def test_json_mode_garbage_returns_retry_string_not_paren():
    _set_env("OMEGACLAW_ACTION_PROTOCOL", "json")
    try:
        out = ap.parse_and_render_metta("totally not json")
        assert not out.startswith("(")
        assert "ACTION_PROTOCOL_ERROR" in out
    finally:
        _set_env("OMEGACLAW_ACTION_PROTOCOL", None)


def test_default_mode_is_json():
    _set_env("OMEGACLAW_ACTION_PROTOCOL", None)
    assert ap.get_mode() == "json"


def test_legacy_mode_uses_balance_parentheses():
    _set_env("OMEGACLAW_ACTION_PROTOCOL", "legacy")
    try:
        out = ap.parse_and_render_metta("send hello world")
        assert out == '((send "hello world"))'
    finally:
        _set_env("OMEGACLAW_ACTION_PROTOCOL", None)


def test_auto_mode_prefers_json_then_falls_back():
    _set_env("OMEGACLAW_ACTION_PROTOCOL", "auto")
    try:
        j = ap.parse_and_render_metta('{"actions":[{"tool":"send","args":{"text":"hi"}}]}')
        assert j == '((send "hi"))'
        legacy = ap.parse_and_render_metta("send hello world")
        assert legacy == '((send "hello world"))'
    finally:
        _set_env("OMEGACLAW_ACTION_PROTOCOL", None)


def test_auto_mode_does_not_fall_back_on_invalid_json():
    # JSON present but invalid (unknown tool) must NOT leak to the legacy parser.
    _set_env("OMEGACLAW_ACTION_PROTOCOL", "auto")
    try:
        out = ap.parse_and_render_metta('{"actions":[{"tool":"rm-rf","args":{"text":"/"}}]}')
        assert not out.startswith("(")
        assert "rm-rf" not in out or "unknown tool" in out
    finally:
        _set_env("OMEGACLAW_ACTION_PROTOCOL", None)


def test_output_format_block_lists_all_tools():
    block = ap.output_format_block()
    for tool in ap.ALLOWED_TOOLS:
        assert tool in block
    assert "actions" in block


def _run_standalone():
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    if failures:
        print(f"\n{failures} test(s) failed")
        sys.exit(1)
    print("\nall action_protocol unit tests passed")


if __name__ == "__main__":
    _run_standalone()
