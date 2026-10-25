class ApiError(Exception):
    """An error the client should see as {"error": {"code", "message"}} with this HTTP status."""

    def __init__(self, status, code, message, headers=None):
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers
