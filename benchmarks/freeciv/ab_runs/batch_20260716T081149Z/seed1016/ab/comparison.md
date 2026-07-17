# FreeCiv A/B — PLN (OmegaClaw) vs plain-LLM

Same model/provider/seed/validation; only the state representation differs (pln = plain facts + MeTTa/PLN-derived recommendations; plain = plain facts only).

| Metric | pln | plain | winner |
| --- | --- | --- | --- |
| Final score | 0 | 0 | tie |
| Peak score | 0 | 0 | tie |
| Cities | 0 | 3 | plain |
| Units | 0 | 14 |  |
| Techs | 7 | 11 | plain |
| Last turn | 235 | 250 |  |
| Turns advanced | 234 | 250 | plain |
| Illegal-action rate | 0.0 | 0.0 | tie |
| Avg LLM ms | 3834.3 | 4380.9 |  |
| Avg reason ms | 524.6 | None |  |
| Avg PLN conclusions/turn | 0.6 | 0.0 |  |
| LLM errors | 0 | 0 |  |

**Verdict:** plain (pln won 0, plain won 3 of 6 tracked metrics).

> Caveat: one seed = a single matched pair — directional, not statistically conclusive. PLN reasoning here is one-hop (situation→priority) via two-premise NAL.
