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

## 6. Risk / rollback

- **Low risk, additive.** Upstream's 16 commits are two isolated features; our 94 commits are
  untouched by the merge. Only hand-edited files: `src/channels.metta`, `src/channel_registry.py`,
  and its test — all behavior-preserving for the existing channels.
- **Rollback:** the sync lives on its own branch; if anything regresses, delete the branch —
  `main` is unaffected until the PR merges.
