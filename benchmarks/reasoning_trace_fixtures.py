"""Deterministic fixtures for the reasoning-trace KPI benchmark (Issue #7).

A scripted 12-iteration reasoning/action loop (>= the issue's 10-turn minimum) covering the
event kinds a trace must capture: normal actions, multi-action turns, a parse error (unknown
tool), and policy denials (env-disabled + policy). Each fixture describes one loop iteration:

  input       : the iteration's input text (hashed into input_state_hash)
  prompt      : the prompt sent to the LLM
  response    : the raw LLM response
  parse       : {ok, source, version, tools, error_codes} — the action_parse outcome
  policy      : optional list of {tool, reason} denials for the turn
  latency_ms  : the LLM call latency to record
  result      : the iteration's final result text
"""

FIXTURES = [
    {"id": 1, "input": "hello", "prompt": "PROMPT: greet", "response": '{"actions":[{"tool":"send","args":{"text":"hi"}}]}',
     "parse": {"ok": True, "source": "json", "version": 1, "tools": ["send"], "error_codes": []},
     "latency_ms": 120, "result": "sent"},
    {"id": 2, "input": "what is 2+2", "prompt": "PROMPT: math", "response": '{"actions":[{"tool":"metta","args":{"expr":"(+ 2 2)"}}]}',
     "parse": {"ok": True, "source": "json", "version": 1, "tools": ["metta"], "error_codes": []},
     "latency_ms": 210, "result": "4"},
    {"id": 3, "input": "remember x", "prompt": "PROMPT: remember", "response": '{"actions":[{"tool":"remember","args":{"text":"x"}}]}',
     "parse": {"ok": True, "source": "json", "version": 1, "tools": ["remember"], "error_codes": []},
     "latency_ms": 95, "result": "REMEMBER-SUCCESS"},
    {"id": 4, "input": "bad tool", "prompt": "PROMPT: bad", "response": '{"actions":[{"tool":"rm-rf","args":{"text":"/"}}]}',
     "parse": {"ok": False, "source": "json", "version": 1, "tools": [], "error_codes": ["unknown_tool"]},
     "latency_ms": 80, "result": "ERROR: unknown tool"},
    {"id": 5, "input": "run shell", "prompt": "PROMPT: shell", "response": '{"actions":[{"tool":"shell","args":{"command":"ls"}}]}',
     "parse": {"ok": True, "source": "json", "version": 1, "tools": ["shell"], "error_codes": []},
     "policy": [{"tool": "shell", "reason": "disabled by OMEGACLAW_DISABLED_TOOLS"}],
     "latency_ms": 140, "result": "policy_denied"},
    {"id": 6, "input": "search", "prompt": "PROMPT: search", "response": '{"actions":[{"tool":"search","args":{"text":"weather"}}]}',
     "parse": {"ok": True, "source": "json", "version": 1, "tools": ["search"], "error_codes": []},
     "latency_ms": 300, "result": "results"},
    {"id": 7, "input": "multi", "prompt": "PROMPT: multi", "response": '{"actions":[{"tool":"shell","args":{"command":"ls"}},{"tool":"send","args":{"text":"done"}}]}',
     "parse": {"ok": True, "source": "json", "version": 1, "tools": ["shell", "send"], "error_codes": []},
     "latency_ms": 175, "result": "ok"},
    {"id": 8, "input": "write file", "prompt": "PROMPT: write", "response": '{"actions":[{"tool":"write-file","args":{"path":"/etc/x","content":"y"}}]}',
     "parse": {"ok": True, "source": "json", "version": 1, "tools": ["write-file"], "error_codes": []},
     "policy": [{"tool": "write-file", "reason": "path /etc/x outside allowed_roots"}],
     "latency_ms": 110, "result": "policy_denied"},
    {"id": 9, "input": "pin", "prompt": "PROMPT: pin", "response": '{"actions":[{"tool":"pin","args":{"text":"task"}}]}',
     "parse": {"ok": True, "source": "json", "version": 1, "tools": ["pin"], "error_codes": []},
     "latency_ms": 60, "result": "PIN-SUCCESS"},
    {"id": 10, "input": "fenced", "prompt": "PROMPT: fenced", "response": '```json\n{"actions":[{"tool":"send","args":{"text":"hey"}}]}\n```',
     "parse": {"ok": True, "source": "json-fenced", "version": 1, "tools": ["send"], "error_codes": []},
     "latency_ms": 130, "result": "sent"},
    {"id": 11, "input": "no json", "prompt": "PROMPT: prose", "response": "I will just talk without JSON.",
     "parse": {"ok": False, "source": "none", "version": None, "tools": [], "error_codes": ["no_json"]},
     "latency_ms": 90, "result": "ERROR: no JSON actions found"},
    {"id": 12, "input": "query claims", "prompt": "PROMPT: qc", "response": '{"actions":[{"tool":"query-claims","args":{"text":"food"}}]}',
     "parse": {"ok": True, "source": "json", "version": 1, "tools": ["query-claims"], "error_codes": []},
     "latency_ms": 160, "result": "claims"},
]


if __name__ == "__main__":
    n_err = sum(1 for f in FIXTURES if not f["parse"]["ok"])
    n_den = sum(len(f.get("policy", [])) for f in FIXTURES)
    print(f"{len(FIXTURES)} iterations; {n_err} parse errors; {n_den} policy denials")
