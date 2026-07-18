# FreeCiv A/B — PLN (OmegaClaw) vs plain-LLM

Same model/provider/seed/validation; only the state representation differs (pln = plain facts + MeTTa/PLN-derived recommendations; plain = plain facts only).

| Metric | pln | plain | winner |
| --- | --- | --- | --- |
| Final score | 0 | 0 | tie |
| Peak score | 0 | 0 | tie |
| Cities | 2 | 3 | plain |
| Units | 14 | 20 |  |
| Techs | 8 | 10 | plain |
| Last turn | 250 | 250 |  |
| Turns advanced | 250 | 250 | tie |
| Illegal-action rate | 0.0 | 0.0 | tie |
| Avg LLM ms | 4665.1 | 4394.5 |  |
| Avg reason ms | 537.9 | None |  |
| Avg PLN conclusions/turn | 1.6 | 0.0 |  |
| LLM errors | 0 | 1 |  |

**Verdict:** plain (pln won 0, plain won 2 of 6 tracked metrics).

> Caveat: one seed = a single matched pair — directional, not statistically conclusive. PLN reasoning here is one-hop (situation→priority) via two-premise NAL.
