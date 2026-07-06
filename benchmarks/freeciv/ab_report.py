"""Progress + final comparison for the PLN-vs-plain-LLM A/B run (Issue #25 experiment).

Reads both arms' JSONL (`<out>/{pln,plain}.jsonl`) + heartbeats. Default prints a 30-min progress
snapshot; `--final` writes `comparison.{md,json}` with trajectories, final metrics, and a verdict.

Stdlib only. Usage: python3 benchmarks/freeciv/ab_report.py <out_dir> [--final]
"""

import argparse
import calendar
import json
import os
import sys
import time

ARMS = ("pln", "plain")


def _load(out_dir, arm):
    path = os.path.join(out_dir, "%s.jsonl" % arm)
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


def _turn_rows(rows):
    return [r for r in rows if "metrics" in r]


def _heartbeat_age(out_dir, arm):
    path = os.path.join(out_dir, "%s.heartbeat" % arm)
    if not os.path.isfile(path):
        return None
    try:
        hb = json.load(open(path, encoding="utf-8"))
        t = calendar.timegm(time.strptime(hb["ts"], "%Y-%m-%d %H:%M:%S"))
        return int(time.time() - t)
    except Exception:  # noqa: BLE001
        return None


def _avg(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), 1) if xs else None


def _arm_stats(out_dir, arm):
    rows = _load(out_dir, arm)
    tr = _turn_rows(rows)
    last = tr[-1] if tr else {}
    lm = last.get("metrics", {}) if last else {}
    proposed = sum(r.get("proposed", 0) for r in tr)
    blocked = sum(r.get("blocked", 0) for r in tr)
    submitted = sum(r.get("submitted", 0) for r in tr)
    advanced = sum(1 for r in tr if r.get("advanced_to") is not None)
    return {
        "arm": arm,
        "turns_logged": len(tr),
        "turns_advanced": advanced,
        "last_turn": lm.get("turn"),
        "score": lm.get("score"), "gold": lm.get("gold"), "science": lm.get("science"),
        "n_cities": lm.get("n_cities"), "n_units": lm.get("n_units"), "n_techs": lm.get("n_techs"),
        "proposed": proposed, "submitted": submitted, "blocked": blocked,
        "illegal_rate": round(blocked / proposed, 3) if proposed else 0.0,
        "avg_llm_ms": _avg([r.get("llm_ms") for r in tr]),
        "avg_reason_ms": _avg([r.get("reason_ms") for r in tr]),
        "avg_conclusions": _avg([r.get("n_conclusions") for r in tr]),
        "llm_errors": sum(1 for r in tr if r.get("llm_error")),
        "reconnects": sum(1 for r in rows if r.get("event") == "reconnect"),
        "heartbeat_age_s": _heartbeat_age(out_dir, arm),
        "peak_score": max([m for m in (r.get("metrics", {}).get("score") for r in tr)
                           if isinstance(m, (int, float))] or [None]) if tr else None,
    }


def snapshot(out_dir):
    s = {a: _arm_stats(out_dir, a) for a in ARMS}
    lines = ["A/B progress — %s (UTC)" % time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
             "run dir: %s" % out_dir, ""]
    fields = [("last_turn", "turn"), ("turns_advanced", "advanced"), ("score", "score"),
              ("n_cities", "cities"), ("n_units", "units"), ("n_techs", "techs"),
              ("gold", "gold"), ("science", "sci"), ("illegal_rate", "illegal%"),
              ("avg_llm_ms", "llm_ms"), ("avg_reason_ms", "reason_ms"), ("avg_conclusions", "concl"),
              ("llm_errors", "llm_err"), ("reconnects", "reconn"), ("heartbeat_age_s", "hb_age_s")]
    hdr = "  %-14s %12s %12s" % ("metric", "pln", "plain")
    lines.append(hdr); lines.append("  " + "-" * 40)
    for key, label in fields:
        lines.append("  %-14s %12s %12s" % (label, s["pln"].get(key), s["plain"].get(key)))
    for a in ARMS:
        age = s[a]["heartbeat_age_s"]
        if age is None or age > 600:
            lines.append("  [warn] arm '%s' heartbeat stale (age=%ss) — may be stalled/down" % (a, age))
    return "\n".join(lines), s


def _trajectory(out_dir, arm):
    return [{"turn": r.get("advanced_to") or r.get("turn"), **r.get("metrics", {})}
            for r in _turn_rows(_load(out_dir, arm))]


def final(out_dir):
    s = {a: _arm_stats(out_dir, a) for a in ARMS}
    traj = {a: _trajectory(out_dir, a) for a in ARMS}

    def better(key, hi=True):
        pv, qv = s["pln"].get(key), s["plain"].get(key)
        if not isinstance(pv, (int, float)) or not isinstance(qv, (int, float)) or pv == qv:
            return "tie"
        if hi:
            return "pln" if pv > qv else "plain"
        return "pln" if pv < qv else "plain"

    verdict = {"final_score": better("score"), "peak_score": better("peak_score"),
               "cities": better("n_cities"), "techs": better("n_techs"),
               "turns_advanced": better("turns_advanced"),
               "illegal_rate": better("illegal_rate", hi=False)}
    wins = {"pln": 0, "plain": 0}
    for v in verdict.values():
        if v in wins:
            wins[v] += 1
    overall = "pln" if wins["pln"] > wins["plain"] else ("plain" if wins["plain"] > wins["pln"] else "tie")

    result = {"stats": s, "verdict": verdict, "verdict_wins": wins, "overall": overall,
              "trajectory": traj, "generated": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())}
    with open(os.path.join(out_dir, "comparison.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    rows = [("Final score", "score"), ("Peak score", "peak_score"), ("Cities", "n_cities"),
            ("Units", "n_units"), ("Techs", "n_techs"), ("Last turn", "last_turn"),
            ("Turns advanced", "turns_advanced"), ("Illegal-action rate", "illegal_rate"),
            ("Avg LLM ms", "avg_llm_ms"), ("Avg reason ms", "avg_reason_ms"),
            ("Avg PLN conclusions/turn", "avg_conclusions"), ("LLM errors", "llm_errors")]
    md = ["# FreeCiv A/B — PLN (OmegaClaw) vs plain-LLM", "",
          "Same model/provider/seed/validation; only the state representation differs "
          "(pln = plain facts + MeTTa/PLN-derived recommendations; plain = plain facts only).", "",
          "| Metric | pln | plain | winner |", "| --- | --- | --- | --- |"]
    for label, key in rows:
        winner = {"score": verdict["final_score"], "peak_score": verdict["peak_score"],
                  "n_cities": verdict["cities"], "n_techs": verdict["techs"],
                  "turns_advanced": verdict["turns_advanced"],
                  "illegal_rate": verdict["illegal_rate"]}.get(key, "")
        md.append("| %s | %s | %s | %s |" % (label, s["pln"].get(key), s["plain"].get(key), winner))
    md += ["", "**Verdict:** %s (pln won %d, plain won %d of %d tracked metrics)."
           % (overall, wins["pln"], wins["plain"], len(verdict)), "",
           "> Caveat: one seed = a single matched pair — directional, not statistically conclusive. "
           "PLN reasoning here is one-hop (situation→priority) via two-premise NAL.", ""]
    with open(os.path.join(out_dir, "comparison.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    return "\n".join(md)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--final", action="store_true")
    args = ap.parse_args()
    if not os.path.isdir(args.out_dir):
        print("run dir not found: %s" % args.out_dir, file=sys.stderr)
        return 2
    if args.final:
        print(final(args.out_dir))
    else:
        text, _ = snapshot(args.out_dir)
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
