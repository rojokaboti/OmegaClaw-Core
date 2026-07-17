# FreeCiv A/B — PLN (OmegaClaw) vs plain-LLM

Same model/provider/seed/validation; only the state representation differs (pln = plain facts + MeTTa/PLN-derived recommendations; plain = plain facts only).

| Metric | pln | plain | winner |
| --- | --- | --- | --- |
| Final score | 0 | 0 | tie |
| Peak score | 0 | 0 | tie |
| Cities | 3 | 3 | tie |
| Units | 29 | 15 |  |
| Techs | 11 | 14 | plain |
| Last turn | 250 | 250 |  |
| Turns advanced | 250 | 250 | tie |
| Illegal-action rate | 0.0 | 0.0 | tie |
| Avg LLM ms | 4262.5 | 4345.8 |  |
| Avg reason ms | 553.8 | None |  |
| Avg PLN conclusions/turn | 0.6 | 0.0 |  |
| LLM errors | 1 | 0 |  |

**Verdict:** plain (pln won 0, plain won 1 of 6 tracked metrics).

> Caveat: one seed = a single matched pair — directional, not statistically conclusive. PLN reasoning here is one-hop (situation→priority) via two-premise NAL.
