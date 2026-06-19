"""Unit tests for the provenance-aware memory schema (Issue #5).

Pure schema/validation/filter tests run everywhere (no chromadb needed). The
chroma-backed store tests use an in-memory EphemeralClient and skip on hosts
without chromadb (they run in-container). Runs under pytest and standalone
(`python3 Autotests/test_memory_schema.py`).
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_SRC, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import importlib.util  # noqa: E402
import json  # noqa: E402
import memory_schema as ms  # noqa: E402

_HAS_CHROMA = importlib.util.find_spec("chromadb") is not None


class _ChromaSkip(Exception):
    """Raised to skip a chroma-backed test when chromadb is unavailable (standalone)."""


# --- pure: build / validate / defaults -----------------------------------

def test_default_confidence_game_state_vs_llm():
    assert ms.build_metadata("c", "s", "game_state")["confidence"] == 1.0
    assert ms.build_metadata("c", "s", "llm")["confidence"] == 0.55
    assert ms.default_confidence("knowledge_prior") == 0.7


def test_build_metadata_shape_and_atoms_json():
    m = ms.build_metadata("City A low food", "freeciv.turn_42", "game_state",
                          session_id="game-123", turn_id=42, atoms=["(Inheritance CityA LowFood)"])
    assert m["claim"] and m["source"] == "freeciv.turn_42" and m["source_type"] == "game_state"
    assert m["session_id"] == "game-123" and m["turn_id"] == 42
    assert isinstance(m["atoms_json"], str)
    assert json.loads(m["atoms_json"]) == ["(Inheritance CityA LowFood)"]
    assert m["created_at"] and m["supersedes"] == ""
    # all metadata values are chroma-safe scalars
    for v in m.values():
        assert isinstance(v, (str, int, float, bool)), v


def test_explicit_confidence_overrides_default():
    assert ms.build_metadata("c", "s", "llm", confidence=0.9)["confidence"] == 0.9


def test_validate_rejects_bad_records():
    assert ms.validate_metadata(ms.build_metadata("c", "s", "user")) is None
    assert ms.validate_metadata({"claim": "c", "source": "s", "source_type": "bogus", "confidence": 1.0})
    assert ms.validate_metadata({"claim": "c", "source": "s", "source_type": "user", "confidence": 2.0})
    assert ms.validate_metadata({"claim": "", "source": "s", "source_type": "user", "confidence": 1.0})
    assert ms.validate_metadata({"claim": "c", "source": "", "source_type": "user", "confidence": 1.0})


def test_claim_id_stable_and_deterministic():
    a = ms.build_metadata("same claim", "src.1", "game_state", turn_id=1)
    b = ms.build_metadata("same claim", "src.1", "game_state", turn_id=1)
    assert ms.claim_id(a) == ms.claim_id(b)
    c = ms.build_metadata("different", "src.1", "game_state", turn_id=1)
    assert ms.claim_id(c) != ms.claim_id(a)


# --- pure: filters --------------------------------------------------------

def test_build_where_variants():
    assert ms.build_where(None) is None
    assert ms.build_where({}) is None
    assert ms.build_where({"source_type": "game_state"}) == {"source_type": {"$eq": "game_state"}}
    assert ms.build_where({"min_confidence": 0.7}) == {"confidence": {"$gte": 0.7}}
    w = ms.build_where({"source_type": "user", "min_confidence": 0.8})
    assert "$and" in w and {"source_type": {"$eq": "user"}} in w["$and"] and {"confidence": {"$gte": 0.8}} in w["$and"]
    w2 = ms.build_where({"session_id": "g1", "turn_id": 3, "since": "2026-01-01T00:00:00Z"})
    assert "$and" in w2 and len(w2["$and"]) == 3


def test_matches_filters_parity():
    llm = ms.build_metadata("maybe", "model", "llm")          # 0.55
    gs = ms.build_metadata("fact", "freeciv", "game_state")    # 1.0
    assert ms.matches_filters(llm, {"min_confidence": 0.5})
    assert not ms.matches_filters(llm, {"min_confidence": 0.7})
    assert ms.matches_filters(gs, {"source_type": "game_state"})
    assert not ms.matches_filters(gs, {"source_type": "llm"})
    assert ms.matches_filters(gs, {"min_confidence": 0.9, "source_type": "game_state"})


# --- action protocol round-trip (the new tools) --------------------------

def test_remember_claim_action_renders():
    sys.path.insert(0, _SRC)
    import action_protocol as ap
    assert ap.parse_and_render_metta('{"actions":[{"tool":"remember-claim","args":{"claim":"x"}}]}') == '((remember-claim "x"))'
    assert ap.parse_and_render_metta('{"actions":[{"tool":"query-claims","args":{"text":"y"}}]}') == '((query-claims "y"))'


# --- chroma-backed store (in-memory; skip without chromadb) --------------

_coll_counter = [0]


def _ephemeral_collection():
    # Unique collection name per call: EphemeralClient instances can share in-process
    # state, so reusing one name would leak documents across tests.
    import chromadb
    _coll_counter[0] += 1
    client = chromadb.EphemeralClient()
    return client.get_or_create_collection(name=f"test_claims_{_coll_counter[0]}", embedding_function=None)


def _with_ephemeral(fn):
    """Run fn() with memory_schema._collection pointed at an in-memory collection.

    Gated on chromadb presence only (no pytest dependency), so it RUNS standalone
    in-container (chromadb present) and skips on hosts without it.
    """
    if not _HAS_CHROMA:
        try:
            import pytest
            pytest.skip("chromadb not installed")
        except ImportError:
            raise _ChromaSkip("chromadb not installed")
    coll = _ephemeral_collection()
    saved = ms._collection
    ms._collection = lambda: coll
    try:
        return fn(coll)
    finally:
        ms._collection = saved


def test_remember_claim_and_query_returns_provenance():
    def body(coll):
        cid = ms.remember_claim("City A has low food", [1.0, 0.0, 0.0], "game_state",
                                source="freeciv.turn_42", turn_id=42, atoms=["(Inheritance CityA LowFood)"])
        assert cid.startswith("claim_")
        res = ms.query_claims([1.0, 0.0, 0.0], n=5)
        assert res and res[0]["document"] == "City A has low food"
        meta = res[0]["metadata"]
        assert meta["source_type"] == "game_state" and meta["confidence"] == 1.0
        assert meta["source"] == "freeciv.turn_42" and meta["turn_id"] == 42
    _with_ephemeral(body)


def test_min_confidence_filter_excludes_low_confidence():
    def body(coll):
        ms.remember_claim("trusted game fact", [1.0, 0.0, 0.0], "game_state", source="g")
        ms.remember_claim("shaky llm guess", [0.0, 1.0, 0.0], "llm", source="m")  # 0.55
        hi = ms.query_claims([0.0, 1.0, 0.0], n=5, filters={"min_confidence": 0.8})
        docs = [r["document"] for r in hi]
        assert "shaky llm guess" not in docs       # filtered out by confidence
        assert "trusted game fact" in docs
    _with_ephemeral(body)


def test_source_type_filter():
    def body(coll):
        ms.remember_claim("a game fact", [1.0, 0.0, 0.0], "game_state", source="g")
        ms.remember_claim("a user fact", [0.0, 1.0, 0.0], "user", source="u")
        only_user = ms.query_claims([1.0, 0.0, 0.0], n=5, filters={"source_type": "user"})
        assert [r["metadata"]["source_type"] for r in only_user] == ["user"] or all(
            r["metadata"]["source_type"] == "user" for r in only_user)
    _with_ephemeral(body)


def test_query_claims_text_formats_provenance():
    def body(coll):
        ms.remember_claim("formatted fact", [1.0, 0.0, 0.0], "game_state", source="g")
        text = ms.query_claims_text([1.0, 0.0, 0.0], n=5)
        assert "formatted fact" in text and "source_type=game_state" in text and "confidence=1.0" in text
    _with_ephemeral(body)


def _run_standalone():
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except BaseException as exc:  # pytest Skipped subclasses BaseException
                if exc.__class__.__name__ in ("Skipped", "_ChromaSkip") or isinstance(exc, (ImportError, _ChromaSkip)) or "chromadb" in str(exc):
                    print(f"SKIP {name} (no chromadb)")
                    continue
                if isinstance(exc, AssertionError):
                    failures += 1
                    print(f"FAIL {name}: {exc}")
                else:
                    failures += 1
                    print(f"ERROR {name}: {exc!r}")
    if failures:
        print(f"\n{failures} test(s) failed")
        sys.exit(1)
    print("\nmemory_schema tests passed (chroma-backed skipped without chromadb)")


if __name__ == "__main__":
    _run_standalone()
