# Change Report — Issue #6: Deterministic FreeCiv State-to-Atoms & Action Adapter

**Branch:** `feat/freeciv-adapter` (off `main`, which has #1–#5 merged)
**Issue:** #6 — "Build deterministic FreeCiv state-to-atoms and action adapter for benchmark runs"

---

## 1. Why this change exists

OmegaClaw's differentiator is auditable MeTTa/NAL/PLN reasoning, which needs reliable
**symbolic** input rather than raw text. Issue #6 asks for a deterministic adapter that turns
FreeCiv game state into normalized facts + atoms and **validates actions before submission**,
so benchmark runs are reproducible and illegal moves are rejected.

### Accuracy corrections (verified against the target repo)

- **There is no FreeCiv connector or state schema in this repo.** `game_state` existed only as a
  reserved `source_type` (confidence `1.0`) + `atoms_json` field in `src/memory_schema.py:34,37-43`
  — #5 explicitly deferred the producer. This adapter is the first `game_state` producer.
- **The target interface is [`taso-ventures/freeciv-llm`](https://github.com/taso-ventures/freeciv-llm)**
  (AGPL-3.0), a WebSocket/HTTP API where **the LLM agent is the client**. OmegaClaw *is* the agent.
  We align to its documented API and validation semantics; we **do not vendor** any of its code
  (it stays an external sibling runtime dependency — no license contamination of the MIT core).
- The issue's example atoms use NAL arrow-form `(--> ...)`, but the repo stores PLN word-form
  (`Autotests/test_memory_schema.py:42`). We emit **PLN word-form** `(Inheritance City_1 LowFood)`
  so a future live `remember_claim(..., atoms=[...])` producer stays byte-consistent.
- KPIs "win-rate/score +10–25%" and "reproducible generation +50%" require a **live game + LLM**
  and are not measurable in this repo; they move to the live E2E phase (§5). The **host KPI gate**
  is: byte-for-byte determinism, ≥95% field coverage, and **0% illegal-action submission**.

### The freeciv-llm contract we align to

- **State** (HTTP `:8002`): `GET /api/game/{id}/state?player_id=N&format=llm_optimized` →
  `{turn, phase, strategic{…}, tactical{…}, economic{…}, players, units, cities, techs, …}` where
  `players/units/cities` are **dicts keyed by string id** (`freeciv-proxy/state_extractor.py`).
- **Legal actions** (HTTP `:8002`): `GET /api/game/{id}/legal_actions?player_id=N`.
- **Acting** (WS `:8003`): `llm_connect` → `state_query` → `action_submit {action_type, …}`;
  canonical legality in `freeciv-proxy/action_validator.py`, ids in `action_constants.py`.

## 2. Before → after

| | Before | After |
|---|---|---|
| FreeCiv state → symbols | none (raw text only) | `llm_optimized` state → **deterministic PLN atoms** (`(Inheritance …)` / `(Evaluation …)` with `(stv f c)`) |
| Action legality | none (model output submitted as-is) | pre-submission `validate_action` mirroring `action_validator.py`; **illegal moves refused before `action_submit`** |
| Determinism | n/a | identical state → **byte-identical** facts/atoms/`state_hash` (order-independent) |
| Agent tools | memory/file/search/send/metta | **+ `freeciv-observe`** (state → PLN premises), **+ `freeciv-action`** (validate → submit) |
| `game_state` producer | reserved only (deferred by #5) | first producer; atoms shaped to be `remember_claim`-compatible |

## 3. Files changed

| File | Change |
|---|---|
| `benchmarks/freeciv/{__init__,schemas,adapter,atoms,actions,client}.py` *(new)* | Deterministic adapter core (benchmark-local, stdlib-only): state contract, `normalize_state`/`facts_from_state`/`state_hash`/`coverage`, PLN `atoms_from_facts`/`sentences_from_facts`, `validate_action` (+ `ValidationResult`), and a thin HTTP/WS `FreecivClient`. |
| `benchmarks/freeciv_{fixtures,benchmark}.py` + `freeciv_results.{md,json}` *(new)* | 6-scenario fixture set + KPI A/B benchmark (baseline vs candidate) with `sys.exit(1)` gate; committed results. |
| `src/freeciv_tool.py` *(new)* | Live-tool shim: `observe()` (state → PLN sentences) / `act()` (validate → submit; illegal → structured deny, never submitted). Offline self-test (`freeciv_tool self-tests passed`). |
| `src/freeciv.metta` *(new)* | MeTTa handlers `(freeciv-observe)` / `(freeciv-action $json)` → `py-call freeciv_tool.*`. |
| `src/helper.py`, `src/action_protocol.py`, `src/skills.metta` | 3-place tool registration (`LLM_COMMANDS`, `ARG_SPEC` + `output_format_block` self-tests, `getSkills`). |
| `lib_omegaclaw.metta` | Register `./src/freeciv_tool.py` and `./src/freeciv`. |
| `Autotests/test_freeciv_adapter.py` *(new)* + `Autotests/run_mandatory` | Host-runnable tests (determinism, coverage, PLN shape, legal/illegal matrix, tool wiring); wired into the mandatory suite. |

## 4. KPI results (`benchmarks/freeciv_results.md`)

6 `llm_optimized` states (schema-grounded, verified against `state_extractor.py`) covering food
shortage, undefended city, settler near threat, tech choice, unit movement, worker improvement:

| Metric | baseline | candidate |
|---|---|---|
| States converted to atoms | 0/6 | **6/6** |
| Mean field coverage | 0.00 | **1.00** |
| **Invalid-action submission rate** | **1.00** | **0.00** |
| Legal-action acceptance | n/a | **1.00** |
| Deterministic facts/atoms (2 runs identical) | n/a | **True** |

The candidate converts every state into deterministic PLN atoms and rejects **100%** of the
illegal candidate actions before `action_submit` while accepting all legal ones; the baseline
(raw text, no gate) would submit every illegal action. The benchmark asserts determinism, exits
non-zero on any regression, and the JSON output is byte-identical across runs.

## 5. End-to-end validation

**Host (done, no Docker):**
- `python3 benchmarks/freeciv_benchmark.py` → `KPI GATE: PASSED`; run twice → identical `freeciv_results.json`.
- `python3 Autotests/test_freeciv_adapter.py` → 12/12; under pytest the mandatory pure suite is
  **111 passed, 6 skipped** (the 6 skips are chroma-backed memory tests on a host without chromadb).
- src Phase-1 self-tests pass: `freeciv_tool`, `action_protocol`, `helper`, `tool_policy`,
  `provider_config`, `memory_schema`.

**Live E2E (executed against a running `freeciv-llm` stack):**
- Built and brought up the full `taso-ventures/freeciv-llm` docker-compose stack locally
  (`fciv-net` + mariadb + redis + nginx + flyway + mediamtx). Fixed two environment issues to
  get it healthy: (1) the bind-mounted `logs/` dir was `root`-owned and blocked publite2 from
  spawning civservers (`chmod 777`); (2) the `:8003` gateway crashed on unconditional streaming
  imports (`kubernetes`, `youtube_client`) even with `STREAMING_MODE=disabled` — installed
  `kubernetes` and stubbed `youtube_client` to bring the gateway up.
- Connected as an agent over the proxy `/llmsocket` and the `:8003` gateway (`llm_connect` →
  `auth_success`, `state_query`, `chat`/`action`), and **captured a real `llm_optimized` state**
  (saved at `benchmarks/freeciv/samples/real_state_turn0.json`). The adapter ran **deterministically**
  on the real state (`state_hash` stable across re-serialization) and `validate_action` correctly
  rejected an unknown-unit move (`E201`) against live data.
- **Schema-drift finding + fix (the payoff of the live run):** the real runtime format from
  `civcom.build_llm_optimized_state` differs from the documented `_format_llm_optimized_state`
  (`strategic.score` vs `victory_progress.current_score`; `economic.gold`/`research` vs
  `economic.resources.*`; `tactical.active_units`/`visible_threats`). The adapter was updated to
  handle **both** shapes; regression tests `test_real_runtime_shape_*` lock this in, and the
  captured sample is committed as the anchor.
- **Game-start solved → full E2E on a populated game.** The game stayed at `T000` because
  freeciv-llm civservers default to `minplayers=2` (`Not enough human players … game will not
  start`), so one agent + aifill never leaves pregame. Fix: while in pregame, send
  `/set minplayers 1` then `/start` (server commands via the `chat` message type). With that, the
  game reaches **turn 1 with the player's 7 starting units** (`startunits "cccwwwx"`). The adapter
  then produced **18 deterministic PLN atoms** from the real state (per-unit `Type`/`At`,
  `Gold`/`Science`/`Score`, researched tech), coverage **1.00**, hash stable; it **validated and
  submitted** a legal `unit_fortify` and **blocked an illegal unknown-unit move pre-submit**
  (`E201`). Reproduce with `python3 benchmarks/freeciv/live_play.py` (against the running stack).
  Captured states are committed at `benchmarks/freeciv/samples/real_state_turn{0,1}.json` and
  regression-tested. `websockets` is a live-only dep, lazily imported.
- **Still blocked (external):** live *LLM inference* — a funded provider key is required. ASICloud
  was out of quota (`insufficient_balance`); the repo now also supports **SNET**
  (`SNET_API_KEY`, `https://llm.c.singularitynet.io/v1`, OpenAI-compatible) as a provider. Once a
  funded key is set and the provider selected, the agent's decision layer drives the same
  host-proven `freeciv-observe`/`freeciv-action` tools.

## 6. What was deferred

- **Live win-rate / score KPIs** — need a full game + LLM; measured in the live E2E phase.
- **Real byte-captured fixtures** — the shipped fixtures are schema-grounded synthetic states; the
  adapter is field-tolerant, so real `llm_optimized` captures drop in without code changes.
- **Broader action coverage** — the validator covers move/build-city/fortify/sentry/skip/road/
  irrigation/mine/city-production/tech-research/end-turn; spy/diplomacy/trade actions are future work.

## 7. Reviewer guide — test & compare against the previous version

Prereqs: Python 3 (+ `pytest`, `pyyaml` for the Autotests). No Docker/chromadb/torch needed for §A–§D.

### A. Read the core diff (no build)
```bash
git checkout feat/freeciv-adapter
git diff main --stat
git diff main -- src/helper.py src/action_protocol.py lib_omegaclaw.metta
```

### B. Pure-Python checks (seconds, no Docker)
```bash
python3 benchmarks/freeciv_fixtures.py            # 6 fixtures, 13 legal + 12 illegal actions
python3 benchmarks/freeciv_benchmark.py           # KPI matrix; non-zero exit on regression
python3 src/freeciv_tool.py                        # freeciv_tool self-tests passed
python3 src/action_protocol.py                     # 3-place wiring guard (arg-spec + output_format)
python3 Autotests/test_freeciv_adapter.py          # 12/12 standalone
```

### C. Hand demo — before vs after (seconds)
```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "benchmarks")
from freeciv import adapter, atoms, actions
st = {"format":"llm_optimized","turn":42,"phase":"movement","player_perspective":1,
      "units":{"7":{"id":7,"type":"Settler","owner":1,"x":10,"y":5,"hp":10},
               "9":{"id":9,"type":"Warrior","owner":0,"x":11,"y":5,"hp":10}},
      "cities":{"1":{"id":1,"name":"Rome","owner":1,"x":3,"y":4,"production":"Warriors","food_surplus":-1}},
      "economic":{"resources":{"gold":50,"science":8}},"techs":{"player1":["Pottery"]}}
print("atoms:", *atoms.atoms_from_state(st)[0], sep="\n  ")
print("legal move  :", actions.validate_action({"type":"unit_move","unit_id":7,"dest_x":11,"dest_y":6}, st).is_valid)  # True
print("enemy move  :", actions.validate_action({"type":"unit_move","unit_id":9,"dest_x":11,"dest_y":6}, st).is_valid)  # False (E202)
PY
```

### D. In-container / live (Docker + ASICloud key)
```bash
git clone https://github.com/taso-ventures/freeciv-llm.git --depth=10 ~/Repos/freeciv-llm
( cd ~/Repos/freeciv-llm && cp .env.example .env && docker compose up -d )   # streaming disabled by default
curl "http://localhost:8002/api/game/<id>/state?player_id=1&format=llm_optimized"
# OmegaClaw: provider ASICloud, FREECIV_PROXY_URL/FREECIV_WS_URL set; drive freeciv-observe/action.
```

### E. Compare to `main`
```bash
git show main:benchmarks/freeciv_benchmark.py    # does not exist on main
git ls-tree main -- benchmarks/freeciv           # absent
git show main:src/freeciv_tool.py                # absent
```

## 8. Risk / rollback

- **Additive**: existing skills/benchmarks/tests are unchanged except the additive 3-place tool
  registration; no behavior change to `remember`/`query`/other tools.
- **Opt-out**: `OMEGACLAW_DISABLED_TOOLS=freeciv-observe,freeciv-action` disables the live tools;
  the deterministic adapter + benchmark stand alone if the WS integration is deferred.
- **License**: `freeciv-llm` (AGPL-3.0) is an external sibling dependency, never vendored.
- **KeyError trap**: adding tools to `LLM_COMMANDS` requires matching `ARG_SPEC` entries (done) or
  `output_format_block` raises — covered by the action-protocol self-tests.
- **Deferred**: live win-rate KPIs and real captured fixtures (§6); host proves the deterministic core.
- Not pushed; open a PR when ready (convention: PR + follow-up `docs: reviewer report …` commit).
