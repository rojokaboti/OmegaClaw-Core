# FreeCiv A/B — PLN (OmegaClaw) vs plain-LLM

Same model/provider/seed/validation; only the state representation differs (pln = plain facts + MeTTa/PLN-derived recommendations; plain = plain facts only).

| Metric | pln | plain | winner |
| --- | --- | --- | --- |
| Final score | 0 | 0 | tie |
| Peak score | 0 | 0 | tie |
| Cities | 3 | 3 | tie |
| Units | 18 | 11 |  |
| Techs | 13 | 11 | pln |
| Last turn | 250 | 250 |  |
| Turns advanced | 250 | 250 | tie |
| Illegal-action rate | 0.0 | 0.0 | tie |
| Avg LLM ms | 4707.8 | 4275.7 |  |
| Avg reason ms | 547.4 | None |  |
| Avg PLN conclusions/turn | 0.8 | 0.0 |  |
| LLM errors | 0 | 0 |  |

**Verdict:** pln (pln won 1, plain won 0 of 6 tracked metrics).

> Caveat: one seed = a single matched pair — directional, not statistically conclusive. PLN reasoning here is one-hop (situation→priority) via two-premise NAL.
