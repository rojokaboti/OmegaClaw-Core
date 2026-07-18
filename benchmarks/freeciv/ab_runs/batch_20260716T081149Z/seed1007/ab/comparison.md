# FreeCiv A/B — PLN (OmegaClaw) vs plain-LLM

Same model/provider/seed/validation; only the state representation differs (pln = plain facts + MeTTa/PLN-derived recommendations; plain = plain facts only).

| Metric | pln | plain | winner |
| --- | --- | --- | --- |
| Final score | 0 | 0 | tie |
| Peak score | 0 | 0 | tie |
| Cities | 3 | 2 | pln |
| Units | 22 | 8 |  |
| Techs | 13 | 14 | plain |
| Last turn | 250 | 250 |  |
| Turns advanced | 250 | 250 | tie |
| Illegal-action rate | 0.0 | 0.0 | tie |
| Avg LLM ms | 4256.9 | 4517.3 |  |
| Avg reason ms | 544.4 | None |  |
| Avg PLN conclusions/turn | 1.1 | 0.0 |  |
| LLM errors | 0 | 0 |  |

**Verdict:** tie (pln won 1, plain won 1 of 6 tracked metrics).

> Caveat: one seed = a single matched pair — directional, not statistically conclusive. PLN reasoning here is one-hop (situation→priority) via two-premise NAL.
