"""LMS service layer exceptions."""


class LMSError(Exception):
    """Base LMS error."""


class LMSNotFoundError(LMSError):
    """Resource not found."""


class LMSValidationError(LMSError):
    """Invalid input or business rule violation."""


class LMSPermissionError(LMSError):
    """Caller lacks permission for the operation."""
