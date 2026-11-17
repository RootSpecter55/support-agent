import hashlib
import hmac


def keys_match(given: str, expected: str) -> bool:
    """Constant-time comparison of two secrets of any length (both are hashed first)."""
    return hmac.compare_digest(
        hashlib.sha256(given.encode()).digest(), hashlib.sha256(expected.encode()).digest()
    )


def bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ")
    return None
