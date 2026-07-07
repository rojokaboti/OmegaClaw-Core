# FreeCiv A/B — PLN (OmegaClaw) vs plain-LLM

Same model/provider/seed/validation; only the state representation differs (pln = plain facts + MeTTa/PLN-derived recommendations; plain = plain facts only).

| Metric | pln | plain | winner |
| --- | --- | --- | --- |
| Final score | 0 | 0 | tie |
| Peak score | 0 | 0 | tie |
| Cities | 1 | 2 | plain |
| Units | 15 | 0 |  |
| Techs | 10 | 15 | plain |
| Last turn | 475 | 475 |  |
| Turns advanced | 474 | 474 | tie |
| Illegal-action rate | 0.0 | 0.0 | tie |
| Avg LLM ms | 1845.3 | 2969.5 |  |
| Avg reason ms | 226.0 | None |  |
| Avg PLN conclusions/turn | 1.3 | 0.0 |  |
| LLM errors | 0 | 2 |  |

**Verdict:** plain (pln won 0, plain won 2 of 6 tracked metrics).

> Caveat: one seed = a single matched pair — directional, not statistically conclusive. PLN reasoning here is one-hop (situation→priority) via two-premise NAL.
