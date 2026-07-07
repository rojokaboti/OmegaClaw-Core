"""Progress + mirror-pair verdict for the head-to-head PLN-vs-plain duel (Issue #25 follow-up).

Reads a duel base dir with two game subdirs g1 (PLN=slot 0) and g2 (PLN=slot 1). Prints each
game's live status + latest per-side territory, and the mirror aggregate: how many of the 2 games
PLN won. PLN winning BOTH is a real signal; splitting by physical start = position bias.

Usage: python3 benchmarks/freeciv/duel_report.py <duel_base_dir> [--final]
"""

import argparse
import json
import os
import sys
import time

GAMES = (("g1", 0), ("g2", 1))  # (subdir, pln_side)


def _load(path):
    rows = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
    return rows


def _territory_winner(s0, s1):
    a0 = (s0.get("alive") if s0 else None); a1 = (s1.get("alive") if s1 else None)
    if a0 and not a1:
        return "0"
    if a1 and not a0:
        return "1"
    m0 = (s0 or {}).get("metrics") or {}; m1 = (s1 or {}).get("metrics") or {}
    for k in ("n_cities", "n_units", "n_techs"):
        if (m0.get(k) or 0) != (m1.get(k) or 0):
            return "0" if (m0.get(k) or 0) > (m1.get(k) or 0) else "1"
    return "tie"


def _game_state(base, subdir, pln_side):
    d = os.path.join(base, subdir)
    summ = os.path.join(d, "duel_summary.json")
    rows = _load(os.path.join(d, "duel.jsonl"))
    turn_rows = [r for r in rows if "side0" in r]
    last = turn_rows[-1] if turn_rows else {}
    roles = {"0": ("pln" if pln_side == 0 else "plain"), "1": ("pln" if pln_side == 1 else "plain")}
    if os.path.isfile(summ):
        s = json.load(open(summ, encoding="utf-8"))
        winner_side, winner_arm, done = s.get("winner_side"), s.get("winner_arm"), True
        final = s.get("final", {})
        s0m, s1m = final.get("side0"), final.get("side1")
    else:
        winner_side = _territory_winner(last.get("side0"), last.get("side1")) if last else None
        winner_arm = roles.get(winner_side, "?") if winner_side else "(running)"
        done = False
        s0m = (last.get("side0") or {}).get("metrics")
        s1m = (last.get("side1") or {}).get("metrics")
    return {"subdir": subdir, "pln_side": pln_side, "roles": roles, "done": done,
            "last_turn": (last.get("advanced_to") or last.get("turn")),
            "winner_side": winner_side, "winner_arm": winner_arm,
            "pln_metrics": (s0m if pln_side == 0 else s1m),
            "plain_metrics": (s1m if pln_side == 0 else s0m),
            "turns_logged": len(turn_rows)}


def report(base, final=False):
    games = [_game_state(base, sd, ps) for sd, ps in GAMES]
    lines = ["Duel mirror-pair — %s (UTC)" % time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
             "base: %s" % base, ""]
    pln_wins = plain_wins = 0
    for g in games:
        wa = g["winner_arm"]
        if g["done"]:
            if wa == "pln":
                pln_wins += 1
            elif wa == "plain":
                plain_wins += 1
        lines.append("  %s (PLN=slot%d): turn %s, %s%s" % (
            g["subdir"], g["pln_side"], g["last_turn"],
            ("WINNER=%s" % wa) if g["done"] else ("leading=%s (in progress)" % wa),
            ""))
        lines.append("     PLN   %s" % (g["pln_metrics"] or "?"))
        lines.append("     plain %s" % (g["plain_metrics"] or "?"))
    lines.append("")
    both_done = all(g["done"] for g in games)
    if both_done:
        if pln_wins == 2:
            verdict = "PLN wins BOTH mirror games — a real signal PLN reasoning helped."
        elif plain_wins == 2:
            verdict = "plain wins BOTH — PLN reasoning did not help (may have hurt)."
        else:
            verdict = ("split (PLN %d / plain %d) — likely start-position bias or no effect; "
                       "inconclusive." % (pln_wins, plain_wins))
        lines.append("VERDICT: %s" % verdict)
    else:
        lines.append("VERDICT: (both games still running)")

    if final:
        out = {"base": base, "games": games, "pln_wins": pln_wins, "plain_wins": plain_wins,
               "both_done": both_done, "generated": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())}
        with open(os.path.join(base, "duel_comparison.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        with open(os.path.join(base, "duel_comparison.md"), "w", encoding="utf-8") as f:
            f.write("# FreeCiv duel — PLN vs plain-LLM (mirror pair)\n\n" + "\n".join(lines) + "\n")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--final", action="store_true")
    args = ap.parse_args()
    if not os.path.isdir(args.base):
        print("duel base not found: %s" % args.base, file=sys.stderr); return 2
    print(report(args.base, final=args.final))
    return 0


if __name__ == "__main__":
    sys.exit(main())
