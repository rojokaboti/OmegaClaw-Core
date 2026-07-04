"""Deterministic fixtures for the session-reasoning KPI benchmark (Issue #8).

Two independent multi-turn "games" (session ids), each a sequence of turns. Every turn adds a
few premise sentences and issues one inference query — modeling a benchmark game where facts
accumulate across turns. Used to measure fact preservation, cross-session isolation, and the
reduction in repeated premise re-transmission vs. a stateless baseline.
"""

GAMES = {
    "game-a": [
        {"turn": 1, "facts": ["((--> CityA LowFood) (stv 1.0 0.99))",
                              "((--> CityA Coastal) (stv 1.0 0.99))"],
         "query": "((--> LowFood BuildGranary) (stv 1.0 0.9))"},
        {"turn": 2, "facts": ["((--> Unit7 Settler) (stv 1.0 0.99))"],
         "query": "((--> Settler CanFoundCity) (stv 1.0 0.9))"},
        {"turn": 3, "facts": ["((--> Enemy9 Near) (stv 1.0 0.99))",
                              "((--> Near Threat) (stv 1.0 0.9))"],
         "query": "((--> Threat Defend) (stv 1.0 0.9))"},
    ],
    "game-b": [
        {"turn": 1, "facts": ["((--> CityB HighProd) (stv 1.0 0.99))"],
         "query": "((--> HighProd BuildWonder) (stv 1.0 0.9))"},
        {"turn": 2, "facts": ["((--> TechBronze Researched) (stv 1.0 0.99))",
                              "((--> Bronze Phalanx) (stv 1.0 0.9))"],
         "query": "((--> Phalanx Defense) (stv 1.0 0.9))"},
    ],
}


if __name__ == "__main__":
    for g, turns in GAMES.items():
        nf = sum(len(t["facts"]) for t in turns)
        print(f"{g}: {len(turns)} turns, {nf} facts")
