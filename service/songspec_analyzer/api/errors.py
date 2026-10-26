class ApiError(Exception):
    """An error the client should see as {"error": {"code", "message"}} with this HTTP status."""

    def __init__(self, status, code, message, headers=None, extra=None):
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers
        self.extra = extra or {}               # more fields inside the error object (e.g. the track a link resolved to)
