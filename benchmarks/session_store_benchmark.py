"""KPI benchmark for Issue #16: session store vs the raw-log baseline.

Deterministic, host-runnable. Builds a synthetic corpus and drives the real
`src/session_store.py`.

* **baseline** = `asi-alliance`: only raw `history.metta` / trace logs — no session id index, no
  full-text search, no resume; finding prior decisions means manual log parsing (recall≈0,
  resume≈0 without bespoke tooling).
* **candidate** = SQLite store with FTS search + resumable snapshots + redaction.

KPIs measured over the corpus:
- search latency over a 1,000-session corpus < 500 ms;
- recall@5 >= 0.9 (each tagged session found by its unique keyword);
- resume success >= 0.8 over interrupted-task fixtures;
- 0 secret leaks in searchable/exported content.

Writes `session_store_results.{md,json}`; exits non-zero on gate failure.
Run: `python3 benchmarks/session_store_benchmark.py`
"""

import json
import os
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_SRC, _REPO_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import session_store as ss  # noqa: E402

_N = 1000
_SECRET = "sk-ant-api03-LEAKcanary000111222333444555"


def evaluate():
    dbp = os.path.join(tempfile.mkdtemp(prefix="ss_bench_"), "s.db")
    os.environ["OMEGACLAW_SESSION_DB"] = dbp
    ss.reset(dbp)
    conn = ss.connect(dbp)

    # 1) build a 1,000-session corpus; each session i carries a UNIQUE keyword "kw<i>"
    keywords = []
    for i in range(_N):
        sid = "sess-%04d" % i
        kw = "kw%04d" % i
        keywords.append((sid, kw))
        ss.begin_session(sid, provider="Test", channel="irc",
                         task="task {} about {} widgets".format(i, kw), conn=conn)
        ss.record_message(sid, 1, "user", "please handle {} for the {} project".format(kw, kw), conn=conn)
        ss.record_message(sid, 1, "assistant", "working on {}".format(kw), conn=conn)
        ss.record_tool_call(sid, 1, "shell", "run {}".format(kw), "done {}".format(kw), True, conn=conn)
        # interrupted subset gets a resumable snapshot
        if i % 2 == 0:
            ss.record_snapshot(sid, 1, {"task": kw, "step": "midway", "next": "finish {}".format(kw)}, conn=conn)
            ss.end_session(sid, "interrupted", conn=conn)
        else:
            ss.end_session(sid, "done", conn=conn)
    # one session with an embedded secret (leak canary)
    ss.begin_session("sess-secret", task="use the {} key".format(_SECRET), conn=conn)
    ss.record_message("sess-secret", 1, "assistant", "key is {} keep safe".format(_SECRET), conn=conn)
    ss.record_tool_call("sess-secret", 1, "shell", "export T={}".format(_SECRET), "ok", conn=conn)
    ss.end_session("sess-secret", "done", conn=conn)

    # 2) recall@5 + latency over a 200-keyword sample
    sample = keywords[::5]
    hits_at_5 = 0
    t0 = time.time()
    for sid, kw in sample:
        res = ss.search(kw, limit=5, conn=conn)
        if any(h["session_id"] == sid for h in res):
            hits_at_5 += 1
    elapsed = time.time() - t0
    recall_at_5 = hits_at_5 / len(sample)
    avg_latency_ms = (elapsed / len(sample)) * 1000.0

    # 3) resume success over the interrupted fixtures (sample 100)
    interrupted = [sid for sid, _ in keywords[::2]][:100]
    resumed_ok = 0
    for sid in interrupted:
        r = ss.resume(sid, conn=conn)
        if r["ok"] and r["latest_snapshot"] and r["latest_snapshot"].get("next") and r["recent_messages"]:
            resumed_ok += 1
    resume_rate = resumed_ok / len(interrupted)

    # 4) secret leakage across search + export of the canary session
    leak_blob = json.dumps(ss.export("sess-secret", conn=conn)) + json.dumps(ss.search("key", limit=10, conn=conn))
    secret_leaks = 1 if _SECRET in leak_blob else 0

    conn.close()
    os.environ.pop("OMEGACLAW_SESSION_DB", None)

    candidate = {
        "sessions": _N,
        "search_sample": len(sample),
        "recall_at_5": round(recall_at_5, 4),
        "avg_search_latency_ms": round(avg_latency_ms, 3),
        "resume_rate": round(resume_rate, 4),
        "secret_leaks": secret_leaks,
    }
    baseline = {
        "sessions": _N, "search_sample": len(sample), "recall_at_5": 0.0,
        "avg_search_latency_ms": None, "resume_rate": 0.0, "secret_leaks": 0,
    }
    return {"baseline": baseline, "candidate": candidate}


def render_md(s):
    b, c = s["baseline"], s["candidate"]
    rows = [
        ("Sessions indexed", "sessions"),
        ("recall@5 (target >= 0.90)", "recall_at_5"),
        ("Avg search latency ms over 1k corpus (target < 500)", "avg_search_latency_ms"),
        ("Resume success (target >= 0.80)", "resume_rate"),
        ("Secret leaks in search/export (target 0)", "secret_leaks"),
    ]
    lines = [
        "# Session-Store KPI Benchmark — Issue #16",
        "",
        "Synthetic corpus of **{} sessions** (each tagged with a unique keyword; half interrupted "
        "with a resumable snapshot) + 1 secret-canary session, through the real "
        "`src/session_store.py`.".format(c["sessions"]),
        "",
        "- **baseline** = raw `history.metta`/trace logs: no session index, no FTS, no resume.",
        "- **candidate** = SQLite + FTS5 search + resumable snapshots + redaction.",
        "",
        "| Metric | baseline | candidate |",
        "| --- | --- | --- |",
    ]
    for label, key in rows:
        lines.append("| {} | {} | {} |".format(label, b[key], c[key]))
    lines += [
        "",
        "Candidate indexes **{}** sessions, finds the right session at recall@5 **{:.2f}** in "
        "**{:.2f} ms** avg, resumes **{:.0%}** of interrupted fixtures, and leaks **{}** secrets — "
        "the baseline requires manual log parsing.".format(
            c["sessions"], c["recall_at_5"], c["avg_search_latency_ms"], c["resume_rate"], c["secret_leaks"]),
        "",
        "Reproduce: `python3 benchmarks/session_store_benchmark.py`",
        "",
    ]
    return "\n".join(lines)


def main():
    s = evaluate()
    with open(os.path.join(_HERE, "session_store_results.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    md = render_md(s)
    with open(os.path.join(_HERE, "session_store_results.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)

    c = s["candidate"]
    failures = []
    if c["recall_at_5"] < 0.90:
        failures.append("recall@5 {} < 0.90".format(c["recall_at_5"]))
    if c["avg_search_latency_ms"] is None or c["avg_search_latency_ms"] >= 500:
        failures.append("avg search latency {} ms >= 500".format(c["avg_search_latency_ms"]))
    if c["resume_rate"] < 0.80:
        failures.append("resume rate {} < 0.80".format(c["resume_rate"]))
    if c["secret_leaks"] != 0:
        failures.append("{} secret leaks".format(c["secret_leaks"]))
    if failures:
        print("\nKPI GATE: FAILED")
        for f in failures:
            print("  - " + f)
        sys.exit(1)
    print("\nKPI GATE: PASSED")


if __name__ == "__main__":
    main()
