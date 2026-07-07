# Change Report — Issue #12: Skill install / update lifecycle with lock metadata

**Branch:** `feat/skill-install` (off `main`, which has #1–#13 merged)
**Issue:** #12 — "Add ClawHub, Git, and local skill install/update with lock metadata"
**Depends on:** #11 (loader, merged) + #13 (eligibility, merged). Third in the #11–#19 fan-out.

---

## 1. Why this change exists

The loader (#11) + eligibility (#13) still leave users hand-copying skills and tracking
versions. OpenClaw installs skills from ClawHub / Git / local dirs and records origin + lock
metadata. This adds that lifecycle so OmegaClaw can safely **acquire** the external skill
ecosystem, not just read it: install from a local path, a Git repo (pinned ref), or a
ClawHub-compatible HTTP registry, with idempotent updates, pinning, verification, and rollback.

### Accuracy vetting (issue vs. reality)
- Accurate and correctly sequenced (loader + eligibility exist; no install flow did).
- **"ClawHub" is not a real endpoint** and CI has no guaranteed network, so faithful *and*
  deterministic testing means: Git installs tested against a **temp local git repo** (real
  `git`, no network) and ClawHub against a **localhost `http.server` fixture** (real HTTP, no
  external network). The registry base is env-configurable (`OMEGACLAW_CLAWHUB_URL`).
- CLI home already existed: `scripts/omegaclaw-skills` (from #13) — extended with subcommands
  rather than a new `src/skills_cli.py`.
- **Trust** is recorded as `unverified` (the full trust boundary — sandboxing/approvals/static
  scanning — is Issue #19; this lays the lock/origin groundwork it will build on).

## 2. Before → after

| | Before | After |
|---|---|---|
| Acquire a skill | manual copy | `omegaclaw-skills install local:/… \| git:owner/repo@ref \| clawhub:slug` |
| Provenance | none | per-skill `.omegaclaw-origin.json` + workspace lockfile |
| Update | manual | `update <name>` / `update --all` (idempotent; reinstall from recorded source) |
| Reinstall | duplicate/overwrite risk | idempotent, in-place, 0 duplicate dirs |
| Integrity | none | `verify` re-hashes vs lock → ok / tampered / missing |
| Freeze a skill | none | `pin` (skipped by `update --all`; overridable by explicit named update) |
| Bad source | could corrupt root | staged + validated first → rollback, root untouched |

## 3. Files changed

| File | Change |
|---|---|
| `src/skill_install.py` *(new, stdlib-only, self-testing)* | Source specs (`parse_source`: local/git/clawhub incl. `owner/repo@ref` shorthand); fetch adapters (local copy, `git clone`+checkout+strip `.git`, ClawHub metadata→archive via `urllib`+`tarfile` with a path-traversal-safe extract); `fetch→validate(skill_loader)→commit→lock` with rollback; `install/update/remove/list/verify/pin/unpin`; content-hash (`_hash_dir`, excludes origin file) + workspace lockfile + per-skill origin. Idempotent; pinned protected from `update --all`. |
| `scripts/omegaclaw-skills` | New subcommands `install` / `update` / `list` / `remove` / `verify` / `pin` (`--unpin`), each with `--json`, alongside the existing `doctor`. |
| `benchmarks/skill_install_{fixtures,benchmark}.py` + `_results.{md,json}` *(new)* | 10 local + 10 git sources (git repos created locally, no network); KPI gate over install success, lock coverage, duplicate dirs, pin protection, verify, and rollback. |
| `Autotests/test_skill_install.py` *(new)* + `run_mandatory` | 8 host tests: local idempotency + lock, rollback, verify-tamper, pin protection + named-update override, remove, **git from a temp local repo**, **ClawHub from a localhost HTTP fixture**, source parsing. |
| `.github/workflows/common.yml` | Phase-1 runs `python ../src/skill_install.py`. |
| `README.md` | Documents the install lifecycle + CLI + lock/origin + trust caveat. |

## 4. KPI results (`benchmarks/skill_install_results.md`)

20 sources (10 local + 10 git) through the real `src/skill_install.py`.

| Metric | baseline | candidate |
|---|---|---|
| Install success rate (target ≥ 0.95) | 0.0 | **1.00** |
| Lock-metadata coverage (target 1.00) | 0.0 | **1.00** |
| Duplicate dirs after reinstall (target 0) | 0 | **0** |
| Pinned skipped by `update --all` | False | **True** |
| Pinned bytes unchanged after `update --all` | False | **True** |
| verify: all installed skills OK | False | **True** |
| Rollback leaves root unchanged on bad source | False | **True** |

100% install success, 100% lock coverage, 0 duplicate directories, pinned skills protected and
byte-identical after `update --all`, and clean rollback on an invalid source — none of which
the baseline (no lifecycle) can do. `sys.exit(1)` on regression. Satisfies the issue's KPI gate.

## 5. End-to-end validation

- `python3 src/skill_install.py` → self-tests pass (local install, idempotency, verify/tamper,
  pin protection, rollback, remove, source parsing).
- `python3 Autotests/test_skill_install.py` → 8/8 (incl. real git + localhost ClawHub HTTP).
- `python3 benchmarks/skill_install_benchmark.py` → `KPI GATE: PASSED`; #11 and #13 gates still
  pass.
- Full CLI lifecycle exercised by hand: `install local:… → list → pin → update --all
  (skipped) → verify (ok) → remove`.

### Post-review fix (PR #36 review) — two filesystem-safety blockers
1. **Path traversal in `remove()`** — it joined an untrusted `name` into a path and
   `rmtree`d it (the `or os.path.isdir(dest)` clause even deleted untracked dirs), so
   `remove("../outside-victim")` deleted outside the root. **Fix:** a shared
   `skill_loader.is_safe_skill_name` (rejects empty / `..` / separators / absolute / NUL) +
   `skill_install._safe_dest` (realpath + `commonpath` containment), applied to
   `remove`/`_set_pin`/`verify`/`install` before any write or delete.
2. **Symlinked bundle payloads dereferenced into the root** — local `copytree` and the commit
   copy followed symlinks, so a payload symlinked to an outside file was copied in
   (exfiltration). **Fix:** fetch/commit now `copytree(..., symlinks=True)` (never
   dereference), and a bundle containing **any** symlink is rejected fail-closed
   (`rejected_symlink`, not committed). ClawHub archives were already covered by the
   traversal-safe `tarfile` `data` filter.
Regression tests added:
`test_remove_rejects_path_traversal_and_leaves_outside_dirs_untouched`,
`test_symlinked_bundle_is_rejected_not_dereferenced`, `test_is_safe_skill_name`. Install suite
now 11 tests; KPI gate + loader/policy gates still pass.

### Post-review fix round 2 (PR #36 re-review) — two lifecycle/CLI reporting blockers
1. **Rejected installs/updates reported success.** A symlink-rejected bundle still returned
   `ok: True` (CLI exit 0), and `update()` mapped the rejected reinstall to `"updated"`.
   **Fix:** `install()` returns `ok: False` + an `error` summary when any bundle is rejected
   (`rejected_*`), so the CLI exits non-zero; `update()` preserves the inner per-skill status
   (e.g. `rejected_symlink`) instead of flattening to `"updated"`, and its `ok` is true only
   when every skill is `updated`/`skipped_pinned`.
2. **Top-level `--config` silently ignored.** `--config` defined on both the top parser and
   each subparser meant `--config X install …` (the advertised form) was overwritten by the
   subparser default → a write/delete command could target the *default* skill root. **Fix:**
   subparser `--config` now uses `default=argparse.SUPPRESS` so it never clobbers the
   top-level value when absent; both `--config X install …` and `install --config X …` target
   the intended root.
Regression tests: `test_all_rejected_install_reports_failure`,
`test_update_preserves_rejected_status`, `test_top_level_config_targets_intended_root`
(drives the real CLI `main()` with `--config` before the subcommand). Install suite now 14
tests; 94-test host sweep green; all three KPI gates still pass.

## 6. Reviewer guide

```bash
git checkout feat/skill-install
python3 src/skill_install.py
python3 Autotests/test_skill_install.py            # git + clawhub-http covered
python3 benchmarks/skill_install_benchmark.py      # KPI GATE: PASSED

# Hand demo — local install + lock + verify + pin + rollback:
tmp=$(mktemp -d); mkdir -p "$tmp/src/demo"
printf -- '---\nname: demo\ndescription: d\nversion: 1.0.0\n---\nhi\n' > "$tmp/src/demo/SKILL.md"
printf -- 'version: 1\nroots: ["%s/installed"]\n' "$tmp" > "$tmp/skills.yaml"
python3 scripts/omegaclaw-skills install "local:$tmp/src" --config "$tmp/skills.yaml"
python3 scripts/omegaclaw-skills list   --config "$tmp/skills.yaml"
python3 scripts/omegaclaw-skills verify --config "$tmp/skills.yaml"
cat "$tmp/installed/.omegaclaw-skills.lock.json"
```

## 7. Risk / rollback
- **Additive, isolated to the install path.** No change to the loader/eligibility runtime; the
  default `skills/` root ships empty, so nothing is installed out-of-box and no lockfile is
  committed.
- **Never corrupts the active root:** all fetching happens in a temp staging dir; the root is
  mutated only after loader validation succeeds. A fetch/validation failure returns a structured
  error and leaves the root byte-identical.
- **Deterministic tests, no external network:** git via a temp local repo, ClawHub via a
  localhost HTTP fixture; archive extraction uses the stdlib path-traversal-safe filter.
- **Trust is `unverified`** — this issue provides provenance/lock; the trust boundary
  (sandboxing, approvals, static scanning of untrusted sources) is Issue #19, which builds on
  this lock/origin metadata.
- Follow-up branch off `main`; open a PR against `rojokaboti/OmegaClaw-Core`. Next: #15 plugins,
  #19 sandbox, then #14 workshop.
