class SchoolServiceError(Exception):
    """Domain error for school flows (maps to HTTP 4xx in routes)."""

    def __init__(self, message: str, code: str = "bad_request", http_status: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
