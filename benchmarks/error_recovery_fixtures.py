"""Fixtures for the structured error-recovery KPI benchmark (Issue #10).

One fixture per canonical error category. Each carries the raw failure input and
a `recovery` payload — a corrected input that should succeed once the model has
been given the repair hint — used to measure next-turn recovery.

`kind`:
  - "protocol": drive through action_protocol.parse_and_render_metta (parse,
    validate, authorize) — covers parse_error / unknown_tool /
    schema_validation_error / tool_policy_denied.
  - "runtime":  a validated action that raises during eval — modeled the way the
    MeTTa loop records it (errors.record_runtime_error), covering
    tool_runtime_error.
"""

FIXTURES = [
    {
        "category": "parse_error",
        "kind": "protocol",
        "raw": "Sure! I'll do that now.",  # prose, no JSON at all
        "recovery": '{"actions":[{"tool":"send","args":{"text":"done"}}]}',
    },
    {
        "category": "unknown_tool",
        "kind": "protocol",
        "raw": '{"actions":[{"tool":"rm-rf","args":{"text":"/etc"}}]}',
        "recovery": '{"actions":[{"tool":"shell","args":{"command":"ls /etc"}}]}',
    },
    {
        "category": "schema_validation_error",
        "kind": "protocol",
        "raw": '{"actions":[{"tool":"write-file","args":{"path":"/tmp/x"}}]}',  # missing content
        "recovery": '{"actions":[{"tool":"write-file","args":{"path":"/tmp/x","content":"hi"}}]}',
    },
    {
        "category": "tool_policy_denied",
        "kind": "protocol",
        "disabled": "shell",  # OMEGACLAW_DISABLED_TOOLS for this fixture
        "raw": '{"actions":[{"tool":"shell","args":{"command":"rm -rf /"}}]}',
        # not retryable with the same tool: recover by choosing a different, allowed action
        "recovery": '{"actions":[{"tool":"send","args":{"text":"cannot run that"}}]}',
    },
    {
        "category": "tool_runtime_error",
        "kind": "runtime",
        "action": '(write-file "/nonexistent-dir/deep/x" "data")',  # would raise on eval
        "recovery": '{"actions":[{"tool":"write-file","args":{"path":"/tmp/x","content":"data"}}]}',
    },
]
