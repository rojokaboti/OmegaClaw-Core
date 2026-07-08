"""Unit tests for the multi-agent delegation primitive (Issue #18).

Pure-Python; imports src/delegation.py directly. Uses short sleeps to demonstrate concurrency
deterministically. Runs under pytest and standalone.
"""
import os
import sys
import tempfile
import threading
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_SRC, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import delegation as dg  # noqa: E402


def _sess_db():
    os.environ["OMEGACLAW_SESSION_DB"] = os.path.join(tempfile.mkdtemp(), "s.db")


def _clean(batch=None):
    if batch:
        dg.cleanup(batch)
    os.environ.pop("OMEGACLAW_SESSION_DB", None)


def _sleeper(ctx):
    time.sleep(0.12)
    ctx.write_artifact("out.txt", "done " + ctx.task_id)
    return "processed " + ctx.task_id


def test_parallel_faster_than_serial_with_artifacts_and_sessions():
    _sess_db()
    tasks = [{"id": "t%d" % i, "run": _sleeper} for i in range(6)]
    batch = dg.delegate(tasks, parent_id="p", concurrency=6, timeout=5)
    try:
        assert batch["counts"].get("ok") == 6
        assert batch["wall_clock"] < 0.5              # ~0.12s parallel vs ~0.72s serial
        for r in batch["results"]:
            assert r["session_id"].startswith("deleg-p-")   # isolated session per subagent
            assert r["artifacts"] and os.path.isfile(r["artifacts"][0])  # artifact path returned
            assert os.path.basename(os.path.dirname(r["artifacts"][0])) == r["id"]  # own workdir
    finally:
        _clean(batch)


def test_concurrency_limit_serializes():
    _sess_db()
    tasks = [{"id": "t%d" % i, "run": _sleeper} for i in range(4)]
    batch = dg.delegate(tasks, parent_id="p", concurrency=1, timeout=10)
    try:
        # concurrency=1 => roughly serial (>= 4*0.12s), proving the limit is honored
        assert batch["wall_clock"] >= 0.4
        assert batch["counts"].get("ok") == 4
    finally:
        _clean(batch)


def test_artifact_containment_blocks_escape():
    _sess_db()

    def _escaper(ctx):
        try:
            ctx.write_artifact("../escape.txt", "leak")
            return "ESCAPED"
        except dg.DelegationError:
            return "blocked"

    def _abs_escaper(ctx):
        try:
            ctx.write_artifact("/tmp/omegaclaw-escape.txt", "leak")
            return "ESCAPED"
        except dg.DelegationError:
            return "blocked"

    batch = dg.delegate([{"id": "e1", "run": _escaper}, {"id": "e2", "run": _abs_escaper}],
                        parent_id="p", timeout=5)
    try:
        summaries = {r["id"]: r["summary"] for r in batch["results"]}
        assert summaries == {"e1": "blocked", "e2": "blocked"}
        assert not os.path.exists(os.path.join(batch["deleg_root"], "escape.txt"))
    finally:
        _clean(batch)


def test_timeout_marks_worker_and_batch_returns():
    _sess_db()

    def _slow(ctx):
        time.sleep(2.0)
        return "late"

    batch = dg.delegate([{"id": "s1", "run": _slow, "timeout": 0.2}], parent_id="p")
    try:
        assert batch["results"][0]["status"] == "timeout"
    finally:
        _clean(batch)


def test_cancellation_skips_work():
    _sess_db()
    ev = threading.Event()
    ev.set()
    batch = dg.delegate([{"id": "c%d" % i, "run": _sleeper} for i in range(4)],
                        parent_id="p", cancel_event=ev, timeout=5)
    try:
        assert all(r["status"] == "cancelled" for r in batch["results"])
    finally:
        _clean(batch)


def test_worker_error_is_isolated():
    _sess_db()

    def _boom(ctx):
        raise RuntimeError("worker exploded")

    batch = dg.delegate([{"id": "ok", "run": _sleeper}, {"id": "bad", "run": _boom}],
                        parent_id="p", timeout=5)
    try:
        by_id = {r["id"]: r for r in batch["results"]}
        assert by_id["ok"]["status"] == "ok"          # sibling unaffected
        assert by_id["bad"]["status"] == "error" and "exploded" in by_id["bad"]["error"]
    finally:
        _clean(batch)


def test_unsafe_task_id_rejected_before_running():
    """Regression (PR #41 review): an unsafe task id (../, separators, absolute) must be rejected
    up front — nothing runs, no workspace escapes the delegation root."""
    _sess_db()
    try:
        for bad in ("../escape", "a/b", "/abs", ".."):
            batch = dg.delegate([{"id": bad, "run": _sleeper}], parent_id="p", timeout=5)
            assert batch["ok"] is False and batch["invalid"], (bad, batch)
            assert "deleg_root" not in batch                 # nothing was created/run
    finally:
        _clean()


def test_duplicate_task_ids_rejected():
    """Regression (PR #41 review): duplicate ids would collide workspace/session/results — reject
    the whole batch before running anything."""
    _sess_db()
    try:
        batch = dg.delegate([{"id": "same", "run": _sleeper}, {"id": "same", "run": _sleeper}],
                            parent_id="p", timeout=5)
        assert batch["ok"] is False
        assert any(x["reason"] == "duplicate task id" for x in batch["invalid"])
    finally:
        _clean()


def test_timeout_refuses_post_timeout_writes_and_returns_promptly():
    """Regression (PR #41 review): a timed-out worker cannot create artifacts after the timeout,
    and delegate returns promptly (does not block on the runaway)."""
    _sess_db()

    def _late(ctx):
        time.sleep(0.6)
        ctx.write_artifact("late.txt", "late")   # must be REFUSED (cancelled after timeout)
        return "late"

    t0 = time.time()
    batch = dg.delegate([{"id": "slow", "run": _late, "timeout": 0.1}], parent_id="p")
    elapsed = time.time() - t0
    try:
        assert batch["results"][0]["status"] == "timeout"
        assert elapsed < 0.4, elapsed                        # prompt return, not ~0.6s
        late = os.path.join(batch["results"][0]["workdir"], "late.txt")
        time.sleep(0.7)                                      # let the runaway finish its sleep
        assert not os.path.isfile(late), "timed-out worker wrote an artifact after timeout"
    finally:
        _clean(batch)


def test_no_nested_delegation_by_default():
    _sess_db()

    def _nester(ctx):
        try:
            dg.delegate([{"id": "inner", "run": lambda c: "x"}], parent_id="inner")
            return "NESTED"
        except dg.DelegationError:
            return "refused"

    batch = dg.delegate([{"id": "d1", "run": _nester}], parent_id="p", timeout=5)
    try:
        assert batch["results"][0]["summary"] == "refused"
    finally:
        _clean(batch)


def test_subagent_recorded_in_session_store():
    _sess_db()
    batch = dg.delegate([{"id": "t0", "run": _sleeper}], parent_id="rec", timeout=5)
    try:
        import session_store as ss
        shown = ss.show("deleg-rec-t0")
        assert shown["ok"] and shown["session"]["status"] in ("ok", "done")
    finally:
        _clean(batch)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok:", fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL:", fn.__name__, e)
    if failed:
        sys.exit(1)
    print(f"\nAll {len(fns)} delegation tests passed")
