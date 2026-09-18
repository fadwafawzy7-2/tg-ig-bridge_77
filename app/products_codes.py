"""Stable human-readable product codes used in Instagram captions and lookup."""
from __future__ import annotations
import secrets
import string

_ALPHABET = string.ascii_uppercase
_CODE_LENGTH = 8
PREFIX = "PRD-"

def generate_product_code() -> str:
    return PREFIX + "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))
