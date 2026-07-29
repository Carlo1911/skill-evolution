"""Secret-detection tests for fetch_sessions.contains_secret().

Both redaction points route through this one predicate -- evaluate.redact_secrets()
(before a prompt leaves the machine) and proposal.save_proposal() (before a proposal
hits disk) -- so a false negative here leaks to a third-party provider.

The shapes below are not hypothetical: each was found by scanning the real
~/.hermes/state.db (26,794 message bodies), where the original fixed-substring
SECRET_PATTERNS list missed 27 password assignments, 23 generic *_TOKEN= assignments,
6 generic `sk-` keys, 5 bearer headers, and 1 credentialed database URI.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

from fetch_sessions import SECRET_PATTERNS, contains_secret

LEAKED_SHAPES = [
    # generic OpenAI-style key: the original list only had sk-ant-api / sk-or-v1-
    ("openai generic", "OPENAI key is sk-abcdefghijklmnopqrstuvwxyz012345"),
    ("openai project", "key: sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"),
    # arbitrary env-var assignments -- the list only named specific vars
    ("app token assign", "MYAPP_TOKEN=abcdefghijklmnopqrstuvwxyz"),
    ("secret assign", "SOME_SERVICE_SECRET=hunter2hunter2hunter2"),
    ("api key assign", "VENDOR_API_KEY=abcdef1234567890abcdef"),
    ("password assign", "password=sup3rs3cretvalue"),
    ("passwd colon", "passwd: sup3rs3cretvalue"),
    # auth headers copied out of curl/HTTP logs
    ("bearer header", "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123"),
    # credentialed connection strings beyond the literal DATABASE_URL marker
    ("postgres uri", "postgresql://admin:s3cretpass@db.example.com:5432/app"),
    ("mongodb srv uri", "mongodb+srv://user:p4ssw0rd@cluster0.example.net/db"),
    # other common vendor prefixes
    ("github fine-grained", "github_pat_11ABCDEFG0abcdefghijklmnopqrstuvwxyz"),
    ("gitlab pat", "glpat-abcdefghijklmnopqrst"),
    ("google api key", "AIzaSyA1234567890abcdefghijklmnopqrstuvw"),
    ("slack user token", "xoxp-1234567890-abcdefghijkl"),
    ("stripe live", "sk_live_abcdefghijklmnopqrstuvwx"),
    ("npm token", "npm_abcdefghijklmnopqrstuvwxyz0123456789"),
    ("hf token", "hf_abcdefghijklmnopqrstuvwxyz0123456789"),
    ("jwt", "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K"),
]


@pytest.mark.parametrize("label,text", LEAKED_SHAPES, ids=[s[0] for s in LEAKED_SHAPES])
def test_real_world_secret_shapes_are_detected(label, text):
    assert contains_secret(text), f"{label} would be sent to a provider verbatim"


PRESERVED = [
    ("token count setting", "MAX_TOKENS=1024"),
    ("prose about passwords", "The user asked how to reset their password on the site."),
    ("prose about tokens", "We should cap the token budget for this evaluator."),
    ("plain url", "See https://example.com/docs/api for the reference."),
    ("short assignment", "DEBUG=true"),
    ("markdown heading", "## Authorization and permissions"),
    ("code identifier", "api_key_field = form.get('api_key')"),
]


@pytest.mark.parametrize("label,text", PRESERVED, ids=[s[0] for s in PRESERVED])
def test_benign_lines_are_not_flagged(label, text):
    """Over-redaction destroys the session evidence the analyzer reasons about."""
    assert not contains_secret(text), f"{label} was redacted but carries no secret"


def test_original_fixed_markers_still_detected():
    """The pre-existing substring list must keep working."""
    for marker in ("sk-ant-api-xyz", "ghp_abc", "xoxb-123", "AKIA1234", "-----BEGIN RSA"):
        assert contains_secret(marker), marker


def test_detection_is_case_insensitive():
    assert contains_secret("PASSWORD=Sup3rS3cretValue")
    assert contains_secret("authorization: bearer abcdefghijklmnopqrstuvwxyz01")


def test_gemini_api_key_in_secret_patterns():
    """R7f: GEMINI_API_KEY is a known pattern, matching the already-present OPENAI_API_KEY.

    Gemini keys are opaque with no stable prefix, so it's the env-var *name*, not a
    value-shaped regex, that catches them here -- same posture as ANTHROPIC_API_KEY.
    """
    assert "GEMINI_API_KEY" in SECRET_PATTERNS


def test_gemini_api_key_assignment_is_detected():
    assert contains_secret("GEMINI_API_KEY=AIzaSyRealValueGoesHere1234567890")
