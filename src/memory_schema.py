"""Provenance-aware memory schema (Issue #5).

A common metadata shape for long-term memories and knowledge-prior chunks so
retrieved facts carry *source*, *source_type*, *confidence*, *timestamp*,
*session/turn*, and (optionally) related symbolic *atoms* — for auditability and
benchmark reliability.

Split into:

* **Pure helpers** (no chromadb dependency, host-testable): build/validate the
  metadata, default confidences per source type, and build/evaluate retrieval
  filters.
* **Chroma-backed store** (reuses ``rag._get_collection()``): ``remember_claim`` /
  ``query_claims``. Imported lazily so the pure helpers work on hosts without
  chromadb installed.

The existing ``remember``/``query`` skills (backed by the external
``petta_lib_chromadb`` library) are unchanged; this adds a parallel, provenance-
aware path writing to the same ``memories`` collection, so structured claims are
still recalled by the normal similarity ``query`` while ``query_claims`` adds
metadata filtering.

Note: Chroma metadata values must be scalars (str/int/float/bool), so ``atoms``
(a list) is stored as a JSON string in ``atoms_json``.
"""

from __future__ import annotations

import hashlib
import json
import re
import time

SOURCE_TYPES = ("game_state", "user", "llm", "knowledge_prior", "tool_result")

# Deterministic game-state facts are fully trusted; LLM guesses are discounted.
DEFAULT_CONFIDENCE = {
    "game_state": 1.0,
    "tool_result": 0.9,
    "user": 0.8,
    "knowledge_prior": 0.7,
    "llm": 0.55,
}

# Knowledge-prior chunks indexed by rag.py use this confidence.
KNOWLEDGE_PRIOR_CONFIDENCE = DEFAULT_CONFIDENCE["knowledge_prior"]

_PROVENANCE_FIELDS = ("source", "source_type", "confidence", "created_at")


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def default_confidence(source_type):
    return DEFAULT_CONFIDENCE.get(source_type, 0.55)


def build_metadata(claim, source, source_type, confidence=None, session_id="",
                   turn_id=None, atoms=None, supersedes=None, created_at=None):
    """Build a chroma-safe (scalar-valued) provenance metadata dict.

    Confidence defaults by ``source_type`` (game_state→1.0, llm→0.55, …). ``atoms``
    (a list of symbolic atom strings) is serialized to ``atoms_json``.
    """
    if confidence is None:
        confidence = default_confidence(source_type)
    meta = {
        "claim": claim,
        "source": source,
        "source_type": source_type,
        "confidence": float(confidence),
        "created_at": created_at or _now_iso(),
        "session_id": session_id or "",
        "atoms_json": json.dumps(list(atoms) if atoms else [], ensure_ascii=False),
        "supersedes": supersedes or "",
    }
    if turn_id is not None:
        meta["turn_id"] = int(turn_id)
    return meta


def validate_metadata(meta):
    """Return an error string if ``meta`` is not a valid provenance record, else None."""
    if not isinstance(meta, dict):
        return "metadata is not a mapping"
    if not meta.get("claim"):
        return "missing 'claim'"
    if not meta.get("source"):
        return "missing 'source'"
    st = meta.get("source_type")
    if st not in SOURCE_TYPES:
        return f"unknown source_type {st!r} (allowed: {list(SOURCE_TYPES)})"
    conf = meta.get("confidence")
    if not isinstance(conf, (int, float)) or not (0.0 <= float(conf) <= 1.0):
        return f"confidence {conf!r} must be a number in [0, 1]"
    return None


def claim_id(meta):
    """Deterministic, stable id so re-remembering an identical claim is idempotent."""
    digest = hashlib.sha1((meta.get("claim") or "").encode("utf-8")).hexdigest()[:10]
    turn = meta.get("turn_id", "")
    src = re.sub(r"[^A-Za-z0-9_.-]", "_", str(meta.get("source") or "src"))
    return f"claim_{src}_{turn}_{digest}"


def build_where(filters):
    """Build a Chroma ``where`` clause from a filter dict.

    Supported filters: ``source_type``, ``min_confidence``, ``session_id``,
    ``turn_id``, ``since``/``until`` (ISO ``created_at`` bounds), and
    ``include_superseded`` (default False → exclude records that supersede others...
    actually exclude records that *have been* superseded; see note). Returns ``None``
    when no filters apply (Chroma treats that as "no filter").
    """
    filters = filters or {}
    clauses = []
    if filters.get("source_type"):
        clauses.append({"source_type": {"$eq": filters["source_type"]}})
    if filters.get("min_confidence") is not None:
        clauses.append({"confidence": {"$gte": float(filters["min_confidence"])}})
    if filters.get("session_id"):
        clauses.append({"session_id": {"$eq": filters["session_id"]}})
    if filters.get("turn_id") is not None:
        clauses.append({"turn_id": {"$eq": int(filters["turn_id"])}})
    if filters.get("since"):
        clauses.append({"created_at": {"$gte": filters["since"]}})
    if filters.get("until"):
        clauses.append({"created_at": {"$lte": filters["until"]}})
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def matches_filters(meta, filters):
    """Host-side mirror of :func:`build_where` for a single metadata record."""
    filters = filters or {}
    if filters.get("source_type") and meta.get("source_type") != filters["source_type"]:
        return False
    if filters.get("min_confidence") is not None and float(meta.get("confidence", 0)) < float(filters["min_confidence"]):
        return False
    if filters.get("session_id") and meta.get("session_id") != filters["session_id"]:
        return False
    if filters.get("turn_id") is not None and meta.get("turn_id") != int(filters["turn_id"]):
        return False
    if filters.get("since") and str(meta.get("created_at", "")) < filters["since"]:
        return False
    if filters.get("until") and str(meta.get("created_at", "")) > filters["until"]:
        return False
    return True


# --- chroma-backed store (reuses rag.py's client) -------------------------

def _collection():
    try:
        from rag import _get_collection
    except ImportError:  # pragma: no cover - alternate import path
        from src.rag import _get_collection
    return _get_collection()


def remember_claim(claim, embedding, source_type, source=None, confidence=None,
                   session_id="", turn_id=None, atoms=None, supersedes=None):
    """Write a structured, provenance-tagged claim to the shared memories collection.

    ``embedding`` is computed MeTTa-side (``(embed $claim)``), mirroring ``remember``.
    Returns the stable claim id.
    """
    meta = build_metadata(
        claim, source or f"{source_type}:adhoc", source_type, confidence=confidence,
        session_id=session_id, turn_id=turn_id, atoms=atoms, supersedes=supersedes,
    )
    err = validate_metadata(meta)
    if err:
        raise ValueError(f"invalid claim metadata: {err}")
    cid = claim_id(meta)
    _collection().upsert(ids=[cid], embeddings=[embedding], documents=[claim], metadatas=[meta])
    print(f"[memory_schema] REMEMBER_CLAIM id={cid} source_type={source_type} confidence={meta['confidence']}", flush=True)
    return cid


def query_claims(embedding, n=5, filters=None):
    """Similarity query returning provenance metadata, with optional filtering.

    Returns a list of ``{document, metadata, distance}``. By default superseded
    records are NOT excluded here (supersession is recorded on the superseding
    claim's ``supersedes`` field); pass an explicit filter to scope results.
    """
    where = build_where(filters)
    kwargs = {"query_embeddings": [embedding], "n_results": int(n)}
    if where is not None:
        kwargs["where"] = where
    res = _collection().query(**kwargs)
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    out = []
    for i, doc in enumerate(docs):
        out.append({
            "document": doc,
            "metadata": metas[i] if i < len(metas) else {},
            "distance": dists[i] if i < len(dists) else None,
        })
    return out


def query_claims_text(embedding, n=5):
    """MeTTa-facing recall: a readable string with inline provenance per result."""
    results = query_claims(embedding, n=n, filters=None)
    if not results:
        return "NO_CLAIMS_FOUND"
    lines = []
    for r in results:
        m = r.get("metadata") or {}
        lines.append(
            f"- {r.get('document')} "
            f"[source_type={m.get('source_type', '?')} confidence={m.get('confidence', '?')} "
            f"source={m.get('source', '?')}]"
        )
    return "\n".join(lines)


def remember_claim_llm(claim, embedding):
    """MeTTa-facing wrapper: a claim the agent itself asserts is an ``llm`` source
    (discounted confidence 0.55). Programmatic producers call ``remember_claim`` with
    a higher-trust source_type (game_state/tool_result/...)."""
    return remember_claim(claim, embedding, "llm")


def _selftest():
    # game_state -> 1.0, llm -> 0.55
    m = build_metadata("City A low food", "freeciv.turn_42", "game_state")
    assert m["confidence"] == 1.0 and m["source_type"] == "game_state"
    assert validate_metadata(m) is None
    ml = build_metadata("maybe X", "model", "llm")
    assert ml["confidence"] == 0.55

    # atoms serialized to JSON string (chroma scalar constraint)
    ma = build_metadata("c", "s", "user", atoms=["(Inheritance A B)", "(stv 1 0.9)"])
    assert isinstance(ma["atoms_json"], str) and json.loads(ma["atoms_json"])[0] == "(Inheritance A B)"

    # validation failures
    assert validate_metadata({"claim": "c", "source": "s", "source_type": "bogus", "confidence": 1.0})
    assert validate_metadata({"claim": "c", "source": "s", "source_type": "user", "confidence": 2.0})
    assert validate_metadata({"claim": "", "source": "s", "source_type": "user", "confidence": 1.0})

    # stable id is deterministic
    assert claim_id(m) == claim_id(build_metadata("City A low food", "freeciv.turn_42", "game_state"))

    # where builder
    assert build_where(None) is None
    assert build_where({"source_type": "game_state"}) == {"source_type": {"$eq": "game_state"}}
    w = build_where({"source_type": "user", "min_confidence": 0.8})
    assert "$and" in w and {"confidence": {"$gte": 0.8}} in w["$and"]

    # matches_filters parity
    assert matches_filters(ml, {"min_confidence": 0.5}) and not matches_filters(ml, {"min_confidence": 0.7})
    assert matches_filters(m, {"source_type": "game_state"}) and not matches_filters(m, {"source_type": "llm"})

    print("memory_schema self-tests passed")


if __name__ == "__main__":
    _selftest()
