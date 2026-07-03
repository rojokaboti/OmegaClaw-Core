;Session-scoped reasoning state (Issue #8).
;
;Named reasoning sessions that accumulate premise expressions and replay them through the
;existing two-premise (|- a b) / (|~ a b) inference path. The default (metta ...) skill is
;unchanged; these sit alongside it. State lives in a process-local Python store
;(src/metta_sessions.py) reached via py-call; infer evaluates the assembled program through the
;same sread/eval path as (metta ...).

(= (metta-session-create $sid)
   (py-call (metta_sessions.create $sid)))

(= (metta-session-add $sid $expr)
   (py-call (metta_sessions.add_fact $sid $expr)))

(= (metta-session-clear $sid)
   (py-call (metta_sessions.clear $sid)))

(= (metta-session-snapshot $sid)
   (py-call (metta_sessions.snapshot $sid)))

;infer: pull the assembled (|- fact query) program for the session's stored premises, then
;evaluate it through the real inference path (same sread/eval/swrite/repr as the metta skill).
(= (metta-session-infer $sid $expr)
   (let $prog (py-call (metta_sessions.infer_program $sid $expr))
        (repr (swrite (eval (sread $prog))))))
