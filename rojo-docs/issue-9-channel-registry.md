# Change Report — Issue #9: Channel Registry Abstraction

**Branch:** `feat/channel-registry` (off `main`, which has #1–#8 merged)
**Issue:** #9 — "Refactor communication channels into a registry abstraction"

---

## 1. Why this change exists

Channel dispatch lived as three parallel nested-`if` chains in `src/channels.metta` —
`initChannels` (start), `receive`, and `send` — each branching on `(commchannel)` across
irc/telegram/slack/mattermost with **mock as the else-fallthrough**. Adding a channel meant editing
all three branches. Issue #9 moves selection into a Python **channel registry** with a thin MeTTa
facade, so adding a channel is registering one object — behavior otherwise unchanged.

### Accuracy notes (verified against the runtime)
- Runtime is PeTTa; dispatch is MeTTa py-calling `channels/*.py`. Each channel keeps module-level
  global state (threads/sockets), so the registry calls the **real module functions** (module
  identity preserved) — it never copies them.
- `channels/mock.py` is test-only (not imported in `lib_omegaclaw.metta`) → the registry imports
  channel modules **lazily** (only when selected) and stays import-light, so it is host-unit-testable.
- **Unknown `commchannel` -> mock** is preserved (the old else-branch; the Docker mock suite relies on
  `-t test` routing to mock).
- The `&lastsend` duplicate-send shield + `(cut)` stay in the MeTTa facade (lowest risk); the registry
  does dispatch + the outgoing newline escape (`"\n" -> "\\n"`) that `send` did before.

## 2. Before → after

| | Before | After |
|---|---|---|
| Start dispatch | 4-deep nested `if` + mock else | one `channel_registry.start_channel` py-call |
| Receive dispatch | 4-deep nested `if` + mock else | one `channel_registry.receive` py-call |
| Send dispatch | 4-deep nested `if` + mock else | one `channel_registry.send` py-call (shield/`cut` kept) |
| Add a channel | edit 3 nested branches | `register(Channel(...))` — one object, 0 dispatch conditionals |
| Unknown channel | implicit else → mock | explicit `_resolve(...) -> mock` fallback (tested) |

## 3. Files changed

| File | Change |
|---|---|
| `src/channel_registry.py` *(new, stdlib-only, import-light)* | `Channel` dataclass + `CHANNELS` table (irc/telegram/slack/mattermost/mock); lazy `_import_channel`/`_lazy` (no import at registry import); per-channel `start` builders applying the current defaults; `register`, `list_channels`, `_resolve` (unknown→mock), `start_channel`/`receive`/`send` (send escapes newlines). Self-test. |
| `src/channels.metta` | The three nested-`if` dispatchers replaced by registry py-calls. `initChannels` keeps `configure`/`argk` CLI-arg resolution (now unconditional) + one `start_channel` call; `receive` is one call; `send` keeps `&lastsend` guard + `(cut)` and delegates dispatch. `search` unchanged. |
| `lib_omegaclaw.metta` | Register `./src/channel_registry.py`. |
| `benchmarks/channel_registry_{fixtures,benchmark}.py` + `_results.{md,json}` *(new)* | Maintainability KPI (add-a-channel cost) with `sys.exit(1)` gate; committed results. |
| `Autotests/test_channel_registry.py` *(new)* + `Autotests/run_mandatory` | 6 host tests (dispatch, unknown→mock, newline escape, per-channel config routing, one-object add); wired into the mandatory suite. |
| `.github/workflows/common.yml` | Phase-1 runs `python ../src/channel_registry.py`. |

## 4. KPI results (`benchmarks/channel_registry_results.md`)

Adding a config-less `echo` channel:

| Metric | baseline | candidate |
|---|---|---|
| **Dispatch conditionals to add a channel** | **3** | **0** |
| Dispatch sites edited (start/receive/send) | 3 | 0 |
| Non-blank lines to add a channel | 9 | 2 |
| New channel dispatches (round-trip) | n/a | True |
| Existing channels still resolve | n/a | 5/5 |
| Unknown channel → mock (explicit) | (else branch) | True |

Adding a channel drops from 3 dispatch conditionals across 3 sites to **0** (one registry object),
with all five existing channels still resolving and unknown→mock preserved. The candidate's
one-object add is proven by registering `echo` and exercising start/receive/send end-to-end.

## 5. End-to-end validation
- `python3 src/channel_registry.py` → `channel_registry self-tests passed`.
- `python3 Autotests/test_channel_registry.py` → 6/6; full mandatory pure suite **140 passed,
  6 skipped** (chroma-backed memory tests skip on host) — no regressions across #1–#9.
- `python3 benchmarks/channel_registry_benchmark.py` → `KPI GATE: PASSED`; re-run → identical.
- **In-container (documented, needs the stack):** `-t test` still routes to mock (existing Docker
  mock suite passes); irc/telegram/slack/mattermost still start/receive/send via the registry.

## 6. Reviewer guide — test & compare against the previous version

### A. Read the core diff (no build)
```bash
git checkout feat/channel-registry
git diff main --stat
git diff main -- src/channels.metta          # 3 dispatchers -> registry py-calls; shield/cut kept
```

### B. Pure-Python checks (seconds, no Docker)
```bash
python3 src/channel_registry.py               # channel_registry self-tests passed
python3 benchmarks/channel_registry_benchmark.py   # KPI GATE: PASSED
python3 Autotests/test_channel_registry.py    # 6/6 standalone
```

### C. Hand demo — add a channel in one object (seconds)
```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "src")
import channel_registry as cr
log = []
cr.register(cr.Channel("echo", start=lambda c: log.append(("start", c)),
                       receive=lambda: "hi", send=lambda m: log.append(("send", m))))
cr.start_channel("echo"); print("recv:", cr.receive("echo")); cr.send("echo", "a\nb")
print("log:", log)                                  # send escaped to 'a\\nb'
print("unknown ->", cr._resolve("nope").name)       # mock
PY
```

### D. In-container (Docker)
```bash
docker build -t omegaclaw:local .
./scripts/omegaclaw start -p Test -t test -d omegaclaw:local   # -t test -> mock branch still works
```

### E. Compare to `main`
```bash
git show main:src/channel_registry.py         # does not exist on main
git diff main -- src/channels.metta            # nested-if -> registry
git diff main --stat
```

## 7. Risk / rollback
- Behavior-preserving refactor: same channel functions + defaults, mock fallthrough kept, `&lastsend`
  shield + `(cut)` + newline escape preserved. Registry is import-light + lazy (no new import-time
  deps; `mock.py` still loaded only when selected).
- Unknown/failed channel resolves to mock — never a hard crash on an unrecognized `commchannel`.
- Not pushed until ready; open a PR against `rojokaboti/OmegaClaw-Core`.
