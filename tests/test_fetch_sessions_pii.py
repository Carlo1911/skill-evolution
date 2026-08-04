"""PII masking on the text that crosses the external-provider boundary.

The GEPA optimizer plan's Risk 1 flagged that session excerpts reaching an external
provider got only secret-pattern redaction, with no PII handling, and required explicit
sign-off before shipping. It shipped without one. Measured against the real session DB,
what actually crosses (first 300 chars of each message, messages with detected secrets
dropped wholesale) still carried: 71 messages with a Luhn-passing 13-19 digit run, 28 with
an email address, 27 with an international phone number, and 1 with a RUC.

Design, and the reason it differs from secret handling:

- A detected **secret** drops the whole message (`_summarize_messages` skips it). Correct
  for credentials: there is no version of that message worth sending.
- Detected **PII** is *masked in place*. An email in a paragraph of useful debugging
  evidence should cost that email, not the paragraph.
- **Money amounts are deliberately NOT masked.** They appear in 120 of the messages that
  cross, and `money-admin`-style skills are exactly what the analyzer needs to reason
  about; masking them would remove the evidence rather than protect an identity. The
  distinction is identifiers vs. amounts.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

from fetch_sessions import redact_pii

MASKED = [
    ("email", "escribe a juan.perez@example.com para confirmar"),
    ("email with plus", "usa carlo+test@sub.domain.pe si falla"),
    ("intl phone", "mi numero es +51 987 654 321 por si acaso"),
    ("intl phone dashes", "llama al +1-415-555-0132 manana"),
    # Luhn-valid, card-shaped (leading digit 3-6, standard length)
    ("visa-shaped", "la tarjeta 4111 1111 1111 1111 fue rechazada"),
    ("mastercard-shaped", "probamos con 5500005555555559 y fallo"),
    ("amex-shaped", "el amex 378282246310005 tampoco paso"),
    ("iban", "transferir a DE89370400440532013000 hoy"),
    ("ruc", "el RUC 20100070970 de la empresa"),
    ("dni", "su DNI 12345678 no coincide"),
]


@pytest.mark.parametrize("label,text", MASKED, ids=[m[0] for m in MASKED])
def test_identifiers_are_masked(label, text):
    out = redact_pii(text)
    assert out != text, f"{label} was not masked"
    assert "[PII" in out
    # the surrounding sentence survives -- masking, not dropping
    assert text.split()[0] in out


PRESERVED = [
    ("money PEN", "el gasto fue S/ 1,250.00 en comida"),
    ("money USD", "cobre $3,400.50 del cliente"),
    ("money bare EUR", "presupuesto EUR 990 aprobado"),
    ("token count", "MAX_TOKENS=1024 en la config"),
    ("version string", "actualizamos a la 3.12.2 sin problemas"),
    ("short number", "hay 42 sesiones pendientes"),
    ("timestamp-ish", "corrio a las 2026-07-26T02:02:29 con exito"),
    ("session id", "la sesion 20260714_130038_f8f8da fallo"),
    ("byte count", "el archivo pesa 102716 bytes"),
    ("prose about email", "revisa tu correo antes de responder"),
    ("hash-like non-luhn", "commit 1234567890123456 no aplica"),
]


@pytest.mark.parametrize("label,text", PRESERVED, ids=[p[0] for p in PRESERVED])
def test_amounts_and_ordinary_numbers_are_preserved(label, text):
    """Over-masking removes the evidence the analyzer reasons about."""
    assert redact_pii(text) == text, f"{label} was masked but carries no identifier"


def test_masking_preserves_surrounding_evidence():
    text = ("El usuario reporto que el deploy fallo tras cambiar la config. "
            "Contacto: ana.lopez@example.org. El error fue un timeout de 30s.")
    out = redact_pii(text)

    assert "ana.lopez@example.org" not in out
    assert "el deploy fallo tras cambiar la config" in out
    assert "timeout de 30s" in out


def test_mask_names_the_kind_of_pii():
    """A reviewer reading a proposal should know what was removed, not just that something was."""
    assert "email" in redact_pii("x j@e.com y").lower()
    assert "phone" in redact_pii("x +51 987 654 321 y").lower()


def test_multiple_occurrences_all_masked():
    out = redact_pii("a@b.com y luego c@d.org")
    assert "a@b.com" not in out and "c@d.org" not in out
    assert out.lower().count("pii") == 2


def test_empty_and_plain_text_unchanged():
    assert redact_pii("") == ""
    assert redact_pii("nada sensible aqui") == "nada sensible aqui"


def test_applied_to_the_text_that_crosses_the_boundary(tmp_path, monkeypatch):
    """End-to-end: _summarize_messages must mask, not just expose a helper."""
    import sqlite3
    import fetch_sessions

    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, model TEXT, title TEXT,"
        " started_at REAL);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,"
        " content TEXT, tool_calls TEXT, timestamp REAL);"
    )
    conn.execute("INSERT INTO sessions VALUES ('s1','cli','m','t', 9e9)")
    conn.execute(
        "INSERT INTO messages VALUES (1,'s1','user',?,NULL, 9e9)",
        ("Mi correo es dev@example.com y el gasto fue S/ 500.00",),
    )
    conn.commit()
    conn.close()

    # monkeypatch, not a bare assignment: STATE_FILE is a module constant, so assigning it
    # directly leaks into every later test in the session (it broke
    # test_state_schema_compat.py's check that both modules resolve the same path).
    monkeypatch.setattr(fetch_sessions, "STATE_FILE", str(tmp_path / "state.json"))
    sessions = fetch_sessions.fetch_sessions(db_path=str(db), lookback_hours=10**6, dry_run=True)

    blob = str(sessions)
    assert "dev@example.com" not in blob, "PII crossed the boundary"
    assert "S/ 500.00" in blob, "the amount was masked, removing analysable evidence"
