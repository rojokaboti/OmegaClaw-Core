# Change Report — Upstream sync (2026-07): Renovate bot + WebSocket chat channel

**Branch:** `chore/sync-upstream-2026-07` (off `main`)
**Type:** fork ↔ upstream sync (second one; first was PR #27, 2026-07-02)
**Upstream:** `asi-alliance/OmegaClaw-Core` → merged into our `main`

---

## 1. Why this change exists

Our fork (`rojokaboti/OmegaClaw-Core`) had drifted from upstream after landing the entire
OpenClaw/Hermes-parity batch (#11–#19 + #16/#17/#18). At sync time our `main` was **94 commits
ahead / 16 behind** `upstream/main` (merge-base `9106cee`). The goal: bring upstream's work in
**without disturbing our 94 commits of feature work** — so this is a **merge**, not a rebase.
Rebasing published history would rewrite all 94 commits; a single merge commit brings upstream
in with one deliberate conflict resolution and leaves our history intact (same approach as PR #27).

## 2. What upstream brought (the 16 commits) — two features

| Feature | Files |
|---|---|
| **Renovate bot** (dependency-update automation) | `.github/renovate-config.js`, `.github/workflows/renovate.yml` (new) |
| **WebSocket chat channel** | `channels/wschat.py` (new), `src/channels.metta`, `scripts/omegaclaw`, `lib_omegaclaw.metta`, `requirements.txt` (`websockets==16.0`), docs |

A read-only `git merge-tree` predicted **exactly one** conflict: `src/channels.metta`. Everything
else (`README.md`, `lib_omegaclaw.metta`, `scripts/omegaclaw`, `requirements.txt`, `wschat.py`,
docs, Renovate configs) auto-merged cleanly.

## 3. The one conflict and how it was resolved

Upstream added the `websocket` channel by extending the **old hardcoded nested-`if` dispatch** in
`src/channels.metta` (start/receive/send). But our **Issue #9** (`src/channel_registry.py`, PR #30)
had already **deleted that dispatch** and replaced it with thin `py-call`s into a Python registry —
so the two sides edited code that no longer coexists.

**Resolution:** keep our registry facade and **port the new channel into the registry** — exactly
the extension point Issue #9 was built for ("adding a channel is registering one object instead of
editing three branches"). We did **not** reinstate upstream's if/else.

| File | Change |
|---|---|
| `src/channels.metta` | Kept the registry facade; added `WS_URL`/`WS_TOKEN` runtime-config stubs + `configure` lines, and threaded them as two trailing args to `channel_registry.start_channel`. `receive`/`send` unchanged. |
| `src/channel_registry.py` | New `_websocket_start(cfg)` builder + `CHANNELS["websocket"]` entry (`_lazy("wschat", …)`); `start_channel(...)` gained trailing `ws_url`/`ws_token` params feeding `cfg`; self-test asserts `websocket` is registered. |
| `Autotests/test_channel_registry.py` | Added `"websocket"` to the registered-channels assertion. |

**Lazy import preserved:** importing `channel_registry` still pulls in **no** `websockets`
dependency (the `wschat` module loads only when the websocket channel is actually selected), so
host self-tests/unit tests stay dependency-free. `websockets==16.0` (auto-merged into
`requirements.txt`) covers the container runtime, which loads `wschat.py` via `lib_omegaclaw.metta`.

## 4. End-to-end validation (host, no Docker)

- `python3 src/channel_registry.py` → self-tests pass (incl. `websocket` registered).
- `python3 Autotests/test_channel_registry.py` → 6/6.
- `( cd Autotests && python3 -m pytest -q @run_mandatory )` → full host sweep, no regressions.
- `git log --oneline upstream/main..HEAD` after merge → **0** (fully caught up).
- No conflict markers remain (`git diff --check` clean).

Docker-gated (documented, not run here): build the image, start with `-t websocket` +
`WS_URL`/`WS_TOKEN`, confirm the wschat channel connects and round-trips a message.

## 5. Reviewer guide

```bash
git checkout chore/sync-upstream-2026-07
git log --oneline upstream/main..HEAD        # expect 0 — caught up with upstream
python3 src/channel_registry.py              # websocket registered
python3 Autotests/test_channel_registry.py   # 6/6
git show --stat HEAD                          # the merge commit; only channels.metta was hand-resolved
```

## 5b. Post-review fixes (PR #43 review — 4 blockers + doc nits)

The reviewer found four blockers (all reproduced before fixing) plus two doc nits. Three of the
four are in upstream's newly-merged code; fixes were kept **minimal / low-divergence** to avoid
future merge friction on the next sync.

1. **WebSocket outbox dropped unsent messages after a mid-flush failure** (`channels/wschat.py`
   `_drain_outbox`). It cleared the whole outbox, then on a send failure requeued **only** the
   failed payload — every later unsent payload was lost. Reproduced: queue `[1,2,3]`, fail on `2`
   → `3` vanished. **Fix:** requeue the failed payload **and all remaining unsent ones**, in
   order. Regression: `Autotests/test_wschat.py`.
2. **WebSocket permitted an unauthenticated control path** (inbound frames drive the agent, but
   the adapter has no `auth <secret>` ownership gate like IRC/Slack/etc.). **Fix (chosen approach:
   registry-level, fail-closed, low upstream divergence):** the Issue #9 channel registry now
   **requires both a non-empty `WS_URL` and `WS_TOKEN`** for `commchannel=websocket`; without them
   `_websocket_start` **declines** and no connection/import happens.
3. **A declined/disabled channel was reported as started.** `start_channel` returned
   `CHANNEL-STARTED` unconditionally, so a websocket with missing config looked healthy. **Fix:**
   a channel start builder may return `False` to decline; `start_channel` then reports
   `CHANNEL-DISABLED:<name>` (only an explicit `False` — channels that return `None`, e.g. mock,
   still count as started). Covers the missing-`WS_URL` case truthfully.
4. **Renovate workflow/config was hardcoded to `asi-alliance/OmegaClaw-Core`** and needs upstream
   secrets — in this fork it would fail every week and could try to automate against upstream.
   **Fix (fork-safe):** guard the job with `if: github.repository == 'asi-alliance/OmegaClaw-Core'`
   so it is a no-op in the fork (files kept for parity; guard documented inline).

Doc nits: `docs/reference-channels.md` still described the **old nested-`if` MeTTa dispatch** that
Issue #9 removed → rewritten to describe the registry. `WS_TOKEN` docs (reference-channels /
reference-configuration / README) and the interactive `scripts/omegaclaw` setup flipped from
"optional" to **required (fail-closed)** to match the new registry behavior.

Regression/verification for the fix round: `channel_registry` self-test + `test_channel_registry.py`
(8 tests, incl. fail-closed websocket), new `test_wschat.py` (2 tests), embedded-Python compile of
`scripts/omegaclaw`, and the full `@run_mandatory` sweep (unchanged host baseline).

## 6. Risk / rollback

- **Low risk, additive.** Upstream's 16 commits are two isolated features; our 94 commits are
  untouched by the merge. Only hand-edited files: `src/channels.metta`, `src/channel_registry.py`,
  and its test — all behavior-preserving for the existing channels.
- **Rollback:** the sync lives on its own branch; if anything regresses, delete the branch —
  `main` is unaffected until the PR merges.
