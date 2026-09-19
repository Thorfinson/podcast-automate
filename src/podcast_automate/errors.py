class AppError(Exception):
    """An actionable error that is safe to show in the CLI or manifest."""

    def __init__(self, message: str, *, code: str = "operation_failed",
                 status: str = "failed", details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
        # Small structured facts about the failure, such as a quota reset time; never raw provider output.
        self.details = details or {}
