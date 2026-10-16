"""Lossless, letters-and-numbers printing format for existing ticket secrets.

The original 32-character Base64URL representation remains the database key.
The 39-character unpadded Base32 representation contains the same 24 bytes.
Neither conversion issues a ticket nor changes its check-in state.
"""

import base64
import re


def print_token(value):
    """Convert an original canonical 32-character token to 39 ASCII alphanumerics."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32}", value):
        raise ValueError("Expected a 32-character Base64URL ticket token")
    raw = base64.urlsafe_b64decode(value)
    if len(raw) != 24 or base64.urlsafe_b64encode(raw).decode("ascii") != value:
        raise ValueError("Ticket token is not canonical Base64URL")
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def canonical_token(value):
    """Recognize only canonical Base32 aliases; preserve all other input verbatim."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z2-7]{39}", value):
        return value
    raw = base64.b32decode(value + "=")
    if len(raw) != 24 or base64.b32encode(raw).decode("ascii").rstrip("=") != value:
        return value
    return base64.urlsafe_b64encode(raw).decode("ascii")
