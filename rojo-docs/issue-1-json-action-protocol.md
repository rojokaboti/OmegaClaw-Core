# Change Report — Issue #1: Structured JSON Action Protocol

**Branch:** `feat/json-action-protocol` (2 commits, not yet pushed)
**Baseline:** `main` (`a116e4b`)
**Scope:** 50 files changed, +3185 / −82

---

## 1. Why this change exists

The agent loop asked the LLM to emit **loose text tool commands** and then
repaired/parsed them with a heuristic (`helper.balance_parentheses`). That parser:

- never validated tool names — a hallucinated tool (e.g. `rm-rf /`) was passed
  straight to `eval`;
- relied on fragile string surgery for quoting, multi-arg file ops, and multiline
  `send`;
- failed *before* any MeTTa/NAL/PLN reasoning, making tool execution the biggest
  reliability risk.

GitHub **Issue #1** proposed replacing this with a validated **JSON action
protocol** between LLM output and MeTTa skill evaluation. This pass implements it,
converts the test suite, proves the improvement with a benchmark, and validates it
end-to-end inside the real container.

---

## 2. What the agent does now (before → after)

**Before** — the LLM was told to emit up to 5 loose lines:
```
shell mkdir -p /tmp/x
write-file /tmp/x/a.txt hello world
```
…then `balance_parentheses` heuristically rebuilt `((shell "...") (write-file "..." "..."))`.

**After** — the LLM is told to emit one JSON object, which is parsed and validated
before any MeTTa call:
```json
{"actions":[
  {"tool":"shell","args":{"command":"mkdir -p /tmp/x"}},
  {"tool":"write-file","args":{"path":"/tmp/x/a.txt","content":"hello world"}}
]}
```
The Python layer validates each action (known tool, object args, required keys,
≤ N actions) and renders the **exact same** s-expression shape `sread` already
expects: `((shell "mkdir -p /tmp/x") (write-file "/tmp/x/a.txt" "hello world"))`.
Unknown tools and malformed JSON never reach `eval`.

### Operating modes (env `OMEGACLAW_ACTION_PROTOCOL`)
| Mode | Behavior |
|---|---|
| `json` (default) | Strict JSON only. Invalid/garbage → structured error → model re-prompted. |
| `auto` | JSON if present/valid; otherwise fall back to the legacy parser. Migration aid. |
| `legacy` | Original `balance_parentheses` heuristic only. Escape hatch. |

`OMEGACLAW_MAX_ACTIONS` (default `5`) caps actions per turn.

---

## 3. Files changed

### New — core
| File | Purpose |
|---|---|
| `src/action_protocol.py` | The protocol: `parse_actions`, `validate_action`, `actions_to_metta`, `parse_and_render_metta`, `output_format_block`. Strict JSON + fenced-block extraction; tool/arg validation; action cap; mode dispatch. Has a `__main__` self-test. |

### Modified — wiring
| File | Change |
|---|---|
| `src/loop.metta` | Provider output now flows through `action_protocol.parse_and_render_metta` (was `helper.balance_parentheses`). The `OUTPUT_FORMAT` prompt block is generated from the protocol's `ARG_SPEC`. Error/retry strings reworded to JSON guidance. |
| `lib_omegaclaw.metta` | Registers `./src/action_protocol.py` as a Python module for MeTTa. |
| `src/helper.py` | Unchanged logic — `balance_parentheses` kept as the legacy/auto parser and as the benchmark baseline. |
| `entrypoint.sh` | **Production fix:** added `OMEGACLAW_ACTION_PROTOCOL` and `OMEGACLAW_MAX_ACTIONS` to the `SAFE_VARS` allowlist. The env scrub (`env -i`) was silently stripping them, making the documented overrides no-ops in the container. |
| `scripts/omegaclaw` | Passes the two flags through `docker run -e …`. |
| `README.md` | Documents both env vars. |

### New — tests & experiment
| File | Purpose |
|---|---|
| `Autotests/test_action_protocol.py` | 23 pure-Python unit tests (malformed JSON, unknown tool, multiline send, file ops, metta expr, action cap, mode dispatch, aliases). |
| `Autotests/mock/actions.py` | `act(*specs)` helper that builds JSON answers from Python values (no f-string brace-escaping). |
| `Autotests/mock/test_actions_equivalence.py` | Proves `parse_and_render_metta(act(...)) == balance_parentheses(legacy)` for every tool — so converting a fixture is provably behavior-preserving. |
| `benchmarks/fixtures.py` | 54 synthetic LLM outputs across 7 categories. |
| `benchmarks/run_benchmark.py` | A/B runner (baseline vs json vs auto) + KPI acceptance gate. |
| `benchmarks/results.md` / `results.json` | Committed before/after numbers. |
| `benchmarks/README.md` | Experiment methodology. |

### Modified — fixtures (the bulk)
All **53 canned LLM responses across 34 `Autotests/mock/test_*_mock.py` files** were
converted from loose-text s-expressions to JSON via `act()`. Each conversion was
verified to render byte-identically to the original (see equivalence test).
Two notable adaptations:
- `test_run_repeated_mock.py`: 10 separate `shell` calls → one shell loop (fits the
  5-action cap; the test asserts on output lines, not call count).
- `test_complex_weather_flow_mock.py` / `test_create_script_mock.py`: script bodies
  use real newlines that round-trip through JSON to the written file.

`Autotests/run_mandatory` and `.github/workflows/common.yml` now also run the new
pure-Python suites and `python ../src/action_protocol.py` self-test.

---

## 4. KPI results (`benchmarks/results.md`)

Same 54-fixture corpus through each parser:

| Metric | Baseline (`balance_parentheses`) | Candidate `json` (default) | Candidate `auto` |
|---|---|---|---|
| Overall parse success rate | 29.6% | 72.2% | 83.3% |
| Reject (validation) success rate | 6.7% | 100% | 40% |
| Parse failures | 38 | **15 (−60.5%)** | 9 (−76.3%) |
| **Unknown-tool false-accepts** | 38 | **0** | 9 |
| False rejects (lost legit action) | 24 | 15 | 0 |

**Headline:** strict-JSON cuts parse failures ~60% and eliminates unsafe
unknown-tool execution entirely (38 → 0).

---

## 5. End-to-end validation (already run)

Built `omegaclaw:local` from this branch and ran the in-container mock suite:

- **`@run_mandatory`: 68 passed, 0 failed** (4m24s)
- **`@run_optional`: 5 passed, 1 skipped, 0 failed** (51s; the skip is
  `git_push_to_remote`, which self-skips without push credentials — same as `main`)

The live container prompt showed the JSON `OUTPUT_FORMAT` block, and real tool
execution (file writes, git, metta, memory) was driven by `{"actions":[…]}`
answers — confirming the baked local code ran (not the remote `git-import!`).

---

## 6. Reviewer guide — test & compare against the previous version

Prerequisites: Docker reachable (member of the `docker` group), Python 3.12, and
`pytest` on the host. If you have no pip: `curl -fsSL https://bootstrap.pypa.io/get-pip.py | python3 - --user --break-system-packages && python3 -m pip install --user --break-system-packages pytest`.

### A. Read the core diff first (fast, no build)
```bash
git checkout feat/json-action-protocol
git diff main -- src/action_protocol.py src/loop.metta lib_omegaclaw.metta entrypoint.sh scripts/omegaclaw
```
Focus on `parse_and_render_metta` (the seam), the `loop.metta:65` call site swap,
and the `SAFE_VARS` line in `entrypoint.sh`.

### B. Run the pure-Python checks (seconds, no Docker)
```bash
python3 src/action_protocol.py            # module self-tests
python3 src/helper.py                     # legacy baseline still green
python3 Autotests/test_action_protocol.py # 23 unit tests (standalone runner)
( cd Autotests/mock && python3 test_actions_equivalence.py )   # fixture-conversion proof
```
The equivalence script is the key reassurance: it shows each JSON answer renders to
the **identical** MeTTa s-expression the old parser produced.

### C. Reproduce the KPI experiment (seconds)
```bash
python3 benchmarks/run_benchmark.py
```
Compare the printed table / `benchmarks/results.md` to the claims in §4. The script
exits non-zero if the candidate ever regresses parse failures or accepts an unknown
tool — so a clean exit *is* the gate.

### D. Demonstrate the before/after parser behavior by hand (seconds)
```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "src")
import os; os.environ["OMEGACLAW_ACTION_PROTOCOL"]="json"
import action_protocol as ap
from helper import balance_parentheses

danger = "rm-rf /etc"                       # an unknown/hallucinated tool, loose text
print("OLD (baseline):", balance_parentheses(danger))         # -> passes it through to eval
js = '{"actions":[{"tool":"rm-rf","args":{"text":"/etc"}}]}'
print("NEW (json)    :", ap.parse_and_render_metta(js))       # -> ACTION_PROTOCOL_ERROR, never eval'd
PY
```
Expected: the old parser emits `((rm-rf "/etc"))` (would reach `eval`); the new one
returns a structured error and renders nothing.

### E. Full end-to-end in-container run (heavy: ~15–40 min build, then ~5 min tests)
```bash
# 1. Build the image from the working tree (bakes in the branch code)
docker build -t omegaclaw:local .

# 2. Start the agent as the mock 'Test' provider, KB import off
TEST_SERVER_IP=host.docker.internal IMPORT_KB_ON_START=0 \
  ./scripts/omegaclaw start -p Test -t test -d omegaclaw:local

# 3. Wait until ready (agent loads the embedding model on first iteration)
for i in $(seq 1 90); do docker logs omegaclaw 2>&1 | grep -qE "CHARS_SENT: [0-9]+" && { echo READY; break; }; sleep 2; done

# 4. Confirm the running code is this branch (not the git-import remote)
docker run --rm --entrypoint sh omegaclaw:local -c 'sed -n 1,3p /PeTTa/repos/OmegaClaw-Core/src/action_protocol.py'

# 5. Run the suites
( cd Autotests && python3 -m pytest -s -v @run_mandatory )   # expect: all passed
( cd Autotests && python3 -m pytest -s -v @run_optional )    # expect: passed, git_push skipped

# 6. Teardown
./scripts/omegaclaw stop      # or: ./scripts/omegaclaw clean  (also drops memory volume)
```
> If your shell session isn't yet in the `docker` group, wrap each docker/pytest
> command in `sg docker -c "..."`.

### F. Compare to the previous version (A/B the two branches)
To see that `main` still uses the loose-text format and lacks validation:
```bash
git stash || true
git checkout main
grep -n "OUTPUT_FORMAT" src/loop.metta          # old loose-line instructions
grep -n "balance_parentheses" src/loop.metta    # old direct call
python3 src/helper.py                            # baseline parser tests
git checkout feat/json-action-protocol           # back to the new version
git diff main --stat                              # full file-level overview
```
Optional: build `omegaclaw:main` from `main` and run `@run_mandatory` there — the
suite on `main` uses loose-text canned answers; on this branch they're JSON. Both
should pass, which is the point: behavior is preserved while the protocol hardens.

---

## 7. Risk / rollback notes

- **Backward compatibility:** `auto` and `legacy` modes preserve the old parser;
  flip via `OMEGACLAW_ACTION_PROTOCOL` (now actually honored after the `entrypoint.sh`
  fix). No code rollback needed to revert behavior.
- **Action cap:** default 5 (`OMEGACLAW_MAX_ACTIONS`). Raise it if an operator needs
  longer single-turn chains.
- **`helper.balance_parentheses` retained**, so nothing that depended on it is lost.
- **Not pushed:** the branch is local only; open a PR when ready.
