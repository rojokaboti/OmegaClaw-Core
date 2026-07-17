# FreeCiv A/B — PLN (OmegaClaw) vs plain-LLM

Same model/provider/seed/validation; only the state representation differs (pln = plain facts + MeTTa/PLN-derived recommendations; plain = plain facts only).

| Metric | pln | plain | winner |
| --- | --- | --- | --- |
| Final score | 0 | 0 | tie |
| Peak score | 0 | 0 | tie |
| Cities | 2 | 0 | pln |
| Units | 6 | 0 |  |
| Techs | 11 | 9 | pln |
| Last turn | 250 | 222 |  |
| Turns advanced | 250 | 221 | pln |
| Illegal-action rate | 0.0 | 0.0 | tie |
| Avg LLM ms | 4800.9 | 3790.5 |  |
| Avg reason ms | 540.2 | None |  |
| Avg PLN conclusions/turn | 1.1 | 0.0 |  |
| LLM errors | 1 | 0 |  |

**Verdict:** pln (pln won 3, plain won 0 of 6 tracked metrics).

> Caveat: one seed = a single matched pair — directional, not statistically conclusive. PLN reasoning here is one-hop (situation→priority) via two-premise NAL.
