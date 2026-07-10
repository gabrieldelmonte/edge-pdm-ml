"""Custom HTTP exception classes for typed error raising across the application."""

from __future__ import annotations

from fastapi import HTTPException


class NotFoundError(HTTPException):
    """Raised when a requested resource does not exist."""

    def __init__(self, detail: str = "Not found") -> None:
        """Initialize with HTTP 404 status.

        Args:
            detail: Human-readable error message sent in the response body.
        """
        super().__init__(status_code=404, detail=detail)


class UnauthorizedError(HTTPException):
    """Raised when a request lacks valid authentication credentials."""

    def __init__(self, detail: str = "Unauthorized") -> None:
        """Initialize with HTTP 401 status.

        Args:
            detail: Human-readable error message sent in the response body.
        """
        super().__init__(status_code=401, detail=detail)


class ChecksumMismatchError(HTTPException):
    """Raised when the supplied checksum does not match the computed digest."""

    def __init__(self, detail: str = "Checksum mismatch") -> None:
        """Initialize with HTTP 422 status.

        Args:
            detail: Human-readable error message sent in the response body.
        """
        super().__init__(status_code=422, detail=detail)


class InferencePipelineError(HTTPException):
    """Raised when the inference pipeline encounters an unrecoverable error."""

    def __init__(self, detail: str = "Inference pipeline error") -> None:
        """Initialize with HTTP 500 status.

        Args:
            detail: Human-readable error message sent in the response body.
        """
        super().__init__(status_code=500, detail=detail)


class ForbiddenError(HTTPException):
    """Raised when a user attempts an action they are not permitted to perform."""

    def __init__(self, detail: str = "Forbidden") -> None:
        """Initialize with HTTP 403 status.

        Args:
            detail: Human-readable error message sent in the response body.
        """
        super().__init__(status_code=403, detail=detail)


class ConflictError(HTTPException):
    """Raised when a resource already exists and cannot be created again."""

    def __init__(self, detail: str = "Conflict") -> None:
        """Initialize with HTTP 409 status.

        Args:
            detail: Human-readable error message sent in the response body.
        """
        super().__init__(status_code=409, detail=detail)


class BadRequestError(HTTPException):
    """Raised when a request is malformed or contains invalid parameters."""

    def __init__(self, detail: str = "Bad request") -> None:
        """Initialize with HTTP 400 status.

        Args:
            detail: Human-readable error message sent in the response body.
        """
        super().__init__(status_code=400, detail=detail)
