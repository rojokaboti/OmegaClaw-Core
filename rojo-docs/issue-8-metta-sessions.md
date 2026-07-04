# Change Report — Issue #8: Session-Scoped Reasoning State

**Branch:** `feat/metta-sessions` (off `main`, which has #1–#7 merged)
**Issue:** #8 — "Add optional session-scoped MeTTa/AtomSpace reasoning state"

---

## 1. Why this change exists

`(metta $str)` is a stateless read-eval of one expression in the global `&self` space
(`src/skills.metta`), so multi-turn continuity (e.g. a FreeCiv game) has to be re-fed from
prompts/memory every turn instead of living as symbolic state. Issue #8 adds **named reasoning
sessions** — create / add-fact / infer / clear / snapshot — isolated from each other and from the
default `metta`, with **one session per FreeCiv game**, so premises accumulate once and are reused.

### Accuracy corrections (issue spec vs. runtime — verified)
- **Runtime is PeTTa (MeTTa on SWI-Prolog), not Hyperon.** There is **no named-AtomSpace API**
  (`new-space`/`add-atom`/`match`/`get-atoms` are absent; only `&self`), **no `hyperon`/`janus-swi`**,
  and the Python↔MeTTa bridge is **one-way** (`py-call`, MeTTa→Python). So the issue's *preferred*
  "real AtomSpace/MeTTa runner per session" is **not feasible**; we build its documented **fallback**:
  a Python store that accumulates premise expressions and replays them through the existing `(|- …)` path.
- **NAL/PLN inference is strictly two-premise** (`(|- a b)` — `lib_nal.metta`, `(|~ a b)` — `lib_pln.metta`).
  So "infer over a session" pairs the query with **each** stored premise (`(|- fact query)`), not a
  whole-space sweep.
- `(metta ...)` is **left byte-for-byte unchanged**; the session skills sit alongside it and reuse
  the same `sread`/`eval`/`swrite`/`repr` path.

## 2. Before → after

| | Before | After |
|---|---|---|
| Cross-turn symbolic state | none (`metta` is stateless) | named sessions persist premises across calls |
| Reuse of accumulated facts | re-fed from prompt/memory every turn | added once; replayed from the store |
| Isolation | single global `&self` | sessions isolated by id (no leakage) |
| FreeCiv continuity | per-turn re-derivation | one session per `game_id`, seeded by `freeciv-observe`, snapshotted per turn |
| Agent skills | `metta` | + `metta-session-create/add/infer/clear/snapshot` |

## 3. Files changed

| File | Change |
|---|---|
| `src/metta_sessions.py` *(new, stdlib-only)* | The store: `OrderedDict` session_id→facts (LRU), `create`/`add_fact`/`facts`/`clear`/`snapshot`/`info`/`reset`, and `infer_program(sid, query)` → a `(unique-atom (collapse (superpose ((|- f query) …))))` program string. Env limits (max sessions LRU, max facts FIFO, max snapshot bytes). Self-test. |
| `src/metta_sessions.metta` *(new)* | MeTTa handlers: `metta-session-{create,add,clear,snapshot}` → `py-call`; `metta-session-infer` evals the assembled program through the real `|-` path. |
| `src/helper.py`, `src/action_protocol.py`, `src/skills.metta` | 3-place registration of the five tools (`LLM_COMMANDS`, `ARG_SPEC` + self-tests, `getSkills`). |
| `lib_omegaclaw.metta` | Register `./src/metta_sessions.py` and `./src/metta_sessions`. |
| `src/freeciv_tool.py` | `observe(...)` best-effort seeds `freeciv:<game_id>` with the turn's PLN premises + snapshots (never breaks observation). |
| `benchmarks/metta_sessions_{fixtures,benchmark}.py` + `_results.{md,json}` *(new)* | KPI A/B (stateless baseline vs session store) with `sys.exit(1)` gate; committed results. |
| `Autotests/test_metta_sessions.py` *(new)* + `Autotests/run_mandatory` | 9 host tests (lifecycle, infer assembly, **isolation**, LRU/FIFO limits, snapshot, freeciv seed); wired into the mandatory suite. |
| `.github/workflows/common.yml` | Phase-1 runs `python ../src/metta_sessions.py`. |

## 4. KPI results (`benchmarks/metta_sessions_results.md`)

Two independent multi-turn games driven through the real `src/metta_sessions.py`:

| Metric | baseline | candidate |
|---|---|---|
| Fact preservation across turns | 0.00 | **1.00** |
| Cross-session leakage (facts) | 3 | **0** |
| Premise transmissions (all turns) | 14 | 8 |
| **Premise re-send reduction** | — | **43%** |

Candidate preserves 100% of session premises with **zero** cross-session leakage and cuts repeated
premise re-transmission by **43%** (≥30% gate) — each fact is added once and replayed from the
store, vs. the stateless baseline re-sending all accumulated premises every turn.

## 5. End-to-end validation
- `python3 src/metta_sessions.py` → `metta_sessions self-tests passed`.
- `python3 Autotests/test_metta_sessions.py` → 9/9; full mandatory pure suite **133 passed, 6
  skipped** (chroma-backed memory tests skip on host) — #6/#7/#8 coexist.
- `python3 benchmarks/metta_sessions_benchmark.py` → `KPI GATE: PASSED`; re-run → results identical.
- Guards: `python3 src/action_protocol.py` / `src/freeciv_tool.py` self-tests still pass (3-place
  wiring + freeciv seed didn't change observation/validation).
- **In-container (documented, needs PeTTa):** `(metta-session-create g1)`,
  `(metta-session-add g1 "((--> a b) (stv 1.0 0.9))")`, then
  `(metta-session-infer g1 "((--> b c) (stv 1.0 0.9))")` derives a conclusion via `|-`; a second
  session stays isolated; a snapshot appears under `memory/traces/sessions/`.

## 6. Deferred / notes
- **Snapshots use an independent stdlib JSONL writer** (`memory/traces/sessions/`). Since #7's
  `tracing.py` is now on `main`, snapshots could optionally route through `tracing.emit`/`set_context`
  for one unified trace stream — a trivial future swap, intentionally not coupled here.
- Session facts are **symbolic PLN atoms** (not raw text), so no secret redaction is applied to
  snapshots; wire `redaction.redact_secrets` if free-text bodies are ever stored.

## 7. Reviewer guide — test & compare against the previous version

### A. Read the core diff (no build)
```bash
git checkout feat/metta-sessions
git diff main --stat
git diff main -- src/skills.metta src/action_protocol.py src/freeciv_tool.py
```

### B. Pure-Python checks (seconds, no Docker)
```bash
python3 src/metta_sessions.py                 # metta_sessions self-tests passed
python3 benchmarks/metta_sessions_benchmark.py # KPI GATE: PASSED
python3 Autotests/test_metta_sessions.py       # 9/9 standalone
python3 src/action_protocol.py                 # guard: 3-place wiring intact
```

### C. Hand demo — persistence + isolation (seconds)
```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "src")
import metta_sessions as ms
ms.add_fact("g1", "((--> CityA LowFood) (stv 1.0 0.99))")
ms.add_fact("g1", "((--> Unit7 Settler) (stv 1.0 0.99))")
print("infer program:", ms.infer_program("g1", "((--> LowFood BuildGranary) (stv 1.0 0.9))"))
ms.add_fact("g2", "((--> CityB HighProd) (stv 1.0 0.99))")
print("g1 facts:", len(ms.facts("g1")), "| g2 facts:", len(ms.facts("g2")), "| isolated:", set(ms.facts("g1")).isdisjoint(ms.facts("g2")))
PY
```

### D. In-container (Docker) — real inference
```bash
docker build -t omegaclaw:local .
# drive the loop; metta-session-create/add/infer over (|- ...) returns a derived conclusion.
```

### E. Compare to `main`
```bash
git show main:src/metta_sessions.py     # does not exist on main
git ls-tree main -- src/metta_sessions.metta   # absent
git diff main --stat
```

## 7b. PR #29 review fixes
- **Evaluator tools share metta's control surface.** `metta-session-infer` (and
  `metta-session-add`, which stores expressions infer later evaluates) reach the same
  `sread`/`eval` path as `metta`, so they are now in `HIGH_RISK_TOOLS` and in
  `tool_policy._DEFAULT_RISK` (high), and **disabling `metta` (`OMEGACLAW_DISABLED_TOOLS=metta`)
  now also disables them** — closing a bypass where a metta-gated deployment could still reach
  evaluation. Non-evaluator session tools (create/clear/snapshot) stay available. Covered by an
  `action_protocol` self-test.
- **`add_fact` is idempotent.** Re-adding an expression already in a session is a no-op
  (`FACT-DUP`), so re-seeding an unchanged FreeCiv state every `observe` no longer doubles the
  store or evicts genuine history under the fact cap (reviewer probe: was 4→8, now 4→4). Covered
  by `test_add_fact_is_idempotent` + a double-observe assertion.

## 8. Risk / rollback
- Additive: `(metta ...)` and all existing tools/skills unchanged; new skills sit alongside. The
  store is process-local with best-effort IO (a store/snapshot failure never breaks the loop) and
  env-configurable size/TTL caps to bound memory.
- The freeciv `observe` seed is wrapped best-effort — never breaks observation/validation.
- Not pushed until ready; open a PR against `rojokaboti/OmegaClaw-Core`.
