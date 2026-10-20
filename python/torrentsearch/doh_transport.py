"""httpx transport that connects to DoH addresses while keeping TLS SNI."""

from __future__ import annotations

from typing import Iterable

import httpx
from httpcore._backends.auto import AutoBackend
from httpcore._backends.base import SOCKET_OPTION, AsyncNetworkStream

from .doh import aclose_doh, resolve


class _DohBackend(AutoBackend):
    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> AsyncNetworkStream:
        # TCP goes to the DoH IP. TLS SNI stays the original hostname
        # (httpcore start_tls uses the URL host, not this argument).
        target = await resolve(host)
        return await super().connect_tcp(
            target,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class DohTransport(httpx.AsyncBaseTransport):
    def __init__(self, **kwargs) -> None:
        self._inner = httpx.AsyncHTTPTransport(**kwargs)
        self._inner._pool._network_backend = _DohBackend()  # type: ignore[attr-defined]

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()
        await aclose_doh()
