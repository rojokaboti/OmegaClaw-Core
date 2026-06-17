"""Fixture for the LLM-logging privacy benchmark (Issue #3).

A single representative raw model response mixing normal prose with several
secret-looking strings. SECRETS is the list of substrings that must never leak
into default logs (and must never appear unredacted in debug logs).
"""

GH_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0" + "KLMNOP"
GH_PAT = "github_pat_" + "11ABCDEFG0" + "abcdefghijklmnopqrstuv"
OPENAI_KEY = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"
ANTHROPIC_KEY = "sk-ant-api03-" + "ZyXwVuTsRqPoNmLkJiHg"
BEARER_TOKEN = "abcDEF123456ghiJKL789"
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
B64_SECRET = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU2Nzg5"

SECRETS = [GH_TOKEN, GH_PAT, OPENAI_KEY, ANTHROPIC_KEY, BEARER_TOKEN, AWS_KEY, B64_SECRET]

NORMAL_TEXT = "Here is the requested summary of the weather and your account."

RESPONSE = (
    NORMAL_TEXT
    + " github=" + GH_TOKEN
    + " pat=" + GH_PAT
    + " openai=" + OPENAI_KEY
    + " anthropic=" + ANTHROPIC_KEY
    + " Authorization: Bearer " + BEARER_TOKEN
    + " aws=" + AWS_KEY
    + " blob=" + B64_SECRET
)
