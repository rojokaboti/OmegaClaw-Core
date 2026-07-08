"""Unit tests for the cron/webhook scheduler (Issue #17).

Pure-Python; imports src/scheduler.py directly against a temp jobs DB with an INJECTED clock
(deterministic — no real waiting). Runs under pytest and standalone.
"""
import hashlib
import hmac
import json
import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_REPO_ROOT, "src")
for _p in (_SRC, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scheduler as sch  # noqa: E402

T0 = 1_000_000.0


def _db():
    d = tempfile.mkdtemp(prefix="sch_")
    os.environ["OMEGACLAW_JOBS_DB"] = os.path.join(d, "jobs.db")
    os.environ["OMEGACLAW_SESSION_DB"] = os.path.join(d, "s.db")
    sch.reset(os.environ["OMEGACLAW_JOBS_DB"])


def _clean():
    for k in ("OMEGACLAW_JOBS_DB", "OMEGACLAW_SESSION_DB"):
        os.environ.pop(k, None)


def test_once_fires_once_then_done():
    _db()
    try:
        ran = []
        sch.create_job("o1", "once", str(T0 + 10), prompt="hi", now=T0)
        assert sch.run_due(now=T0 + 5, runner=lambda j, c: ran.append(j["id"]))["count"] == 0
        assert sch.run_due(now=T0 + 11, runner=lambda j, c: ran.append(j["id"]) or "out")["count"] == 1
        assert sch.get_job("o1")["next_run"] is None                # done
        assert sch.run_due(now=T0 + 999, runner=lambda j, c: "x")["count"] == 0
        assert ran == ["o1"]
    finally:
        _clean()


def test_interval_reschedules_and_cron_creates():
    _db()
    try:
        sch.create_job("iv", "interval", "60", now=T0)
        before = sch.get_job("iv")["next_run"]
        sch.run_due(now=before + 0.1, runner=lambda j, c: "tick")
        assert sch.get_job("iv")["next_run"] > before
        assert sch.create_job("cr", "cron", "*/5 * * * *", now=T0)["ok"]
        assert sch.create_job("bad", "cron", "not a cron", now=T0)["ok"] is False
    finally:
        _clean()


def test_unsafe_id_and_runaway_interval_rejected():
    _db()
    try:
        assert sch.create_job("../x", "once", str(T0))["ok"] is False
        assert sch.create_job("a/b", "once", str(T0))["ok"] is False
        assert sch.create_job("toofast", "interval", "0.1", now=T0)["ok"] is False
    finally:
        _clean()


def test_restart_recovery_no_lost_or_duplicate():
    _db()
    try:
        sch.create_job("survive", "once", str(T0), prompt="x", now=T0)
        # "restart" = a fresh run_due call on the same persisted DB, long overdue
        f1 = sch.run_due(now=T0 + 5000, runner=lambda j, c: "out")
        f2 = sch.run_due(now=T0 + 9000, runner=lambda j, c: "out")
        assert [f["id"] for f in f1["fired"]] == ["survive"]        # not lost
        assert f2["count"] == 0                                     # not duplicated
    finally:
        _clean()


def test_failure_alerts_and_empty_policy():
    _db()
    try:
        alerts = []

        def alert(job, msg):
            alerts.append(job["id"])

        sch.create_job("boom", "once", str(T0), prompt="boom", now=T0)
        sch.run_due(now=T0 + 1, runner=lambda j, c: (_ for _ in ()).throw(RuntimeError("x")),
                    alert_fn=alert)
        assert "boom" in alerts

        sch.create_job("silent", "once", str(T0), now=T0, on_empty="silent")
        sch.create_job("noisy", "once", str(T0), now=T0, on_empty="alert")
        a2 = []
        sch.run_due(now=T0 + 1, runner=lambda j, c: "", alert_fn=lambda j, m: a2.append(j["id"]))
        assert "noisy" in a2 and "silent" not in a2
    finally:
        _clean()


def test_delivery_only_on_nonempty_success():
    _db()
    try:
        delivered = []
        sch.create_job("d", "once", str(T0), prompt="p", delivery="chan", now=T0)
        sch.run_due(now=T0 + 1, runner=lambda j, c: "hello",
                    delivery_fn=lambda job, out: delivered.append((job["id"], out)))
        assert delivered == [("d", "hello")]
    finally:
        _clean()


def test_context_chaining():
    _db()
    try:
        sch.create_job("first", "once", str(T0), now=T0)
        sch.run_due(now=T0 + 1, runner=lambda j, c: "FIRST-OUTPUT")
        seen = {}
        sch.create_job("second", "once", str(T0 + 5), chain_from="first", now=T0)
        sch.run_due(now=T0 + 6, runner=lambda j, c: seen.setdefault("chain", c["chain_output"]) or "ok")
        assert seen["chain"] == "FIRST-OUTPUT"
    finally:
        _clean()


def test_recursion_guard():
    _db()
    try:
        sch.create_job("r", "once", str(T0), now=T0)

        def _runner(job, ctx):
            return "nested=" + str(sch.create_job("child", "once", str(T0), now=T0)["ok"])

        sch.run_due(now=T0 + 1, runner=_runner)
        assert "nested=False" in sch.get_job("r")["last_output"]
        assert sch.get_job("child") is None
    finally:
        _clean()


def test_cron_respects_all_five_fields():
    """Regression (PR #42 review): a `*` DOW must not short-circuit the minute/hour/dom/mon
    checks (daily/monthly specs were firing every minute)."""
    import calendar
    import time as _t

    def gm(s):
        return _t.gmtime(calendar.timegm(_t.strptime(s, "%Y-%m-%d %H:%M")))

    # 0 0 * * * must NOT match midday, and its next fire is the next midnight
    assert sch._cron_matches("0 0 * * *", gm("2026-07-08 12:34")) is False
    nxt = sch._cron_next("0 0 * * *", calendar.timegm(_t.strptime("2026-07-08 12:34", "%Y-%m-%d %H:%M")))
    assert _t.strftime("%Y-%m-%d %H:%M", _t.gmtime(nxt)) == "2026-07-09 00:00"
    # */5 from an off-minute → next 5-minute boundary
    off = calendar.timegm(_t.strptime("2026-07-08 12:34", "%Y-%m-%d %H:%M"))
    assert _t.strftime("%H:%M", _t.gmtime(sch._cron_next("*/5 * * * *", off))) == "12:35"
    # DOW-specific spec: matches only when minute/hour also match (2026-07-06 is a Monday)
    assert sch._cron_matches("0 9 * * 1", gm("2026-07-06 09:00")) is True
    assert sch._cron_matches("30 9 * * 1", gm("2026-07-06 09:00")) is False   # minute mismatch
    assert sch._cron_matches("0 9 * * 1", gm("2026-07-07 09:00")) is False    # Tuesday


def test_webhook_transient_id_never_deletes_durable_job():
    """Regression (PR #42 review): a webhook's transient job must not delete a pre-existing
    durable job even if the generated id collides."""
    import uuid
    _db()
    try:
        sch.webhook_subscribe("gh", "", {"prompt": "x"})
        fixed = "abcdef0123456789"

        class _U:
            hex = fixed
        orig = uuid.uuid4
        uuid.uuid4 = lambda: _U()
        try:
            sch.create_job("wh-gh-" + fixed, "once", "9999999999", prompt="DURABLE", now=0)
            r = sch.webhook_event("gh", {"e": "push"}, runner=lambda j, c: "ok")
        finally:
            uuid.uuid4 = orig
        assert r["ok"] is False and "already exists" in (r.get("error") or "")
        assert sch.get_job("wh-gh-" + fixed) is not None          # durable job survived
        assert sch.get_job("wh-gh-" + fixed)["prompt"] == "DURABLE"
    finally:
        _clean()


def test_webhook_signature_validation():
    _db()
    try:
        secret = "shh"
        sch.webhook_subscribe("gh", secret, {"prompt": "handle"})
        payload = {"event": "push", "n": 1}
        raw = json.dumps(payload, sort_keys=True).encode("utf-8")
        good = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        assert sch.webhook_event("gh", payload, good, runner=lambda j, c: "ok")["ok"]
        assert sch.webhook_event("gh", payload, "bad").get("rejected") is True
        assert sch.webhook_event("gh", payload, None).get("rejected") is True
        # unknown subscription
        assert sch.webhook_event("nope", payload, good)["ok"] is False
    finally:
        _clean()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok:", fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL:", fn.__name__, e)
    if failed:
        sys.exit(1)
    print(f"\nAll {len(fns)} scheduler tests passed")
