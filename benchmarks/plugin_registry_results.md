# Plugin-Registry KPI Benchmark — Issue #15

Toy-plugin corpus (`plugin_registry_fixtures.build_plugins`: a working calculator, an echoer, a failing plugin, and a duplicate-tool plugin) through the real `src/plugin_registry.py`.

- **baseline** = no registry: adding one callable tool edits **4 core files** (`getSkills` + MeTTa equation + `LLM_COMMANDS` + `ARG_SPEC`), 0 runtime plugins.
- **candidate** = a manifest-declared plugin ships tools with **0 core edits**.

| Metric | baseline | candidate |
| --- | --- | --- |
| Core files edited to add a tool (target 0) | 4 | 0 |
| Plugins loaded from a manifest dir | 0 | 3 |
| Tools registered | 0 | 2 |
| Plugin tool invocation correct (calc 2+3*4 = 14) | False | True |
| Disabled plugin contributes nothing | False | True |
| Failing plugin isolated (others still load) | False | True |
| Duplicate tool name rejected | False | True |

Candidate adds a working, invocable tool with **0 core-file edits** (baseline needs 4); disabled plugins contribute nothing; a failing plugin is isolated with a reported error; duplicate tool names are rejected.

Reproduce: `python3 benchmarks/plugin_registry_benchmark.py`
