# FreeCiv A/B — PLN (OmegaClaw) vs plain-LLM

Same model/provider/seed/validation; only the state representation differs (pln = plain facts + MeTTa/PLN-derived recommendations; plain = plain facts only).

| Metric | pln | plain | winner |
| --- | --- | --- | --- |
| Final score | 0 | 0 | tie |
| Peak score | 0 | 0 | tie |
| Cities | 1 | 0 | pln |
| Units | 9 | 0 |  |
| Techs | 10 | 6 | pln |
| Last turn | 250 | 196 |  |
| Turns advanced | 250 | 195 | pln |
| Illegal-action rate | 0.0 | 0.0 | tie |
| Avg LLM ms | 4008.3 | 4148.0 |  |
| Avg reason ms | 546.3 | None |  |
| Avg PLN conclusions/turn | 0.3 | 0.0 |  |
| LLM errors | 0 | 0 |  |

**Verdict:** pln (pln won 3, plain won 0 of 6 tracked metrics).

> Caveat: one seed = a single matched pair — directional, not statistically conclusive. PLN reasoning here is one-hop (situation→priority) via two-premise NAL.
