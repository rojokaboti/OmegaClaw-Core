"""Session-scoped reasoning state (Issue #8).

The default ``(metta $str)`` skill is a stateless read-eval in the global ``&self`` space, so
multi-turn continuity is pushed into prompts/memory. This module adds **named reasoning
sessions**: a process-local store that accumulates *premise expressions* per session and replays
them through the existing two-premise ``(|- a b)`` / ``(|~ a b)`` inference path.

Why a Python premise-store (not a real per-session AtomSpace): the runtime is PeTTa (MeTTa on
SWI-Prolog) with no named-AtomSpace API (only ``&self``) and a one-way ``py-call`` bridge
(MeTTa->Python), so Python cannot own a live space. And NAL/PLN inference is strictly two-premise,
so "session state" is naturally a list of premise atoms replayed pairwise against a query — which
is exactly what this store does. ``(metta ...)`` is left untouched; these sit alongside it.

MeTTa handlers (src/metta_sessions.metta) call ``create``/``add_fact``/``clear``/``snapshot`` via
``py-call`` and evaluate ``infer_program(...)`` through the real ``eval``/``|-`` path.

Limits (env, best-effort): OMEGACLAW_MAX_SESSIONS (16, LRU-evict), OMEGACLAW_MAX_FACTS_PER_SESSION
(500, FIFO-drop oldest), OMEGACLAW_MAX_SNAPSHOT_BYTES (1_000_000). Snapshots go to
OMEGACLAW_SESSION_SNAPSHOT_DIR (default <repo>/memory/traces/sessions/).
"""

import json
import os
import time
from collections import OrderedDict

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# session_id -> {"facts": [expr_str], "created": ts, "last": ts}
_sessions = OrderedDict()


# --------------------------------------------------------------------------- config

def _int_env(name, default):
    try:
        v = int(os.environ.get(name, ""))
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _max_sessions():
    return _int_env("OMEGACLAW_MAX_SESSIONS", 16)


def _max_facts():
    return _int_env("OMEGACLAW_MAX_FACTS_PER_SESSION", 500)


def _max_snapshot_bytes():
    return _int_env("OMEGACLAW_MAX_SNAPSHOT_BYTES", 1_000_000)


def _snapshot_dir():
    return os.environ.get("OMEGACLAW_SESSION_SNAPSHOT_DIR",
                          os.path.join(_REPO_ROOT, "memory", "traces", "sessions"))


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


# --------------------------------------------------------------------------- lifecycle

def _touch(sid):
    """Mark session most-recently-used (for LRU)."""
    if sid in _sessions:
        _sessions.move_to_end(sid)
        _sessions[sid]["last"] = _now()


def _evict_if_needed():
    while len(_sessions) > _max_sessions():
        _sessions.popitem(last=False)  # drop least-recently-used


def create(sid):
    """Create a session if absent (idempotent). Returns a short status string."""
    sid = str(sid)
    if sid in _sessions:
        _touch(sid)
        return "SESSION-EXISTS:" + sid
    _sessions[sid] = {"facts": [], "created": _now(), "last": _now()}
    _evict_if_needed()
    return "SESSION-CREATED:" + sid


def add_fact(sid, expr):
    """Append a premise expression to a session (auto-creates). FIFO-caps facts.

    Idempotent: re-adding an expression already in the session is a no-op (returns FACT-DUP).
    This keeps "each fact added once" true even when a producer re-seeds unchanged premises
    every turn (e.g. FreeCiv ``observe`` on an unchanged state), so it never doubles the store
    or evicts genuine history under the fact cap.
    """
    sid = str(sid)
    expr = "" if expr is None else str(expr).strip()
    if not expr:
        return "FACT-EMPTY:" + sid
    if sid not in _sessions:
        create(sid)
    facts = _sessions[sid]["facts"]
    if expr in facts:
        _touch(sid)
        return "FACT-DUP:{}:{}".format(sid, len(facts))
    facts.append(expr)
    cap = _max_facts()
    if len(facts) > cap:
        del facts[0:len(facts) - cap]  # drop oldest
    _touch(sid)
    return "FACT-ADDED:{}:{}".format(sid, len(facts))


def facts(sid):
    """Return a copy of the session's premise list (empty if unknown)."""
    sid = str(sid)
    return list(_sessions[sid]["facts"]) if sid in _sessions else []


def clear(sid):
    """Remove a session and its facts. Returns a short status string."""
    sid = str(sid)
    existed = _sessions.pop(sid, None) is not None
    return ("SESSION-CLEARED:" if existed else "NO-SESSION:") + sid


def info():
    """Diagnostic snapshot of all sessions (id -> fact count)."""
    return {sid: len(s["facts"]) for sid, s in _sessions.items()}


def reset():
    """Test helper: drop all sessions."""
    _sessions.clear()


# --------------------------------------------------------------------------- infer

def infer_program(sid, query):
    """Build a MeTTa program string that infers a query against the session's stored premises.

    NAL/PLN are two-premise, so we pair the query with EACH stored fact:
    ``(unique-atom (collapse (superpose ((|- <f1> <query>) (|- <f2> <query>) ...))))``.
    No facts -> ``()`` (nothing to infer). The MeTTa wrapper evaluates this via the same
    ``sread``/``eval`` path as ``(metta ...)``.
    """
    sid = str(sid)
    query = "" if query is None else str(query).strip()
    session_facts = facts(sid)
    _touch(sid)
    if not session_facts or not query:
        return "()"
    calls = " ".join("(|- {} {})".format(f, query) for f in session_facts)
    return "(unique-atom (collapse (superpose ({}))))".format(calls)


# --------------------------------------------------------------------------- snapshot

def _safe_name(sid):
    return "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in str(sid)) or "session"


def snapshot(sid):
    """Write one JSON line capturing the session's state to the snapshot dir; return the path.

    Best-effort: a snapshot failure never raises to the caller. Facts are truncated so the
    serialized record stays under OMEGACLAW_MAX_SNAPSHOT_BYTES.
    """
    sid = str(sid)
    if sid not in _sessions:
        return "NO-SESSION:" + sid
    s = _sessions[sid]
    record = {"sid": sid, "created": s["created"], "last": s["last"],
              "count": len(s["facts"]), "facts": list(s["facts"]), "ts": _now()}
    # size cap: drop oldest facts until under the byte budget
    limit = _max_snapshot_bytes()
    while record["facts"] and len(json.dumps(record, ensure_ascii=False)) > limit:
        record["facts"].pop(0)
        record["truncated"] = True
    path = os.path.join(_snapshot_dir(), _safe_name(sid) + ".jsonl")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:  # best-effort; never break the loop
        print("[metta_sessions] WARNING could not write snapshot ({}): {}".format(path, exc), flush=True)
        return "SNAPSHOT-FAILED:" + sid
    return path


# --------------------------------------------------------------------------- self-test

def _selftest():
    """Lightweight self-tests runnable without pytest/Docker."""
    import tempfile

    reset()
    assert create("a") == "SESSION-CREATED:a" and create("a") == "SESSION-EXISTS:a"
    assert add_fact("a", "((--> x y) (stv 1.0 0.9))").startswith("FACT-ADDED:a:1")
    add_fact("a", "((--> y z) (stv 1.0 0.9))")
    assert len(facts("a")) == 2
    assert add_fact("a", "((--> x y) (stv 1.0 0.9))").startswith("FACT-DUP") and len(facts("a")) == 2

    # infer pairs the query with each stored fact
    prog = infer_program("a", "((--> z w) (stv 1.0 0.9))")
    assert prog.count("(|- ") == 2 and "superpose" in prog and prog.startswith("(unique-atom"), prog
    assert infer_program("nope", "q") == "()" and infer_program("a", "") == "()"

    # isolation: a second session does not see a's facts
    add_fact("b", "((--> p q) (stv 1.0 0.9))")
    assert facts("a") != facts("b") and len(facts("b")) == 1

    # add_fact auto-creates; clear removes
    assert clear("a").startswith("SESSION-CLEARED") and facts("a") == []
    assert clear("a").startswith("NO-SESSION")

    # FIFO fact cap
    os.environ["OMEGACLAW_MAX_FACTS_PER_SESSION"] = "3"
    reset()
    for i in range(5):
        add_fact("c", "f{}".format(i))
    assert facts("c") == ["f2", "f3", "f4"], facts("c")
    os.environ.pop("OMEGACLAW_MAX_FACTS_PER_SESSION", None)

    # LRU session cap
    os.environ["OMEGACLAW_MAX_SESSIONS"] = "2"
    reset()
    create("s1"); create("s2"); create("s3")  # s1 evicted
    assert set(info()) == {"s2", "s3"}, info()
    os.environ.pop("OMEGACLAW_MAX_SESSIONS", None)

    # snapshot writes a file
    with tempfile.TemporaryDirectory() as d:
        os.environ["OMEGACLAW_SESSION_SNAPSHOT_DIR"] = d
        reset(); add_fact("g1", "((--> a b) (stv 1.0 0.9))")
        path = snapshot("g1")
        assert os.path.isfile(path)
        rec = json.loads(open(path, encoding="utf-8").readline())
        assert rec["sid"] == "g1" and rec["count"] == 1 and rec["facts"][0].startswith("((-->")
        os.environ.pop("OMEGACLAW_SESSION_SNAPSHOT_DIR", None)
    reset()
    print("metta_sessions self-tests passed")


if __name__ == "__main__":
    _selftest()
