# FreeCiv State-to-Atoms & Action Adapter KPI Benchmark — Issue #6

Fixture dataset: **6 `llm_optimized` states** (schema-grounded, verified against freeciv-llm `state_extractor.py`) covering food shortage, undefended city, settler near threat, tech choice, unit movement, and worker improvement.

- **baseline** = raw state as text: no symbolic atoms, no pre-submission legality check (illegal actions reach the game).
- **candidate** = deterministic state->PLN atoms + `validate_action` gate.

| Metric | baseline | candidate |
| --- | --- | --- |
| States converted to atoms | 0/6 | 6/6 |
| Mean field coverage | 0.00 | 1.00 |
| **Invalid-action submission rate** | **1.00** | **0.00** |
| Legal-action acceptance | n/a | 1.00 |
| Deterministic facts/atoms (2 runs identical) | n/a | True |

### Per-fixture

| Fixture | category | facts | atoms | coverage | determ. | legal ok | illegal blocked |
| --- | --- | --- | --- | --- | --- | --- | --- |
| city_food_shortage | economy | 12 | 12 | 1.00 | True | 2/2 | 2/2 |
| undefended_city | military | 12 | 12 | 1.00 | True | 2/2 | 2/2 |
| settler_near_threat | military | 8 | 8 | 1.00 | True | 2/2 | 2/2 |
| tech_choice | science | 13 | 13 | 1.00 | True | 2/2 | 2/2 |
| unit_movement | movement | 6 | 6 | 1.00 | True | 2/2 | 2/2 |
| worker_improvement | economy | 10 | 10 | 1.00 | True | 3/3 | 2/2 |

The candidate converts every state into deterministic PLN atoms and rejects **100%** of the illegal candidate actions before they reach `action_submit`, while accepting all legal ones. The baseline (raw text, no gate) would submit every illegal action. Live win-rate/score KPIs require a running game and are measured in the live E2E phase (report §5).

Reproduce: `python3 benchmarks/freeciv_benchmark.py`
