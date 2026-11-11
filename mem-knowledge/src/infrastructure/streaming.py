"""Close storage iterators even when sending to the HTTP consumer fails."""

from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send


class ClosingStreamingResponse(StreamingResponse):
    _prefetched_source = None

    @classmethod
    async def with_first_chunk(cls, source, **kwargs):
        iterator = aiter(source)
        try:
            first = await anext(iterator, b"")
        except BaseException:
            await iterator.aclose()
            raise

        async def body():
            yield first
            async for chunk in iterator:
                yield chunk

        response = cls(body(), **kwargs)
        response._prefetched_source = iterator
        return response

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                await self.body_iterator.aclose()
            finally:
                if self._prefetched_source is not None:
                    await self._prefetched_source.aclose()
