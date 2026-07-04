"""Fixtures for the channel-registry maintainability benchmark (Issue #9).

The issue's experiment: add a dummy channel and compare the edit cost. We capture the *exact*
edits each design requires to add a config-less ``echo`` channel:

- BASELINE (nested-if dispatch, the original repo): a new `if (== (commchannel) echo)` branch must be
  threaded into ALL THREE dispatchers (start / receive / send) in src/channels.metta.
- CANDIDATE (registry): one `register(Channel(...))` object; the dispatch functions are untouched.

The benchmark counts non-blank lines and `(commchannel)` conditionals in each snippet, and also
exercises the real registry to prove the candidate snippet actually works.
"""

# What adding `echo` costs in the OLD nested-if design (edits across the 3 dispatchers):
BASELINE_ADD_SNIPPET = """\
; initChannels (start):
(if (== (commchannel) echo)
    (py-call (echo.start_echo))
    <existing-else-chain>)
; receive:
(if (== (commchannel) echo)
    (py-call (echo.getLastMessage))
    <existing-else-chain>)
; send:
(if (== (commchannel) echo)
    (let $temp (cut) (py-call (echo.send_message $safemsg)))
    <existing-else-chain>)
"""

# What adding `echo` costs in the registry design (one object; no dispatch edits):
CANDIDATE_ADD_SNIPPET = """\
register(Channel("echo", _lazy("echo", "start_echo"), _lazy("echo", "getLastMessage"),
                 _lazy("echo", "send_message")))
"""


def _nonblank_lines(text):
    return [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith(";")]


def _conditionals(text):
    return text.count("(== (commchannel)")


if __name__ == "__main__":
    print("baseline: %d lines, %d conditionals" % (len(_nonblank_lines(BASELINE_ADD_SNIPPET)),
                                                    _conditionals(BASELINE_ADD_SNIPPET)))
    print("candidate: %d lines, %d conditionals" % (len(_nonblank_lines(CANDIDATE_ADD_SNIPPET)),
                                                     _conditionals(CANDIDATE_ADD_SNIPPET)))
