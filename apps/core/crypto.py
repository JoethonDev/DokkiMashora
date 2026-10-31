"""Small symmetric-encryption helper for credentials stored in the database.

The key is derived from Django's ``SECRET_KEY``. Rotating the secret key makes
existing encrypted values unreadable; the configuration UI then asks the
operator to re-enter the password instead of leaking a traceback.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(f"{settings.SECRET_KEY}:dokkimashora:credential:v1".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return ""
