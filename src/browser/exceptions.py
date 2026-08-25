class SourceError(Exception):
    def __init__(self, source, url, message):
        self.source = source
        self.url = url
        super().__init__(message)


class BlockedSourceError(SourceError):
    """Raised when a site presents captcha/anti-bot/access restriction.

    Policy: stop interacting with the source, record event, move on.
    """

    kind = "blocked"


class RateLimitedError(SourceError):
    kind = "rate_limited"


class UnreachableSourceError(SourceError):
    """Network-level failure reaching a source (e.g. host unreachable)."""

    kind = "unreachable"
