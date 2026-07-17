# FreeCiv A/B — PLN (OmegaClaw) vs plain-LLM

Same model/provider/seed/validation; only the state representation differs (pln = plain facts + MeTTa/PLN-derived recommendations; plain = plain facts only).

| Metric | pln | plain | winner |
| --- | --- | --- | --- |
| Final score | 0 | None | tie |
| Peak score | 0 | None | tie |
| Cities | 3 | None | tie |
| Units | 30 | None |  |
| Techs | 14 | None | tie |
| Last turn | 250 | None |  |
| Turns advanced | 250 | 0 | pln |
| Illegal-action rate | 0.0 | 0.0 | tie |
| Avg LLM ms | 4000.5 | None |  |
| Avg reason ms | 554.6 | None |  |
| Avg PLN conclusions/turn | 0.3 | None |  |
| LLM errors | 2 | 0 |  |

**Verdict:** pln (pln won 1, plain won 0 of 6 tracked metrics).

> Caveat: one seed = a single matched pair — directional, not statistically conclusive. PLN reasoning here is one-hop (situation→priority) via two-premise NAL.
