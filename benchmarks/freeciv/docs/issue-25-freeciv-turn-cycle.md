# Change Report — Issue #25: FreeCiv game does not advance past turn 1

> **Relocated (consolidation):** all FreeCiv code + docs now live under `benchmarks/freeciv/`.
> Paths in this historical report reflect their **original** locations at the time of the change
> (e.g. `benchmarks/freeciv_turn_cycle_benchmark.py` is now `benchmarks/freeciv/turn_cycle_benchmark.py`).

**Branch:** `feat/freeciv-turn-cycle` (off `main`, which has #1–#10 merged)
**Issue:** #25 — "FreeCiv benchmark: game does not advance past turn 1 (turn-cycle / end_turn handshake)"

---

## 1. Why this change exists

The multi-turn-advancement follow-up flagged by Issue #6: driving a `taso-ventures/freeciv-llm`
game through the adapter never ticked past **turn 1** — an agent re-decided over the same turn-1
state forever. The Issue #6 pipeline (state→PLN atoms, pre-submit validation) was unaffected; the
blocker was purely the turn-cycle **`end_turn` handshake**.

## 2. Root cause (client-side; confirmed live)

The turn-advancing packet — `PACKET_PLAYER_PHASE_DONE` (pid 52) — was never produced because the
OmegaClaw client sent `end_turn` in a shape the proxy **rejects at its message-validation gate**:

- The client sent `{"type":"action","action_type":"end_turn"}` (`llm_play.py:151`), with
  `action_type` at the **top level**.
- The proxy's `message_validator` schema for an `action` message requires a top-level **`action`
  dict** (`freeciv-proxy/message_validator.py:133`, `required_fields:[type, action]`). The client's
  message has no `action` key, so it is rejected with **`E220: Missing required field: action`** —
  before `_handle_action` ever runs. No packet, turn stuck on 1.

**Two corrections to the issue's own hypotheses** (verified in the proxy source + live):
- There is **no** `player_phase_done`/`turn_done`/`phase_done` top-level message type; pid 52 is
  produced only via a valid `action` message. And the `:8003` gateway converges on the same path.
  So "a different message type is required" is refuted.
- The static read of `_handle_action` (which extracts from `msg['data']` **or** `msg['action']`)
  suggested a `data`-nested envelope would work. The **live run proved it does not** — the earlier
  `message_validator` gate requires the `action` key specifically, so `data`-nesting also fails
  E220. The live stack was essential to land on the correct shape.

Live evidence (`freeciv-proxy` debug log), before → after:
```
# before (top-level action_type, and the data-nested attempt): both rejected
VALIDATION_ERROR ... error_code=E220 | message=Missing required field: action | input={"type":"action","action_type":"end_turn"}
VALIDATION_ERROR ... error_code=E220 | message=Missing required field: action | input={"type":"action","data":{"action_type":"end_turn"}}
# after (nested under `action`): accepted, turn advances
Action accepted: agent=omega, turn=1, action_type=end_turn
🔄 PACKET_BEGIN_TURN received: turn 2
Action accepted: agent=omega, turn=2, action_type=end_turn
🔄 PACKET_BEGIN_TURN received: turn 3   (… → turn 4, 5)
```
There was **no residual server-side gate** (no stale-turn / timeout / aifill issue): once the
envelope is correct, the turn advances immediately. So the fix is **100% client-side** — no
`freeciv-llm` change was needed.

Secondary client defects also fixed: the loop had **no turn-increment detection** (fixed
`range(CYCLES)` + blind `sleep(3)`, `turn` only printed); `live_play.py` never ended a turn; and
`client.submit_action` sent `type:"action_submit"`, which isn't a dispatched message type
(silently dropped) — so `freeciv_tool.act()`'s submit path was dead too.

## 3. What changed (before → after)

| | Before | After |
|---|---|---|
| end_turn wire shape | `{"type":"action","action_type":"end_turn"}` → E220, dropped | `{"type":"action","action":{"action_type":"end_turn"}}` → pid 52 |
| Turn detection | none (blind `sleep`, fixed cycle count) | `await_turn_advance` polls until `turn` strictly increases |
| Loop driver | N cycles (re-decides over a held turn) | N **observed** turn advances |
| `live_play.py` | one-shot fortify, no end-turn | drives ≥3 real turns (no LLM key needed) |
| `client.submit_action` | `type:"action_submit"` (undispatched, dropped) | `type:"action"` via the shared envelope |
| Envelope source | 3 divergent dialects across files | one helper (`client.action_message`) |

## 4. Files changed

| File | Change |
|---|---|
| `benchmarks/freeciv/client.py` | New `action_message` / `end_turn_message` — the single, live-verified envelope (`{"type":"action","action":{"action_type":..,<fields>}}`, `unit_id`→`actor_id`). `submit_action` now sends a dispatched `action` message via it (was the dropped `action_submit`). |
| `benchmarks/freeciv/turncycle.py` *(new)* | Shared async helpers: `recv_until`, `get_state`, `turn_of`, `send_end_turn`, and **`await_turn_advance`** (poll until the turn strictly increases). Dedups what `llm_play`/`live_play` each had. |
| `benchmarks/freeciv/llm_play.py` | `_to_packet`/end_turn use `client.action_message`; loop driven by observed turn advances via `await_turn_advance`; delegates state/wait to `turncycle`; `FREECIV_TURNS`. |
| `benchmarks/freeciv/live_play.py` | Now the deterministic **acceptance runner**: drives ≥`FREECIV_TURNS` real turns (fortify + end_turn + `await_turn_advance`), asserts monotonic advancement. No LLM/provider key. |
| `Autotests/test_freeciv_turn_cycle.py` *(new)* + `Autotests/run_mandatory` | 7 host tests over a `MockProxyWS` that models both proxy gates (E220 action-required + normalize→advance): correct envelope drives ≥3 monotonic turns; old top-level **and** `data`-nested shapes are E220-rejected and stay on turn 1 (regression guard). |
| `benchmarks/freeciv_turn_cycle_{fixtures,benchmark}.py` + `_results.{md,json}` *(new)* | KPI A/B (baseline shape 0 turns vs candidate 5/5) with a `sys.exit(1)` gate; committed results. |

No `freeciv-llm` (AGPL) code was changed or vendored.

## 5. KPI results (`benchmarks/freeciv_turn_cycle_results.md`)

| Metric | baseline (old shape) | candidate (fix) |
|---|---|---|
| Turns advanced (of 5) | 0 | **5** |
| Reached turn (from 1) | 1 | **6** |
| Monotonically increasing | False | **True** |

## 6. End-to-end validation

**Host (deterministic, no Docker — the committed gate):**
- `python3 Autotests/test_freeciv_turn_cycle.py` → 7/7. Under pytest, freeciv + protocol + errors
  suites → **74 passed**. `python3 src/freeciv_tool.py` self-test OK. `freeciv` and
  `freeciv_turn_cycle` benchmark gates PASSED.

**Live (against the running `taso-ventures/freeciv-llm` stack):**
- Brought the stack up (`docker compose up -d fciv-net`; `logs/` already `777` from #6; connected
  directly to `:8002/llmsocket`, avoiding the `:8003` gateway's streaming imports).
- Reproduced the bug: old shape (and the `data`-nested attempt) → `E220`, turn stayed **1**.
- With the fix, `benchmarks/freeciv/live_play.py` (`FREECIV_GAME_ID=omega_e2e_v25`) reached turn 1
  with 7 units, produced **18 deterministic PLN atoms** (coverage 1.00, hash stable), blocked an
  illegal unknown-unit move pre-submit (`E201`), and drove **turns 1→2→3→4** (`turns advanced: 3
  monotonic=True`, `E2E OK`). Proxy log shows `Action accepted … end_turn` → `PACKET_BEGIN_TURN
  received: turn 2/3/4`.

## 7. Reviewer guide

### A. Read the core diff (no build)
```bash
git checkout feat/freeciv-turn-cycle
git diff main -- benchmarks/freeciv/client.py benchmarks/freeciv/turncycle.py benchmarks/freeciv/llm_play.py
```
Focus on `client.action_message` (the envelope) and `turncycle.await_turn_advance` (the detection).

### B. Pure-Python checks (seconds, no Docker)
```bash
python3 Autotests/test_freeciv_turn_cycle.py       # 7/7; old + data shapes stuck, correct shape advances
python3 benchmarks/freeciv_turn_cycle_benchmark.py # KPI GATE: PASSED (baseline 0, candidate 5/5)
( cd Autotests && python3 -m pytest -q test_freeciv_turn_cycle.py test_freeciv_adapter.py )
```

### C. Live (Docker) — the issue's acceptance criteria
```bash
cd ~/Repos/freeciv-llm && docker compose up -d fciv-net     # logs/ perms already 777 (#6)
pip install --user --break-system-packages websockets       # live-only dep
cd ~/Repos/OmegaClaw-Core
FREECIV_GAME_ID=omega_e2e_v25 FREECIV_TURNS=3 python3 benchmarks/freeciv/live_play.py   # prints turn 1->2->3->4, E2E OK
# proxy log evidence:
grep -E "Action accepted.*end_turn|PACKET_BEGIN_TURN received: turn [2-9]" ~/Repos/freeciv-llm/logs/llm-handler-debug.log | tail
```

### D. Compare to `main`
```bash
git show main:benchmarks/freeciv/turncycle.py    # does not exist on main
grep -n 'action_type.*end_turn' main:benchmarks/freeciv/llm_play.py 2>/dev/null || \
  git show main:benchmarks/freeciv/llm_play.py | grep -n 'end_turn'   # old top-level shape on main
```

## 8. Risk / rollback
- **Additive + benchmark-local**: only the freeciv benchmark runners/client + a new host test +
  a new benchmark. The deterministic adapter, atoms, and `validate_action` are untouched (still
  0% illegal). No `src/` behavior change beyond `freeciv_tool.submit_action` now reaching the
  proxy (previously a silent no-op).
- **No external/AGPL change**: the fix is entirely the client envelope + advance detection; the
  `freeciv-llm` proxy was diagnosed read-only and needs no patch (per the confirmed scope).
- **Single envelope source** (`client.action_message`) prevents the three-dialect drift that
  caused this.
- Not pushed; open a PR when ready (convention: PR + follow-up `docs: reviewer report …` commit).
