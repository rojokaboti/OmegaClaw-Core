# FreeCiv A/B — PLN (OmegaClaw) vs plain-LLM

Same model/provider/seed/validation; only the state representation differs (pln = plain facts + MeTTa/PLN-derived recommendations; plain = plain facts only).

| Metric | pln | plain | winner |
| --- | --- | --- | --- |
| Final score | 0 | 0 | tie |
| Peak score | 0 | 0 | tie |
| Cities | 0 | 1 | plain |
| Units | 0 | 0 |  |
| Techs | 8 | 11 | plain |
| Last turn | 258 | 258 |  |
| Turns advanced | 257 | 257 | tie |
| Illegal-action rate | 0.0 | 0.0 | tie |
| Avg LLM ms | 1645.8 | 3202.3 |  |
| Avg reason ms | 227.3 | None |  |
| Avg PLN conclusions/turn | 1.9 | 0.0 |  |
| LLM errors | 0 | 0 |  |

**Verdict:** plain (pln won 0, plain won 2 of 6 tracked metrics).

> Caveat: one seed = a single matched pair — directional, not statistically conclusive. PLN reasoning here is one-hop (situation→priority) via two-premise NAL.
