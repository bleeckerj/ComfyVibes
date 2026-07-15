"""ComfyUI client errors."""


class ComfyClientError(RuntimeError):
    """Base error for ComfyClient with normalized remote failure metadata."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        endpoint: str | None = None,
        category: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.endpoint = endpoint
        self.category = category
