# PLN-vs-LLM batch — statistical aggregate

batch: /home/rojo-dev/Repos/OmegaClaw-Core/benchmarks/freeciv/ab_runs/batch_20260716T081149Z
seeds scanned: 20

## duel (mirror slots, PLN vs plain)
games: 40  |  PLN wins: 21, plain wins: 19, ties: 0  |  sign-test p=0.8746

| metric | mean Δ (pln−plain) | n | t | p≈ |
|---|---|---|---|---|
| cities | -0.2 | 40 | -1.749 | 0.0803 |
| units | -1.25 | 40 | -1.164 | 0.2442 |
| techs | -0.675 | 40 | -0.865 | 0.3872 |

## A/B (PLN vs plain, each vs AI)
games: 19  |  PLN wins: 9, plain wins: 10, ties: 0  |  sign-test p=1.0

| metric | mean Δ (pln−plain) | n | t | p≈ |
|---|---|---|---|---|
| cities | -0.263 | 19 | -0.96 | 0.3369 |
| units | -1.474 | 19 | -0.539 | 0.59 |
| techs | -0.105 | 19 | -0.138 | 0.8899 |

_Sign-test p: exact two-sided binomial over decisive games. Metric p≈: normal approximation to the paired-t two-sided p (use with care for small n)._
