"""Multi-agent delegation with isolated subagent workspaces (Issue #18).

Complex tasks benefit from parallel independent investigations, review agents, and sandboxed
workers. OmegaClaw was a single loop; this adds an explicit, bounded, auditable **delegation
primitive**: run a batch of subtasks concurrently, each in its own **isolated workdir + session**,
with a **concurrency limit**, per-worker **timeout**, clean **cancellation**, and a structured
result schema. Subagents can only affect the parent through **declared artifacts** (containment
enforced), and **nested delegation is refused by default** (no runaway recursion).

A subtask is ``{"id": str, "run": callable(ctx) -> summary_str, "timeout": float?}``. The worker
receives a :class:`WorkerContext` giving it its private ``workdir``, a cancellation check, and
``write_artifact(relpath, content)`` — the ONLY sanctioned output channel, which rejects any path
escaping the workdir. Results carry each subagent's session id + artifact paths for audit.

Stdlib only (``concurrent.futures`` threads). Each subagent is recorded in the session store
(Issue #16) for later ``sessions show/search``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

try:
    import session_store
except ImportError:  # pragma: no cover
    try:
        from src import session_store
    except ImportError:
        session_store = None

_DEFAULT_CONCURRENCY = int(os.environ.get("OMEGACLAW_DELEGATION_CONCURRENCY", "4"))
_DEFAULT_TIMEOUT = float(os.environ.get("OMEGACLAW_DELEGATION_TIMEOUT", "30"))

# Guards against recursive runaway: set while inside delegate(); a worker that calls delegate()
# again is refused unless allow_nested=True.
_in_delegation = threading.local()


class DelegationError(Exception):
    pass


@dataclass
class WorkerContext:
    """Handed to each subagent: its private workspace + the sanctioned output channel."""
    task_id: str
    session_id: str
    workdir: str
    _cancel: threading.Event
    artifacts: List[str] = field(default_factory=list)

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def write_artifact(self, relpath: str, content) -> str:
        """Write a declared artifact INSIDE the workdir and register it. Rejects any path that
        escapes the workdir (``..``/absolute/symlink) — this is the only way a subagent affects
        the parent, so it must be contained."""
        if os.path.isabs(relpath):
            raise DelegationError("artifact path must be relative: {!r}".format(relpath))
        target = os.path.realpath(os.path.join(self.workdir, relpath))
        root = os.path.realpath(self.workdir)
        if not (target == root or target.startswith(root + os.sep)):
            raise DelegationError("artifact {!r} escapes the subagent workdir".format(relpath))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        mode = "wb" if isinstance(content, (bytes, bytearray)) else "w"
        with open(target, mode) as f:
            f.write(content)
        self.artifacts.append(target)
        return target


def _run_one(subtask: Dict[str, Any], parent_id: str, deleg_root: str,
             cancel: threading.Event, default_timeout: float) -> Dict[str, Any]:
    # NB: this runs in a worker thread, so it must NOT share a sqlite3 connection with the parent
    # (sqlite3 forbids cross-thread connection use). session_store calls open their own per-call
    # connection in THIS thread, which is thread-safe.
    tid = str(subtask.get("id"))
    sid = "deleg-{}-{}".format(parent_id, tid)
    workdir = os.path.join(deleg_root, tid)
    os.makedirs(workdir, exist_ok=True)
    ctx = WorkerContext(task_id=tid, session_id=sid, workdir=workdir, _cancel=cancel)
    started = time.time()
    if session_store is not None:
        try:
            session_store.begin_session(sid, channel="delegation", task=str(subtask.get("task", tid)))
        except Exception:  # noqa: BLE001
            pass

    if cancel.is_set():
        _finish(sid, "cancelled", ctx)
        return {"id": tid, "session_id": sid, "workdir": workdir, "status": "cancelled",
                "summary": "", "artifacts": [], "duration": 0.0}

    run = subtask.get("run")
    result = {"id": tid, "session_id": sid, "workdir": workdir, "artifacts": ctx.artifacts}
    try:
        if not callable(run):
            raise DelegationError("subtask {!r} has no callable 'run'".format(tid))
        # nested-delegation guard: mark this thread as inside a delegation for the worker's scope
        _in_delegation.active = True
        summary = run(ctx)
        result.update(status="cancelled" if cancel.is_set() else "ok",
                      summary=str(summary or ""), duration=round(time.time() - started, 4))
    except Exception as e:  # noqa: BLE001 - a worker failure is isolated, never crashes the batch
        result.update(status="error", summary="", error=str(e),
                      duration=round(time.time() - started, 4))
    finally:
        _in_delegation.active = False
    _finish(sid, result.get("status", "error"), ctx)
    return result


def _finish(sid: str, status: str, ctx: "WorkerContext") -> None:
    if session_store is None:
        return
    try:
        session_store.record_snapshot(sid, 1, {"status": status, "artifacts": ctx.artifacts})
        session_store.end_session(sid, status)
    except Exception:  # noqa: BLE001
        pass


def delegate(subtasks: List[Dict[str, Any]], *, parent_id: str = "parent",
             concurrency: Optional[int] = None, timeout: Optional[float] = None,
             cancel_event: Optional[threading.Event] = None, allow_nested: bool = False,
             deleg_root: Optional[str] = None) -> Dict[str, Any]:
    """Run ``subtasks`` concurrently in isolated workspaces. Returns a structured batch result.

    - **concurrency**: max workers in flight (default ``OMEGACLAW_DELEGATION_CONCURRENCY`` / 4).
    - **timeout**: per-worker seconds (a worker exceeding it is marked ``timeout``).
    - **cancel_event**: set it (e.g. on parent interruption) to stop the batch — not-yet-started
      workers are skipped and running ones observe ``ctx.cancelled()``; the batch returns promptly.
    - **allow_nested**: nested delegation is refused by default (runaway guard).
    """
    if getattr(_in_delegation, "active", False) and not allow_nested:
        raise DelegationError("nested delegation refused by default (set allow_nested=True)")

    concurrency = concurrency or _DEFAULT_CONCURRENCY
    per_timeout = timeout if timeout is not None else _DEFAULT_TIMEOUT
    cancel = cancel_event or threading.Event()
    own_root = deleg_root is None
    deleg_root = deleg_root or tempfile.mkdtemp(prefix="omegaclaw-deleg-{}-".format(parent_id))

    results: Dict[str, Dict[str, Any]] = {}
    started = time.time()
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futures = {ex.submit(_run_one, st, parent_id, deleg_root, cancel, per_timeout):
                   str(st.get("id")) for st in subtasks}
        for fut, tid in futures.items():
            st = next(s for s in subtasks if str(s.get("id")) == tid)
            t = st.get("timeout", per_timeout)
            try:
                results[tid] = fut.result(timeout=t)
            except FutureTimeout:
                results[tid] = {"id": tid, "session_id": "deleg-{}-{}".format(parent_id, tid),
                                "workdir": os.path.join(deleg_root, tid), "status": "timeout",
                                "summary": "", "artifacts": [],
                                "duration": round(time.time() - started, 4)}
            except Exception as e:  # noqa: BLE001
                results[tid] = {"id": tid, "status": "error", "error": str(e), "artifacts": []}

    ordered = [results.get(str(st.get("id")), {"id": str(st.get("id")), "status": "missing"})
               for st in subtasks]
    wall = round(time.time() - started, 4)
    counts: Dict[str, int] = {}
    for r in ordered:
        counts[r.get("status", "?")] = counts.get(r.get("status", "?"), 0) + 1
    out = {"ok": counts.get("error", 0) == 0 and counts.get("missing", 0) == 0,
           "parent_id": parent_id, "wall_clock": wall, "concurrency": concurrency,
           "counts": counts, "results": ordered, "deleg_root": deleg_root}
    return out


def cleanup(batch_result: Dict[str, Any]) -> None:
    """Remove a batch's isolated workspaces (call when the artifacts have been consumed)."""
    root = batch_result.get("deleg_root")
    if root and os.path.isdir(root):
        shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------------------------- selftest

def _selftest() -> None:
    for k in ("OMEGACLAW_SESSION_DB",):
        os.environ.pop(k, None)
    os.environ["OMEGACLAW_SESSION_DB"] = os.path.join(tempfile.mkdtemp(), "s.db")

    def _sleeper(ctx):
        if ctx.cancelled():
            return "skipped"
        time.sleep(0.15)
        ctx.write_artifact("out.txt", "done " + ctx.task_id)
        return "processed " + ctx.task_id

    tasks = [{"id": "t%d" % i, "run": _sleeper} for i in range(6)]

    # parallel is much faster than serial
    par = delegate(tasks, parent_id="p1", concurrency=6, timeout=5)
    assert par["counts"].get("ok") == 6, par["counts"]
    assert par["wall_clock"] < 0.6, par["wall_clock"]        # ~0.15s vs 0.9s serial
    # structured outputs: session id + artifact path per subagent
    for r in par["results"]:
        assert r["session_id"].startswith("deleg-p1-") and r["artifacts"], r
        assert os.path.isfile(r["artifacts"][0])
    cleanup(par)

    # artifact containment: escaping writes are refused (isolation enforced)
    def _escaper(ctx):
        try:
            ctx.write_artifact("../escape.txt", "leak")
            return "ESCAPED"
        except DelegationError:
            return "blocked"
    esc = delegate([{"id": "e1", "run": _escaper}], parent_id="p2", timeout=5)
    assert esc["results"][0]["summary"] == "blocked", esc["results"][0]
    cleanup(esc)

    # timeout: a slow worker is marked timeout, the batch still returns
    def _slow(ctx):
        time.sleep(2.0)
        return "late"
    to = delegate([{"id": "s1", "run": _slow, "timeout": 0.2}], parent_id="p3")
    assert to["results"][0]["status"] == "timeout", to["results"][0]
    cleanup(to)

    # cancellation: a pre-set cancel event skips work
    ev = threading.Event(); ev.set()
    can = delegate([{"id": "c%d" % i, "run": _sleeper} for i in range(4)], parent_id="p4",
                   cancel_event=ev, timeout=5)
    assert all(r["status"] == "cancelled" for r in can["results"]), can["counts"]
    cleanup(can)

    # no nested delegation by default
    def _nester(ctx):
        try:
            delegate([{"id": "n", "run": lambda c: "x"}], parent_id="inner")
            return "NESTED"
        except DelegationError:
            return "refused"
    nst = delegate([{"id": "d1", "run": _nester}], parent_id="p5", timeout=5)
    assert nst["results"][0]["summary"] == "refused", nst["results"][0]

    del os.environ["OMEGACLAW_SESSION_DB"]
    print("delegation self-tests passed")


if __name__ == "__main__":
    _selftest()
