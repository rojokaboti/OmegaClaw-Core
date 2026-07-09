"""Progress + mirror-pair verdict for the head-to-head PLN-vs-plain duel (Issue #25 follow-up).

Reads a duel base dir with two game subdirs g1 (PLN=slot 0) and g2 (PLN=slot 1). Prints each
game's status + per-side territory, and the mirror aggregate: how many of the 2 games PLN won.
PLN winning BOTH is a real signal; splitting by physical start = position bias.

Final metrics are extracted robustly (last epoch, ignore plateau tail) via ``run_summary`` — see
that module for why naive last-line parsing produced the empty/`?` committed comparisons flagged
in the PR #44 review.

`--final` writes ``duel_comparison.{md,json}`` **and** a compact, committable ``duel_summary.json``
(the minimal input needed to regenerate the comparison). It reads raw ``duel.jsonl`` when present,
falls back to that committed summary, and **fails closed** (exit 2, no write) when neither is
available — so it can never overwrite a tracked comparison with empty output.

Usage: python3 benchmarks/freeciv/duel_report.py <duel_base_dir> [--final]
"""

import argparse
import json
import os
import sys
import time

import run_summary as rs

GAMES = (("g1", 0), ("g2", 1))  # (subdir, pln_side)


def _side(r, i):
    return r.get("side%d" % i)


def _side_summary(rows, side_idx):
    return rs.summarize_side(
        rows,
        turn_of=lambda r: r.get("turn"),
        metrics_of=lambda r, i=side_idx: (_side(r, i) or {}).get("metrics"),
        proposed_of=lambda r, i=side_idx: (_side(r, i) or {}).get("proposed"),
        nconc_of=lambda r, i=side_idx: (_side(r, i) or {}).get("n_conclusions"),
    )


def _game_summary(base, subdir, pln_side):
    """Robust per-game summary from raw duel.jsonl (or None if no turn data present)."""
    d = os.path.join(base, subdir)
    rows = [r for r in rs.load_jsonl(os.path.join(d, "duel.jsonl")) if "side0" in r]
    if not rows:
        return None
    pln_i, plain_i = (0, 1) if pln_side == 0 else (1, 0)
    pln = _side_summary(rows, pln_i)
    plain = _side_summary(rows, plain_i)
    winner = rs.territory_winner(pln and pln["final"], plain and plain["final"])
    return {"subdir": subdir, "pln_side": pln_side, "pln": pln, "plain": plain,
            "winner_arm": winner, "plateau_turn": (pln or plain or {}).get("plateau_turn")}


def _load_or_compute(base):
    """Prefer raw logs; fall back to the committed duel_comparison.json; else None (fail closed).

    duel_comparison.json carries the full per-game payload, so it doubles as the compact,
    committable regeneration input — no separate (gitignored) *_summary.json needed.
    """
    games = [_game_summary(base, sd, ps) for sd, ps in GAMES]
    if any(g is not None for g in games):
        return games, "raw"
    prior = os.path.join(base, "duel_comparison.json")
    if os.path.isfile(prior):
        return json.load(open(prior, encoding="utf-8")).get("games", []), "committed comparison"
    return None, None


def _fmt(m):
    return "%d cities / %d units / %d techs" % (m["n_cities"], m["n_units"], m["n_techs"]) if m else "?"


def _render(base, games, source):
    lines = ["Duel mirror-pair — %s (UTC)" % time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
             "base: %s   (source: %s)" % (base, source), ""]
    pln_wins = plain_wins = 0
    for g in games:
        if not g:
            continue
        w = g.get("winner_arm")
        if w == "pln":
            pln_wins += 1
        elif w == "plain":
            plain_wins += 1
        pln, plain = g.get("pln"), g.get("plain")
        lines.append("  %s (PLN=slot%s): plateau turn %s — winner=%s" % (
            g["subdir"], g["pln_side"], g.get("plateau_turn"), w or "?"))
        lines.append("     PLN   %s%s" % (
            _fmt(pln and pln["final"]),
            ("   [%.2f actions/turn, %.0f%% == recs]" % (pln["avg_proposed"], pln["pct_actions_eq_recs"])
             if pln and pln.get("avg_proposed") is not None else "")))
        lines.append("     plain %s" % _fmt(plain and plain["final"]))
    lines.append("")
    if pln_wins == 2:
        verdict = "PLN wins BOTH mirror games — a real signal PLN reasoning helped."
    elif plain_wins == 2:
        verdict = "plain wins BOTH — PLN reasoning did not help (may have hurt)."
    elif pln_wins == 1 and plain_wins == 1:
        verdict = "split 1-1 (PLN wins one slot, plain the other)."
    else:
        verdict = "PLN %d / plain %d of 2 games." % (pln_wins, plain_wins)
    lines.append("VERDICT: %s" % verdict)
    return "\n".join(lines), pln_wins, plain_wins


def report(base, final=False):
    games, source = _load_or_compute(base)
    if games is None:
        sys.stderr.write(
            "duel_report: no raw duel.jsonl and no committed duel_comparison.json under %s — "
            "refusing to produce a verdict (would be empty). Run against the dir that holds the "
            "run data.\n" % base)
        return None
    # Raw logs absent: the committed comparison IS the record. Reprint it byte-for-byte and NEVER
    # rewrite (even under --final) — regenerating from the committed JSON would only churn volatile
    # metadata (base path, source, timestamp) and dirty a clean checkout. Mirrors the A/B reporter.
    if source != "raw":
        md = os.path.join(base, "duel_comparison.md")
        if os.path.isfile(md):
            sys.stderr.write("duel_report: raw logs absent — reprinting committed "
                             "duel_comparison.md (not regenerating).\n")
            return open(md, encoding="utf-8").read()
        text, _, _ = _render(base, games, source)  # only JSON committed: display, do not write
        return text
    text, pln_wins, plain_wins = _render(base, games, source)
    if final:
        payload = {"base": base, "games": games, "pln_wins": pln_wins, "plain_wins": plain_wins,
                   "source": source, "generated": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())}
        with open(os.path.join(base, "duel_comparison.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        with open(os.path.join(base, "duel_comparison.md"), "w", encoding="utf-8") as f:
            f.write("# FreeCiv duel — PLN vs plain-LLM (mirror pair)\n\n" + text + "\n")
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--final", action="store_true")
    args = ap.parse_args()
    if not os.path.isdir(args.base):
        print("duel base not found: %s" % args.base, file=sys.stderr)
        return 2
    text = report(args.base, final=args.final)
    if text is None:
        return 2
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
