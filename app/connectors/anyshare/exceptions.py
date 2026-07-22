"""AnyShare API error classification.

All errors are mapped to one of these types so retry logic can
decide whether to retry, skip, or dead-letter.
"""


class AnyShareError(Exception):
    """Base exception for AnyShare connector."""


class AuthError(AnyShareError):
    """401 — token expired or invalid. Retry once after refresh."""


class AccessLost(AnyShareError):
    """403 — caller lost access to the object. Do NOT retry."""


class NotFound(AnyShareError):
    """404 — source file missing. Flag as candidate missing."""


class RateLimited(AnyShareError):
    """429 — rate limit hit. Retry after Retry-After seconds."""


class ServerError(AnyShareError):
    """5xx — server-side error. Retry with exponential backoff."""


class NetworkError(AnyShareError):
    """Connection timeout / DNS failure. Retry with backoff + jitter."""


class SignatureExpired(AnyShareError):
    """Pre-signed download URL expired. Re-fetch from osdownload."""


def classify_status(status_code: int) -> type[AnyShareError]:
    """Map HTTP status code to the appropriate error class."""
    _map = {
        401: AuthError,
        403: AccessLost,
        404: NotFound,
        429: RateLimited,
    }
    if status_code in _map:
        return _map[status_code]
    if 500 <= status_code < 600:
        return ServerError
    return AnyShareError
