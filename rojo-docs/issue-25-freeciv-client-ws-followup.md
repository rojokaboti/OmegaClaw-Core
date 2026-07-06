# Change Report — Issue #25 follow-up: FreecivClient live WebSocket auth + endpoint fix

**Branch:** `fix/freeciv-client-ws-auth` (off `main`, which has #1–#10, #6, #25/PR #32 merged)
**Context:** post-merge NO-GO review of **PR #32** (Issue #25 turn-cycle fix) found the
foundational `freeciv-action` live tool path still could not authenticate against the real
`freeciv-llm` proxy. PR #32 itself is correct and stays merged; this is the follow-up fix.

---

## 1. Why this change exists

PR #32 correctly fixed the **action envelope** (`{"type":"action","action":{"action_type":…}}`)
and turn-advance detection. But the agent's live submission path —
`freeciv_tool.act()` → `FreecivClient.submit_action()` — still failed **before** it could send
that action, for two independent reasons, both verified against upstream
`taso-ventures/freeciv-llm`:

1. **`llm_connect` handshake was invalid.** It nested the credential as
   `{"type":"llm_connect","agent_id":…,"data":{"api_token":…,"port":…}}`. The proxy's
   `MessageValidator` schema (`freeciv-proxy/message_validator.py:75`) requires
   `["type","agent_id","api_token"]` at the **top level**. Reproduced against the real
   validator: `E220 Missing required field: api_token`. So the socket handshake was rejected
   and no action was ever submitted — `freeciv-action` validated an action locally, then died
   on submit.
2. **Wrong WebSocket endpoint.** `from_env()` defaulted to `ws://host.docker.internal:8003` and
   `_ws_roundtrip()` appended `/ws/agent/{agent}?game_id=…`. That is the **separate llm-gateway**
   FastAPI route (`llm-gateway/websocket_handlers.py:1077`), not the **proxy**
   `/llmsocket/<port>` handler (`freeciv-proxy/freeciv-proxy.py:319`) that the Issue #25-validated
   runners (`live_play.py` / `llm_play.py`, `ws://localhost:8002/llmsocket/8002`) and the
   `action` envelope target. The client was sending proxy-shaped messages to a gateway route.

Net: the direct runner path was fine, but the reusable agent tool path was unreliable for real
live use. This matters because PR #32's report claimed it fixed the previously-dead
`submit_action` path.

### Verification against upstream (reproduced, not assumed)
Ran each message through the real `freeciv-proxy/message_validator.py`:

| Message | Before | After |
|---|---|---|
| `llm_connect` (nested `data.api_token`) | **INVALID — E220 missing api_token** | — |
| `llm_connect` (top-level `api_token`, default token) | — | **VALID** |
| `action` (unit_move) | VALID | VALID |
| `action` (end_turn) | VALID | VALID |

## 2. Before → after

| | Before | After |
|---|---|---|
| `llm_connect` credential | nested under `data` → `E220` | **top-level** `api_token` (+ `agent_id`, `game_id`, `port`) |
| WS endpoint | gateway `…:8003/ws/agent/{a}?game_id=…` (suffixed) | proxy `…:8002/llmsocket/8002`, used **verbatim** |
| `FREECIV_WS_URL` semantics | base URL that gets a path appended | the **complete** endpoint (matches runners' `FREECIV_PROXY_WS`) |
| Handshake builder | inlined nested dict | `connect_message()` (single, testable, top-level) |
| `action_message` actor_id | dropped a passed-through `actor_id` | preserved when no `unit_id` given |
| Regression coverage | only `turncycle.send_end_turn()` after a pre-authed mock | full `submit_action()` wire sequence (handshake + URL + frames) |

## 3. Files changed

| File | Change |
|---|---|
| `benchmarks/freeciv/client.py` | `connect_message()` builds `llm_connect` with **top-level** `api_token` (+ optional `game_id`/`port`); `_ws_uri()` returns the endpoint **verbatim** (no `/ws/agent` suffix); `from_env()` defaults to the proxy `/llmsocket/8002` and honors `FREECIV_WS_URL` then `FREECIV_PROXY_WS`; `submit_action()` uses `connect_message()`; `action_message()` preserves a passed-through `actor_id`; updated module docstring (wire contract); added an offline `_selftest()` that drives `submit_action()` through a fake `websockets` module and asserts the frame sequence. |
| `Autotests/test_freeciv_client_ws.py` *(new)* | 6 host tests: top-level `api_token` (E220 regression guard, asserts `data` absent), action/end_turn schema, `actor_id` preservation, `/llmsocket` URL (never `/ws/agent`), `from_env` defaults, and the full `submit_action` wire sequence via an injected fake socket. |
| `Autotests/run_mandatory` | Adds `test_freeciv_client_ws.py`. |
| `.github/workflows/common.yml` | Phase-1 runs `python ../benchmarks/freeciv/client.py`. |
| `rojo-docs/issue-25-freeciv-turn-cycle.md` | Stale "Not pushed; open a PR when ready" → merged as PR #32 + points to this follow-up. |
| `rojo-docs/issue-6-freeciv-adapter.md` | Stale "Acting (WS `:8003`): … `action_submit`" → corrected to proxy `/llmsocket/<port>` + top-level `api_token` + `type:"action"`. |

## 4. KPI / correctness results

No new KPI number — this is a live-path correctness fix. Proof is the upstream-validator
reproduction (§1) plus the wire-sequence regression test. All prior gates unaffected:
`freeciv_benchmark.py` (#6, 0% illegal) and `freeciv_turn_cycle_benchmark.py` (#25) both still
`KPI GATE: PASSED`; benchmark artifacts unchanged.

## 5. End-to-end validation

**Host (pure-Python — no live server):**
- `python3 benchmarks/freeciv/client.py` → `freeciv client self-tests passed` (drives
  `submit_action()` through a fake socket; asserts top-level `api_token`, `/llmsocket` URL, and
  the exact `[llm_connect, action]` frame order).
- `python3 src/freeciv_tool.py` → self-test still passes (tool path intact).
- `Autotests/test_freeciv_client_ws.py` → 6/6; freeciv host suite
  (`test_freeciv_client_ws, test_freeciv_adapter, test_freeciv_turn_cycle`) → **31 passed**.
- Both freeciv KPI gates still pass.
- **Upstream contract probe:** `connect_message()` / `action_message()` / `end_turn_message()`
  all validate **VALID** against the real `freeciv-proxy/message_validator.py`.

**In-container / live (documented, gated):** with the `freeciv-llm` proxy running, `freeciv-action`
now completes the `llm_connect` handshake on `/llmsocket/8002` and submits the action, instead of
failing `E220` at connect. (Same live-gated posture as #6/#25; the host regression pins the wire
contract deterministically.)

## 6. Reviewer guide

```bash
git checkout fix/freeciv-client-ws-auth
git diff main -- benchmarks/freeciv/client.py            # nested->top-level api_token; /llmsocket URL
python3 benchmarks/freeciv/client.py                     # self-test: full submit_action wire sequence
python3 Autotests/test_freeciv_client_ws.py              # 6/6

# Reproduce the upstream-validator proof (needs the freeciv-llm clone):
python3 - <<'PY'
import sys, json
sys.path.insert(0,"benchmarks"); sys.path.insert(0,"/path/to/freeciv-llm/freeciv-proxy")
from freeciv.client import FreecivClient, action_message, end_turn_message
from message_validator import MessageValidator
v=MessageValidator(); c=FreecivClient("http://h:8002","ws://h:8002/llmsocket/8002","test-token-fc3d-001","g",1)
for label,m in [("connect",c.connect_message()),("action",action_message({"type":"unit_move","unit_id":7,"dest_x":5,"dest_y":5})),("end_turn",end_turn_message())]:
    try: v.validate_message(json.dumps(m)); print(label,"VALID")
    except Exception as e: print(label,"INVALID",e)
PY
```

## 7. Risk / rollback
- **Scope is the client wire contract only.** The deterministic adapter, `validate_action`
  (0%-illegal gate), atoms, and turn-cycle logic are untouched — all their tests/gates still pass.
- **Behavior for the direct runners is unchanged** (they already used `/llmsocket` + top-level
  `api_token`); this only brings `submit_action()` onto that same validated path.
- **`FREECIV_WS_URL` semantics changed** from "base to be suffixed" to "complete endpoint" — now
  matching the runners' `FREECIV_PROXY_WS`. A deployment that set `FREECIV_WS_URL` to a bare host
  (expecting the `/ws/agent` suffix, which never worked against the proxy anyway) must set the full
  `/llmsocket/<port>` URL; documented in the module docstring.
- WS import stays lazy → host import/tests need no `websockets`.
- Follow-up branch off `main`; open a PR against `rojokaboti/OmegaClaw-Core`.
