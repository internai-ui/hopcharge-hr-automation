"""
crypto.py — Encryption helpers for the 5 sensitive employee columns.

Strategy (Option A you chose): encrypt at rest IN Postgres using pgcrypto's
pgp_sym_encrypt / pgp_sym_decrypt. The plaintext never sits in a Postgres
column — only the bytea ciphertext does. The symmetric key is supplied by the
app at query time from the EMPLOYEE_FIELD_KEY environment variable, so the key
lives in your process environment, not in the database.

Why pgcrypto rather than app-side Fernet:
  * The DB only ever stores ciphertext; a leaked DB dump reveals nothing.
  * Decryption happens inside the SQL query, so the app code stays simple.
  * One key to manage, rotateable with a single UPDATE … pgp_sym_encrypt(
        pgp_sym_decrypt(col, old_key), new_key).

Sensitive columns: pan, aadhaar, uan, account_number, salary_ctc.

IMPORTANT: if EMPLOYEE_FIELD_KEY is lost, encrypted employee data is
unrecoverable. Store it in a password manager / secrets vault. Back it up
separately from the database.
"""
from __future__ import annotations

import os

from sqlalchemy import text

SENSITIVE_FIELDS = ("pan", "aadhaar", "uan", "account_number", "salary_ctc")


def field_key() -> str:
    key = os.environ.get("EMPLOYEE_FIELD_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "EMPLOYEE_FIELD_KEY is not set. Set it to a long random passphrase "
            "before reading or writing employee sensitive fields. If you lose "
            "this key the encrypted columns cannot be decrypted."
        )
    return key


def encrypt_expr(bind_param_name: str):
    """Return a SQL text fragment that encrypts a bound plaintext value.
    Used in INSERT/UPDATE: VALUES (..., pgp_sym_encrypt(:pan, :_key), ...)."""
    return text(f"pgp_sym_encrypt(:{bind_param_name}, :_key)")


def encrypt_value(session, plaintext: str | None) -> bytes | None:
    """Encrypt a single value via a round-trip to Postgres. Returns bytea
    bytes suitable for assigning directly to a *_enc model column.
    None / empty in → None out (so empty fields stay NULL, not encrypted '')."""
    if plaintext is None or plaintext == "":
        return None
    row = session.execute(
        text("SELECT pgp_sym_encrypt(:val, :_key) AS enc"),
        {"val": str(plaintext), "_key": field_key()},
    ).first()
    return row.enc if row else None


def decrypt_value(session, ciphertext: bytes | None) -> str | None:
    """Decrypt a bytea value back to plaintext. None in → None out."""
    if ciphertext is None:
        return None
    row = session.execute(
        text("SELECT pgp_sym_decrypt(:val, :_key) AS dec"),
        {"val": ciphertext, "_key": field_key()},
    ).first()
    return row.dec if row else None


def encrypt_employee_sensitive(session, data: dict) -> dict:
    """Given a plaintext employee dict, return a dict mapping the *_enc columns
    to encrypted bytea, ready to set on an Employee model. The plaintext keys
    (pan, aadhaar, ...) are consumed; only *_enc keys are returned."""
    out = {}
    for f in SENSITIVE_FIELDS:
        out[f"{f}_enc"] = encrypt_value(session, data.get(f))
    return out


def decrypt_employee_sensitive(session, emp) -> dict:
    """Given an Employee model row, return {pan: ..., aadhaar: ..., ...} of
    decrypted plaintext. Used when the app needs to reveal sensitive fields."""
    out = {}
    for f in SENSITIVE_FIELDS:
        out[f] = decrypt_value(session, getattr(emp, f"{f}_enc"))
    return out


def mask(value: str | None) -> str:
    """UI-side last-4 masking, mirrors the existing employee_db.py behaviour."""
    if not value:
        return ""
    v = str(value)
    if len(v) <= 4:
        return "•" * len(v)
    return "•" * (len(v) - 4) + v[-4:]
