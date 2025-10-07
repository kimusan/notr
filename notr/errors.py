class NotrError(Exception):
    """Base application exception."""


class AuthenticationError(NotrError):
    """Raised when master password authentication fails."""


class BackendError(NotrError):
    """Raised when backend operations fail."""
