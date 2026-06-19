"""Fixture dataset for the memory-provenance KPI benchmark (Issue #5).

Facts spanning every source_type, including two game-state facts that are
explicitly **superseded** by the current-turn fact. Each item carries a
`relevant` flag for the benchmark query ("city A food situation"): superseded and
low-confidence items are NOT trustworthy answers.

`superseded: True` mirrors what `remember_claim(..., supersedes=...)` sets on the
old record in production, so the benchmark exercises the *implemented* supersession
filter (not fixture-only logic).
"""

FIXTURES = [
    # current, high-trust, relevant
    {"claim": "City A has low food production this turn", "source": "freeciv.turn_42",
     "source_type": "game_state", "turn_id": 42, "created_at": "2026-06-17T10:00:00Z", "relevant": True},
    {"claim": "User said City A should prioritize food", "source": "user.msg_7",
     "source_type": "user", "created_at": "2026-06-17T10:01:00Z", "relevant": True},
    {"claim": "Granaries increase a city's food storage", "source": "freeciv_manual.md",
     "source_type": "knowledge_prior", "created_at": "2026-06-17T09:00:00Z", "relevant": True},
    {"claim": "Food shortage check returned deficit=3 for City A", "source": "tool.fooddiff",
     "source_type": "tool_result", "created_at": "2026-06-17T10:02:00Z", "relevant": True},
    # low-confidence LLM guess -> not trustworthy
    {"claim": "City A probably has plenty of food, I think", "source": "model",
     "source_type": "llm", "created_at": "2026-06-17T10:03:00Z", "relevant": False},
    # earlier-turn game facts, explicitly superseded by turn 42 (remember_claim marks these)
    {"claim": "City A had surplus food", "source": "freeciv.turn_30",
     "source_type": "game_state", "turn_id": 30, "created_at": "2026-06-17T08:00:00Z",
     "superseded": True, "relevant": False},
    {"claim": "City A food is unknown (pending survey)", "source": "freeciv.turn_41",
     "source_type": "game_state", "turn_id": 41, "created_at": "2026-06-17T09:30:00Z",
     "superseded": True, "relevant": False},
]
