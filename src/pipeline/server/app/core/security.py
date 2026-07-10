"""Password hashing and verification using PBKDF2-HMAC-SHA256."""

from __future__ import annotations

import hashlib
import hmac
import os


def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256 with a random salt.

    Args:
        password: Plaintext password to hash.

    Returns:
        A string in the form ``<hex_salt>$<hex_digest>``.
    """
    salt = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100_000,
    ).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Validate a plaintext password against a stored salted hash.

    Args:
        password: Plaintext candidate password.
        stored_hash: Previously stored hash in the form ``<salt>$<digest>``.

    Returns:
        True when the password matches the stored hash, False otherwise.
    """
    try:
        salt, expected = stored_hash.split("$", maxsplit=1)
    except ValueError:
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100_000,
    ).hex()
    return hmac.compare_digest(actual, expected)
