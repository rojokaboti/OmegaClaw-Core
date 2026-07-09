"""Robust run summarization for the FreeCiv A/B + duel reporters.

Extracts the *final* verdict metrics from a run's raw per-turn JSONL, handling two real-world
artifacts that naive "last line" parsing gets wrong (and which produced empty/`?` committed
comparisons — see the PR #44 review):

  - **Plateau tail:** when a game ends, the server stops advancing and the client logs stale
    reads with ``turn`` = 0/None and ``metrics`` = null. Those must be ignored; the final
    standing is the last record that actually carried metrics.
  - **Mid-game reset:** a server idle-timeout/reconnect can restart the game at turn 1 inside a
    single run (observed in ``duelfix`` g1). We split at the reset and summarize the last epoch,
    so peak/final reflect one coherent game rather than two concatenated.

Both a raw ``*.jsonl`` and the compact ``summary.json`` this module writes are enough to
regenerate the tracked ``comparison.*`` files; the reporters prefer raw logs, fall back to the
committed summary, and **fail closed** (no write) when neither is available — so ``--final`` can
never overwrite a tracked comparison with empty output.

Stdlib only.
"""

import json
import os

_METRIC_KEYS = ("n_cities", "n_units", "n_techs")


def load_jsonl(path):
    rows = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return rows


def _last_epoch(records, turn_of):
    """Return the records belonging to the last epoch (drop everything before a turn reset).

    A reset is a turn number that drops by >1 vs the previous record (server restarted the game
    at turn 1 mid-run). Only records with a real (truthy, >0) turn are considered for the split.
    """
    idx = [i for i, r in enumerate(records) if (turn_of(r) or 0) > 0]
    if not idx:
        return records
    start = idx[0]
    prev = turn_of(records[idx[0]])
    for i in idx[1:]:
        t = turn_of(records[i])
        if t < prev - 1:      # reset detected -> new epoch begins here
            start = i
        prev = t
    return records[start:]


def summarize_side(records, turn_of, metrics_of, proposed_of, nconc_of):
    """Summarize one competitor's series. Returns None if it never carried metrics.

    Uses the last epoch, ignores plateau-tail records with no metrics, and reports the last
    real standing (``final``), the ``peak`` over the epoch, ``plateau_turn`` (last real turn),
    and anchoring stats (avg actions/turn, % of turns where proposed == recommendations).
    """
    ep = _last_epoch(records, turn_of)
    # A usable record has BOTH a real turn (>0) and metrics: the plateau tail logs stale reads
    # with turn 0/None and either null OR all-zero metrics, which must never be the "final".
    withm = [r for r in ep if (turn_of(r) or 0) > 0 and metrics_of(r)]
    if not withm:
        return None
    last = metrics_of(withm[-1])
    peak = {k: max(int(metrics_of(r).get(k) or 0) for r in withm) for k in _METRIC_KEYS}
    proposed = [proposed_of(r) for r in withm if isinstance(proposed_of(r), (int, float))]
    nconc = [nconc_of(r) for r in withm if isinstance(nconc_of(r), (int, float))]
    eq = sum(1 for r in withm
             if isinstance(proposed_of(r), (int, float))
             and proposed_of(r) == nconc_of(r))
    n = len(withm)
    return {
        "final": {k: int(last.get(k) or 0) for k in _METRIC_KEYS},
        "peak": peak,
        "plateau_turn": turn_of(withm[-1]),
        "turns": n,
        "avg_proposed": round(sum(proposed) / len(proposed), 2) if proposed else None,
        "avg_conclusions": round(sum(nconc) / len(nconc), 2) if nconc else None,
        "pct_actions_eq_recs": round(100.0 * eq / n, 1) if n else None,
    }


def territory_winner(pln_final, plain_final):
    """cities > units > techs; None if either missing or a full tie."""
    if not pln_final or not plain_final:
        return None
    for k in _METRIC_KEYS:
        if (pln_final.get(k) or 0) != (plain_final.get(k) or 0):
            return "pln" if (pln_final[k] or 0) > (plain_final[k] or 0) else "plain"
    return "tie"
