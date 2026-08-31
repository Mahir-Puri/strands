"""A small module written the safe way. There is nothing to find here.

This case exists to measure false positives. A good security pipeline should
come back from this one empty. If it "finds" something, that is noise, and the
benchmark should punish it for that.
"""

import hashlib
import hmac
import secrets
import sqlite3


def hash_password(password: str, salt: bytes) -> str:
    # Salted, slow KDF. The right way to store a password.
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000).hex()


def new_salt() -> bytes:
    return secrets.token_bytes(16)


def verify_token(provided: str, expected: str) -> bool:
    # Constant-time compare so this cannot be timed.
    return hmac.compare_digest(provided, expected)


def find_user(conn: sqlite3.Connection, name: str):
    # Parameterised query. No string building anywhere near the SQL.
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM users WHERE name = ?", (name,))
    return cur.fetchone()
