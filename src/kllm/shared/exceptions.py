"""Application errors shared by the CLI, services, and agent boundary."""


class KllmError(Exception):
    """Base class for expected application failures."""


class ValidationError(KllmError, ValueError):
    """Raised when a domain value does not satisfy its contract."""


class ConflictError(KllmError):
    """Raised when an operation conflicts with current application state."""


class AuthenticationError(KllmError):
    """Raised when credentials are missing or rejected."""


class NotFoundError(KllmError):
    """Raised when a requested domain resource does not exist."""


class ExternalServiceError(KllmError):
    """Raised when an external service cannot complete an operation."""


class ResourceUnavailableError(KllmError):
    """Raised when a requested local or remote resource is unavailable."""


class PersistenceError(KllmError):
    """Raised when durable application state cannot be read or written."""


class DownloadInProgressError(ConflictError):
    """Raised when a download conflicts with another active download."""


class DownloadCancelledError(KllmError):
    """Raised when an active model download is cancelled by its caller."""
