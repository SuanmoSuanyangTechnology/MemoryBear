"""Analysis failures retain the legacy exception boundary."""


class LLMClientException(RuntimeError):
    pass
