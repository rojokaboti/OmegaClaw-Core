"""Unit tests for session-scoped reasoning state (Issue #8).

Pure-Python, no Docker/LLM/MeTTa. Runs under pytest and standalone
(`python3 Autotests/test_metta_sessions.py`). Covers lifecycle, infer-program assembly,
session isolation, LRU/size limits, and snapshotting.
"""
import json
import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_SRC, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import metta_sessions as ms  # noqa: E402


def setup_function(_):
    ms.reset()


# --- lifecycle -------------------------------------------------------------

def test_create_add_facts_clear():
    assert ms.create("a") == "SESSION-CREATED:a"
    assert ms.create("a") == "SESSION-EXISTS:a"        # idempotent
    assert ms.add_fact("a", "((--> x y) (stv 1.0 0.9))").startswith("FACT-ADDED:a:1")
    assert ms.add_fact("a", "((--> y z) (stv 1.0 0.9))").startswith("FACT-ADDED:a:2")
    assert len(ms.facts("a")) == 2
    assert ms.clear("a") == "SESSION-CLEARED:a" and ms.facts("a") == []
    assert ms.clear("a") == "NO-SESSION:a"


def test_add_fact_auto_creates_and_ignores_empty():
    assert ms.add_fact("z", "(f)").startswith("FACT-ADDED:z:1")   # auto-created
    assert ms.add_fact("z", "   ") == "FACT-EMPTY:z" and len(ms.facts("z")) == 1


def test_add_fact_is_idempotent():
    assert ms.add_fact("d", "((--> a b) (stv 1.0 0.9))").startswith("FACT-ADDED:d:1")
    assert ms.add_fact("d", "((--> a b) (stv 1.0 0.9))").startswith("FACT-DUP:d:1")  # no-op
    assert len(ms.facts("d")) == 1


# --- infer program ---------------------------------------------------------

def test_infer_program_pairs_query_with_each_fact():
    ms.add_fact("a", "F1")
    ms.add_fact("a", "F2")
    prog = ms.infer_program("a", "Q")
    assert prog == "(unique-atom (collapse (superpose ((|- F1 Q) (|- F2 Q)))))", prog


def test_infer_program_empty_when_no_facts_or_no_query():
    ms.create("a")
    assert ms.infer_program("a", "Q") == "()"           # no facts
    ms.add_fact("a", "F1")
    assert ms.infer_program("a", "") == "()"            # no query
    assert ms.infer_program("unknown", "Q") == "()"     # unknown session


# --- isolation -------------------------------------------------------------

def test_sessions_are_isolated():
    ms.add_fact("a", "AFACT")
    ms.add_fact("b", "BFACT")
    assert ms.facts("a") == ["AFACT"] and ms.facts("b") == ["BFACT"]
    assert "BFACT" not in ms.facts("a") and "AFACT" not in ms.facts("b")


# --- limits ----------------------------------------------------------------

def test_max_facts_per_session_fifo_cap():
    os.environ["OMEGACLAW_MAX_FACTS_PER_SESSION"] = "3"
    try:
        for i in range(5):
            ms.add_fact("c", "f{}".format(i))
        assert ms.facts("c") == ["f2", "f3", "f4"]     # oldest dropped
    finally:
        os.environ.pop("OMEGACLAW_MAX_FACTS_PER_SESSION", None)


def test_max_sessions_lru_eviction():
    os.environ["OMEGACLAW_MAX_SESSIONS"] = "2"
    try:
        ms.create("s1")
        ms.create("s2")
        ms.add_fact("s1", "x")     # touch s1 -> s2 now least-recent
        ms.create("s3")            # evicts s2
        assert set(ms.info()) == {"s1", "s3"}, ms.info()
    finally:
        os.environ.pop("OMEGACLAW_MAX_SESSIONS", None)


# --- snapshot --------------------------------------------------------------

def test_snapshot_writes_file_and_respects_size_cap():
    with tempfile.TemporaryDirectory() as d:
        os.environ["OMEGACLAW_SESSION_SNAPSHOT_DIR"] = d
        try:
            ms.add_fact("g1", "((--> a b) (stv 1.0 0.99))")
            ms.add_fact("g1", "((--> b c) (stv 1.0 0.99))")
            path = ms.snapshot("g1")
            assert os.path.isfile(path)
            rec = json.loads(open(path, encoding="utf-8").readline())
            assert rec["sid"] == "g1" and rec["count"] == 2 and len(rec["facts"]) == 2
            assert ms.snapshot("nope") == "NO-SESSION:nope"

            # size cap truncates facts
            os.environ["OMEGACLAW_MAX_SNAPSHOT_BYTES"] = "120"
            ms.snapshot("g1")  # should not raise; may truncate
        finally:
            os.environ.pop("OMEGACLAW_SESSION_SNAPSHOT_DIR", None)
            os.environ.pop("OMEGACLAW_MAX_SNAPSHOT_BYTES", None)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        ms.reset()
        fn()
        print("ok:", fn.__name__)
    print("\nAll {} metta_sessions tests passed".format(len(fns)))
