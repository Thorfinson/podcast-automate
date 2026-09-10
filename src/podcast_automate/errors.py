class AppError(Exception):
    """An actionable error that is safe to show in the CLI or manifest."""

    def __init__(self, message: str, *, code: str = "operation_failed",
                 status: str = "failed"):
        super().__init__(message)
        self.code = code
        self.status = status
