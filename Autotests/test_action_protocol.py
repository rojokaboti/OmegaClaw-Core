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


def _set_mode(mode):
    if mode is None:
        os.environ.pop("OMEGACLAW_ACTION_PROTOCOL", None)
    else:
        os.environ["OMEGACLAW_ACTION_PROTOCOL"] = mode


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
    assert r.errors


def test_unknown_tool_rejected():
    r = ap.parse_actions('{"actions":[{"tool":"rm-rf","args":{"text":"/"}}]}')
    assert not r.ok
    assert any("unknown tool" in e for e in r.errors)


def test_unknown_tool_never_reaches_metta_render():
    # Even mixed with a valid action, the unknown one must be dropped.
    raw = (
        '{"actions":[{"tool":"rm-rf","args":{"text":"/"}},'
        '{"tool":"send","args":{"text":"ok"}}]}'
    )
    r = ap.parse_actions(raw)
    rendered = ap.actions_to_metta(r.actions)
    assert "rm-rf" not in rendered
    assert rendered == '((send "ok"))'


def test_multiline_send_preserved_exactly():
    r = ap.parse_actions(
        '{"actions":[{"tool":"send","args":{"text":"Here are the planets:\\n1. Mercury\\n2. Venus"}}]}'
    )
    # Escaped newlines, exactly like helper.balance_parentheses output.
    assert ap.actions_to_metta(r.actions) == '((send "Here are the planets:\\n1. Mercury\\n2. Venus"))'


def test_send_with_embedded_quotes_escaped():
    r = ap.parse_actions('{"actions":[{"tool":"send","args":{"text":"say \\"hi\\" now"}}]}')
    assert ap.actions_to_metta(r.actions) == '((send "say \\"hi\\" now"))'


def test_write_file_requires_path_and_content():
    ok = ap.parse_actions('{"actions":[{"tool":"write-file","args":{"path":"t.txt","content":"hi"}}]}')
    assert ok.ok and ap.actions_to_metta(ok.actions) == '((write-file "t.txt" "hi"))'

    no_content = ap.parse_actions('{"actions":[{"tool":"write-file","args":{"path":"t.txt"}}]}')
    assert not no_content.ok and any("content" in e for e in no_content.errors)

    no_path = ap.parse_actions('{"actions":[{"tool":"write-file","args":{"content":"hi"}}]}')
    assert not no_path.ok and any("path" in e for e in no_path.errors)


def test_append_file_requires_path_and_content():
    r = ap.parse_actions('{"actions":[{"tool":"append-file","args":{"path":"log.txt","content":"line"}}]}')
    assert r.ok and ap.actions_to_metta(r.actions) == '((append-file "log.txt" "line"))'


def test_metta_requires_expr():
    bad = ap.parse_actions('{"actions":[{"tool":"metta","args":{}}]}')
    assert not bad.ok and any("expr" in e for e in bad.errors)

    good = ap.parse_actions('{"actions":[{"tool":"metta","args":{"expr":"(|- a b)"}}]}')
    assert good.ok and ap.actions_to_metta(good.actions) == '((metta "(|- a b)"))'


def test_non_string_arg_rejected():
    r = ap.parse_actions('{"actions":[{"tool":"send","args":{"text":123}}]}')
    assert not r.ok and any("must be a string" in e for e in r.errors)


def test_args_must_be_object():
    r = ap.parse_actions('{"actions":[{"tool":"send","args":"hi"}]}')
    assert not r.ok and any("must be an object" in e for e in r.errors)


def test_fenced_json_block_extracted():
    raw = 'Sure, here you go:\n```json\n{"actions":[{"tool":"pin","args":{"text":"x"}}]}\n```\nthanks'
    r = ap.parse_actions(raw)
    assert r.ok and r.source == "json-fenced"
    assert ap.actions_to_metta(r.actions) == '((pin "x"))'


def test_max_five_actions_enforced():
    items = ",".join('{"tool":"pin","args":{"text":"%d"}}' % i for i in range(8))
    r = ap.parse_actions('{"actions":[' + items + "]}")
    assert len(r.actions) == ap.MAX_ACTIONS
    assert any("truncated" in e for e in r.errors)


def test_arg_aliases_tolerated():
    # tavily-search accepts "query" as alias for "text"; shell accepts "cmd".
    r = ap.parse_actions(
        '{"actions":[{"tool":"tavily-search","args":{"query":"btc"}},'
        '{"tool":"shell","args":{"cmd":"ls"}}]}'
    )
    assert ap.actions_to_metta(r.actions) == '((tavily-search "btc") (shell "ls"))'


# --- mode dispatch (parse_and_render_metta) ------------------------------

def test_json_mode_renders_valid_actions():
    _set_mode("json")
    try:
        out = ap.parse_and_render_metta('{"actions":[{"tool":"send","args":{"text":"hi"}}]}')
        assert out == '((send "hi"))'
    finally:
        _set_mode(None)


def test_json_mode_empty_actions_is_nothing():
    _set_mode("json")
    try:
        assert ap.parse_and_render_metta('{"actions":[]}') == "()"
    finally:
        _set_mode(None)


def test_json_mode_garbage_returns_retry_string_not_paren():
    _set_mode("json")
    try:
        out = ap.parse_and_render_metta("totally not json")
        assert not out.startswith("(")  # routes loop into the re-prompt branch
        assert "ACTION_PROTOCOL_ERROR" in out
    finally:
        _set_mode(None)


def test_default_mode_is_json():
    _set_mode(None)
    assert ap.get_mode() == "json"


def test_legacy_mode_uses_balance_parentheses():
    _set_mode("legacy")
    try:
        # Loose text the legacy heuristic parser understands but JSON would reject.
        out = ap.parse_and_render_metta("send hello world")
        assert out == '((send "hello world"))'
    finally:
        _set_mode(None)


def test_auto_mode_prefers_json_then_falls_back():
    _set_mode("auto")
    try:
        j = ap.parse_and_render_metta('{"actions":[{"tool":"send","args":{"text":"hi"}}]}')
        assert j == '((send "hi"))'
        legacy = ap.parse_and_render_metta("send hello world")
        assert legacy == '((send "hello world"))'
    finally:
        _set_mode(None)


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
