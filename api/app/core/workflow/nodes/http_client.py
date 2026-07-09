import httpx

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("HTTP client not initialized")
    return _client


async def init_http_client() -> None:
    global _client
    _client = httpx.AsyncClient(
        timeout=60,
        limits=httpx.Limits(
            max_connections=300,
            max_keepalive_connections=25,
        ),
    )


async def close_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
