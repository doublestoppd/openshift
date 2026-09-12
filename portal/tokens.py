"""Invitation URL tokens.

The token is a long random opaque value that acts as a lightweight bearer
credential for one (staff, shift) invitation. Only its SHA-256 hash is
stored; the raw value exists transiently while building the SMS message and
is never logged.
"""

import hashlib
import secrets


def generate_token():
    return secrets.token_urlsafe(32)


def hash_token(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
