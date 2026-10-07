"""Login passwords: stored only as scrypt hashes (Python's standard library, no extra package).

A password is never stored, only its hash: a fingerprint the gateway can check
a typed password against, but that can't be turned back into the password.

    scrypt:<N>:<r>:<p>:<salt, hex>:<hash, hex>

The random salt makes two equal passwords hash differently (no look-up
tables); N, r and p are scrypt's cost settings, kept in the string so they can
be raised later without breaking old hashes. ``scripts/hash_password.py``
makes the line for ``.env``.
"""

import hashlib
import hmac
import secrets

SCHEME = "scrypt"
# One of the scrypt settings in OWASP's Password Storage Cheat Sheet (N=2^14,
# r=8, p=5): 16 MiB of memory and 0.5-1 s of CPU per check (measured on our
# laptop, 2026-10-06) - slow on purpose, so a copied hash can't be guessed quickly.
N, R, P = 2**14, 8, 5
SALT_BYTES, KEY_BYTES = 16, 32
MAX_MEMORY = 64 * 1024 * 1024  # OpenSSL's default cap (32 MiB) is too close to 16 MiB x overhead
MIN_PASSWORD_LENGTH = 10


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                          maxmem=MAX_MEMORY, dklen=KEY_BYTES)


def hash_password(password: str, *, n: int = N, r: int = R, p: int = P) -> str:
    """The storable hash of a password, with a fresh random salt.

    Args:
        password: the password in plain text (never stored).
        n, r, p: scrypt cost settings; lower them only in tests.
    """
    salt = secrets.token_bytes(SALT_BYTES)
    return f"{SCHEME}:{n}:{r}:{p}:{salt.hex()}:{_scrypt(password, salt, n, r, p).hex()}"


def _parse(stored: str) -> tuple[int, int, int, bytes, bytes]:
    """Split a stored hash; ValueError if it isn't one we make."""
    scheme, n, r, p, salt, key = stored.strip().split(":")
    if scheme != SCHEME:
        raise ValueError(scheme)
    n_, r_, p_ = int(n), int(r), int(p)
    # Sane bounds: a typo must not make every login take minutes or gigabytes.
    if not (2**10 <= n_ <= 2**20 and n_ & (n_ - 1) == 0 and 1 <= r_ <= 32 and 1 <= p_ <= 16):
        raise ValueError("cost settings")
    salt_b, key_b = bytes.fromhex(salt), bytes.fromhex(key)
    if len(salt_b) < 16 or len(key_b) != KEY_BYTES:
        raise ValueError("lengths")
    return n_, r_, p_, salt_b, key_b


def hash_problem(stored: str) -> str | None:
    """Why this isn't a usable password hash, or None. Catches a plain password pasted into .env."""
    try:
        _parse(stored)
    except ValueError:
        return ("not a password hash made by scripts/hash_password.py (never put a plain password in .env). "
                "Make the line with:  python scripts/hash_password.py <user>")
    return None


def verify_password(password: str, stored: str) -> bool:
    """Does the typed password match the stored hash? Compared in constant time."""
    try:
        n, r, p, salt, key = _parse(stored)
    except ValueError:
        return False
    return hmac.compare_digest(_scrypt(password, salt, n, r, p), key)


def password_problem(username: str, password: str) -> str | None:
    """Why this password is too weak for a real login, or None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"use at least {MIN_PASSWORD_LENGTH} characters"
    if password.strip().lower() == username.lower():
        return "the password must not be the user name (that is the demo login)"
    return None
