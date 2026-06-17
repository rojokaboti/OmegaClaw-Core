"""Helper for building JSON action-protocol responses in mock tests (Issue #1).

Under the strict-JSON default, the mock LLM must answer with JSON rather than
the old loose-text commands. ``act()`` builds that JSON from positional Python
values so tests stay readable and interpolation-safe (no brace-escaping in
f-strings)::

    llm.set_answer(prompt, act(("send", "Hello")))
    llm.set_answer(prompt, act(("shell", "mkdir -p /tmp/x"),
                               ("write-file", "/tmp/x/a.txt", "data")))

Positional values map to each tool's canonical arg keys (see
``action_protocol.ARG_SPEC``), so ``act()`` output renders to exactly the same
MeTTa s-expression the legacy parser produced for the equivalent text command.
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_SRC, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from action_protocol import ARG_SPEC  # noqa: E402

# Canonical positional key order per tool.
_POS_KEYS = {tool: [group[0] for group in groups] for tool, groups in ARG_SPEC.items()}


def act(*specs):
    """Return a JSON action-protocol string for one or more ``(tool, *values)``
    tuples. Each value is assigned to the tool's canonical positional arg key."""
    actions = []
    for spec in specs:
        if isinstance(spec, str):
            spec = (spec,)
        tool = spec[0]
        values = spec[1:]
        keys = _POS_KEYS[tool]
        if len(values) > len(keys):
            raise ValueError(f"{tool}: too many args ({len(values)} > {len(keys)})")
        args = {keys[i]: values[i] for i in range(len(values))}
        actions.append({"tool": tool, "args": args})
    return json.dumps({"actions": actions}, ensure_ascii=False)
