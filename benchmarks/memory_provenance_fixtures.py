"""Fixture dataset for the memory-provenance KPI benchmark (Issue #5).

A small set of facts spanning every source_type plus a stale/contradictory fact
and a superseded fact. Each item also carries a `relevant` flag for a single
benchmark query ("city A food situation") so we can compute a precision proxy:
the stale/superseded/low-confidence items are NOT relevant answers.
"""

# (claim, source, source_type, extra) where extra may set confidence/supersedes/created_at/relevant
FIXTURES = [
    # high-trust, current, relevant
    {"claim": "City A has low food production this turn", "source": "freeciv.turn_42",
     "source_type": "game_state", "turn_id": 42, "created_at": "2026-06-17T10:00:00Z", "relevant": True},
    {"claim": "User said City A should prioritize food", "source": "user.msg_7",
     "source_type": "user", "created_at": "2026-06-17T10:01:00Z", "relevant": True},
    {"claim": "Granaries increase a city's food storage", "source": "freeciv_manual.md",
     "source_type": "knowledge_prior", "created_at": "2026-06-17T09:00:00Z", "relevant": True},
    {"claim": "Food shortage check returned deficit=3 for City A", "source": "tool.fooddiff",
     "source_type": "tool_result", "created_at": "2026-06-17T10:02:00Z", "relevant": True},
    # low-confidence LLM guess about the topic -> NOT a trustworthy answer
    {"claim": "City A probably has plenty of food, I think", "source": "model",
     "source_type": "llm", "created_at": "2026-06-17T10:03:00Z", "relevant": False},
    # stale/contradictory game fact from an earlier turn -> NOT current truth
    {"claim": "City A had surplus food", "source": "freeciv.turn_30",
     "source_type": "game_state", "turn_id": 30, "created_at": "2026-06-17T08:00:00Z", "relevant": False},
    # explicitly superseded fact -> excluded when filtering supersession
    {"claim": "City A food is unknown (pending survey)", "source": "freeciv.turn_41",
     "source_type": "game_state", "turn_id": 41, "created_at": "2026-06-17T09:30:00Z",
     "supersedes": "", "superseded": True, "relevant": False},
]
