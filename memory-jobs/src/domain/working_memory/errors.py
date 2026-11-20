"""Working-memory generation failures interpreted by the application handler."""


class DetailFailure(ValueError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
