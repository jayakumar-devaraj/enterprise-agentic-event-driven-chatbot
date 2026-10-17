"""HTTP Basic authentication (scrypt) and thread ownership binding (guardrail L1, ADR 0007)."""

import base64
import hashlib
import hmac
import secrets
import sys

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str, salt: bytes | None = None) -> str:
    """Return `scrypt$<salt_b64>$<hash_b64>` for use in AUTH_USERS."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def parse_users(raw: str) -> dict[str, str]:
    """Parse `user:scrypt$salt$hash,user2:...`; malformed entries fail startup loudly."""
    users: dict[str, str] = {}
    for entry in filter(None, (e.strip() for e in raw.split(","))):
        name, sep, encoded = entry.partition(":")
        if not sep or not name or not encoded.startswith("scrypt$") or encoded.count("$") != 2:
            raise ValueError("AUTH_USERS entries must look like user:scrypt$<salt>$<hash>")
        users[name] = encoded
    return users


def verify(users: dict[str, str], username: str, password: str) -> bool:
    """Constant-time check; unknown users still pay the scrypt cost to avoid a timing oracle."""
    encoded = users.get(username) or hash_password("decoy", salt=b"\0" * 16)
    _, salt_b64, hash_b64 = encoded.split("$")
    candidate = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=_N, r=_R, p=_P)
    return hmac.compare_digest(candidate, base64.b64decode(hash_b64)) and username in users


def owner_key(thread_id: str) -> str:
    """Redis key binding a thread to the user who opened it."""
    return f"thread_owner:{thread_id}"


if __name__ == "__main__":
    # Usage: python -m gateway.auth <username> <password>  → prints an AUTH_USERS entry.
    print(f"{sys.argv[1]}:{hash_password(sys.argv[2])}")
