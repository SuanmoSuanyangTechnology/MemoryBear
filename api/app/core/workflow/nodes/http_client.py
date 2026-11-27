import httpx

_client: httpx.AsyncClient | None = None
_sandbox_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("HTTP client not initialized")
    return _client


def get_sandbox_client() -> httpx.AsyncClient:
    """Dedicated client for the code-node sandbox (POST /v1/sandbox/run).

    Kept separate from ``get_http_client()`` on purpose. core/sandbox serves via
    actix with its default keep-alive (5s) and httpx's default
    ``keepalive_expiry`` is also 5s, so the two expire at the same instant with
    zero margin. A pooled connection reused inside that window can be handed out
    after the server already closed it; the request then fails with
    ``RemoteProtocolError: Server disconnected without sending a response``
    before ever reaching the sandbox handler, which the workflow reports as a
    generic internal error (code 10001) on whichever node happened to run late.

    Keep-alive is disabled here so each call takes a fresh connection and that
    race cannot occur. Sandbox calls are cluster-local and cost ~19ms, so the
    extra handshake is negligible; the limits only bound concurrency.
    """
    if _sandbox_client is None:
        raise RuntimeError("Sandbox HTTP client not initialized")
    return _sandbox_client


async def init_http_client() -> None:
    global _client, _sandbox_client
    _client = httpx.AsyncClient(
        timeout=60,
        limits=httpx.Limits(
            max_connections=300,
            max_keepalive_connections=25,
        ),
    )
    _sandbox_client = httpx.AsyncClient(
        timeout=60,
        limits=httpx.Limits(
            max_connections=300,
            max_keepalive_connections=0,
            keepalive_expiry=0.0,
        ),
    )


async def close_http_client() -> None:
    global _client, _sandbox_client
    if _client is not None:
        await _client.aclose()
        _client = None
    if _sandbox_client is not None:
        await _sandbox_client.aclose()
        _sandbox_client = None
