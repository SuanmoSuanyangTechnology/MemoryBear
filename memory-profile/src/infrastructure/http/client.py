import httpx

from src.config import settings


class HttpClient:
    def __init__(self):
        limits = httpx.Limits(
            max_connections=settings.HTTP_MAX_CONNECTIONS,
            max_keepalive_connections=settings.HTTP_KEEPALIVE_CONNECTIONS,
            keepalive_expiry=settings.HTTP_KEEPALIVE_EXPIRY,
        )
        self.client = httpx.AsyncClient(
            limits=limits,
            http2=True,
        )
        self.ssrf_client = httpx.AsyncClient(
            limits=limits,
            http2=True,
            proxy=settings.HTTP_SSRF_PROXY,
        )

    def get_client(self, ssrf: bool):
        return self.ssrf_client if ssrf else self.client

    async def close(self):
        await self.client.aclose()
        await self.ssrf_client.aclose()

    async def get(self, *args, ssrf: bool = True, **kwargs) -> httpx.Response:
        return await self.get_client(ssrf).get(*args, **kwargs)

    async def post(self, *args, ssrf: bool = True, **kwargs) -> httpx.Response:
        return await self.get_client(ssrf).post(*args, **kwargs)

    async def put(self, *args, ssrf: bool = True, **kwargs) -> httpx.Response:
        return await self.get_client(ssrf).put(*args, **kwargs)

    async def delete(self, *args, ssrf: bool = True, **kwargs) -> httpx.Response:
        return await self.get_client(ssrf).delete(*args, **kwargs)

    async def head(self, *args, ssrf: bool = True, **kwargs) -> httpx.Response:
        return await self.get_client(ssrf).head(*args, **kwargs)


_client: HttpClient | None = None


async def init_http_client():
    global _client
    if _client is not None:
        return
    _client = HttpClient()


def get_http_client():
    if _client is not None:
        return _client
    raise ValueError("Client not initialized")


async def close_http_client():
    global _client
    if _client is not None:
        await _client.close()
