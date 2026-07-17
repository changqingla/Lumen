"""Security policy for outbound HTTP endpoints."""

from __future__ import annotations

import asyncio
import contextvars
import ipaddress
import logging
import socket
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpcore
import httpx


OUTBOUND_ENDPOINT_ERROR_MESSAGE = "Base URL 不符合安全要求"
_REDACT_HTTPX_QUERY = contextvars.ContextVar(
    "lumen_redact_httpx_query",
    default=False,
)


class _HttpxQueryRedactionFilter(logging.Filter):
    """Remove signed queries from HTTPX's built-in INFO request log."""

    lumen_httpx_query_redaction_filter = True

    def filter(self, record: logging.LogRecord) -> bool:
        if not _REDACT_HTTPX_QUERY.get() or not isinstance(record.args, tuple):
            return True
        record.args = tuple(
            str(argument).split("?", 1)[0]
            if isinstance(argument, httpx.URL) and argument.query
            else argument
            for argument in record.args
        )
        return True


def _install_httpx_query_redaction_filter() -> None:
    httpx_logger = logging.getLogger("httpx")
    if any(
        getattr(item, "lumen_httpx_query_redaction_filter", False)
        for item in httpx_logger.filters
    ):
        return
    httpx_logger.addFilter(_HttpxQueryRedactionFilter())


_install_httpx_query_redaction_filter()


class OutboundEndpointError(ValueError):
    """Raised when an outbound endpoint cannot be used safely."""

    def __init__(self) -> None:
        super().__init__(OUTBOUND_ENDPOINT_ERROR_MESSAGE)


AddressResolver = Callable[[str, int], Awaitable[Sequence[str]]]


async def _resolve_addresses(host: str, port: int) -> Sequence[str]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(
        host,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    return tuple(str(record[4][0]) for record in records)


class OutboundEndpointPolicy:
    """Validate and pin outbound HTTP requests to policy-approved addresses.

    DNS is checked both immediately before a request and in ``connect_tcp``.
    The connection-layer check pins the socket to the validated numeric IP,
    while HTTP and TLS continue using the original hostname for Host and SNI.
    """

    def __init__(
        self,
        *,
        allow_private_networks: bool = False,
        allow_query: bool = False,
        require_https: bool = False,
        dns_timeout_seconds: float = 5.0,
        resolver: AddressResolver | None = None,
    ) -> None:
        self.allow_private_networks = allow_private_networks
        self.allow_query = allow_query
        self.require_https = require_https
        self.dns_timeout_seconds = max(0.1, float(dns_timeout_seconds))
        self._resolver = resolver or _resolve_addresses

    async def validate_url(self, value: str) -> str:
        """Return a normalized URL after validating its syntax and addresses."""

        parsed, host, port = self._parse_url(value)
        await self._resolve_validated_addresses(host, port)
        return self._normalize_url(parsed, host, port)

    async def resolve_connection_addresses(
        self, host: str, port: int
    ) -> tuple[str, ...]:
        """Resolve and validate the numeric addresses used by ``connect_tcp``."""

        normalized_host, normalized_port = self._normalize_connection_target(host, port)
        return await self._resolve_validated_addresses(normalized_host, normalized_port)

    async def request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Issue a validated, IP-pinned request without redirects or env proxies."""

        validated_url = await self.validate_url(url)
        kwargs.pop("follow_redirects", None)

        redaction_token = (
            _REDACT_HTTPX_QUERY.set(True) if self.allow_query else None
        )
        try:
            # Application tests use small protocol-compatible fakes. Production
            # callers pass httpx.AsyncClient and always take the guarded transport.
            if not isinstance(client, httpx.AsyncClient):
                request_method = getattr(client, method.lower(), None)
                if request_method is not None:
                    return await request_method(
                        validated_url, follow_redirects=False, **kwargs
                    )
                return await client.request(
                    method, validated_url, follow_redirects=False, **kwargs
                )

            transport = self._build_guarded_transport()
            async with httpx.AsyncClient(
                transport=transport,
                timeout=client.timeout,
                follow_redirects=False,
                trust_env=False,
            ) as guarded_client:
                return await guarded_client.request(
                    method,
                    validated_url,
                    follow_redirects=False,
                    **kwargs,
                )
        finally:
            if redaction_token is not None:
                _REDACT_HTTPX_QUERY.reset(redaction_token)

    @asynccontextmanager
    async def stream(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> AsyncIterator[httpx.Response]:
        """Stream a validated request while keeping its guarded client open."""

        validated_url = await self.validate_url(url)
        kwargs.pop("follow_redirects", None)

        redaction_token = (
            _REDACT_HTTPX_QUERY.set(True) if self.allow_query else None
        )
        try:
            # Keep protocol-compatible fakes usable in service tests. Production
            # callers always pass httpx.AsyncClient and use the guarded transport.
            if not isinstance(client, httpx.AsyncClient):
                stream_method = getattr(client, "stream", None)
                if stream_method is not None:
                    async with stream_method(
                        method,
                        validated_url,
                        follow_redirects=False,
                        **kwargs,
                    ) as response:
                        yield response
                    return

                response = await self.request(client, method, validated_url, **kwargs)
                try:
                    yield response
                finally:
                    close_response = getattr(response, "aclose", None)
                    if close_response is not None:
                        await close_response()
                return

            transport = self._build_guarded_transport()
            async with httpx.AsyncClient(
                transport=transport,
                timeout=client.timeout,
                follow_redirects=False,
                trust_env=False,
            ) as guarded_client:
                async with guarded_client.stream(
                    method,
                    validated_url,
                    follow_redirects=False,
                    **kwargs,
                ) as response:
                    yield response
        finally:
            if redaction_token is not None:
                _REDACT_HTTPX_QUERY.reset(redaction_token)

    def _build_guarded_transport(self) -> httpx.AsyncHTTPTransport:
        """Create an HTTPX transport whose final TCP target is policy checked."""

        transport = httpx.AsyncHTTPTransport(trust_env=False)
        pool = getattr(transport, "_pool", None)
        network_backend = getattr(pool, "_network_backend", None)
        if not isinstance(network_backend, httpcore.AsyncNetworkBackend):
            raise RuntimeError(
                "Installed httpx/httpcore does not support guarded outbound transports"
            )
        pool._network_backend = _PolicyAsyncNetworkBackend(self, network_backend)
        return transport

    async def _resolve_validated_addresses(
        self, host: str, port: int
    ) -> tuple[str, ...]:
        literal_address = self._parse_ip_address(host)
        if literal_address is not None:
            self._ensure_address_allowed(literal_address)
            return (str(literal_address),)

        if self._is_localhost_name(host) and not self.allow_private_networks:
            raise OutboundEndpointError()

        try:
            resolved_addresses = await asyncio.wait_for(
                self._resolver(host, port),
                timeout=self.dns_timeout_seconds,
            )
        except Exception:
            raise OutboundEndpointError() from None

        if not resolved_addresses:
            raise OutboundEndpointError()

        validated: list[str] = []
        for raw_address in resolved_addresses:
            try:
                address = ipaddress.ip_address(str(raw_address).split("%", 1)[0])
            except ValueError:
                raise OutboundEndpointError() from None
            self._ensure_address_allowed(address)
            normalized = str(address)
            if normalized not in validated:
                validated.append(normalized)
        return tuple(validated)

    @staticmethod
    def _normalize_connection_target(host: str, port: int) -> tuple[str, int]:
        normalized_host = str(host or "").strip().rstrip(".").lower()
        if (
            not normalized_host
            or "/" in normalized_host
            or "\\" in normalized_host
            or "%" in normalized_host
            or any(
                character.isspace() or ord(character) < 32 or ord(character) == 127
                for character in normalized_host
            )
        ):
            raise OutboundEndpointError()
        try:
            normalized_port = int(port)
        except (TypeError, ValueError):
            raise OutboundEndpointError() from None
        if not 1 <= normalized_port <= 65535:
            raise OutboundEndpointError()
        try:
            normalized_host = normalized_host.encode("idna").decode("ascii")
        except UnicodeError:
            raise OutboundEndpointError() from None
        return normalized_host, normalized_port

    def _parse_url(self, value: str) -> tuple[SplitResult, str, int]:
        normalized = str(value or "").strip()
        if (
            not normalized
            or "\\" in normalized
            or any(
                character.isspace() or ord(character) < 32 or ord(character) == 127
                for character in normalized
            )
        ):
            raise OutboundEndpointError()

        try:
            parsed = urlsplit(normalized)
            port = parsed.port
        except ValueError:
            raise OutboundEndpointError() from None

        scheme = parsed.scheme.lower()
        host = str(parsed.hostname or "").rstrip(".").lower()
        if (
            scheme not in {"http", "https"}
            or (self.require_https and scheme != "https")
            or not parsed.netloc
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or (parsed.query and not self.allow_query)
            or parsed.fragment
            or "%" in host
        ):
            raise OutboundEndpointError()
        if port is not None and not 1 <= port <= 65535:
            raise OutboundEndpointError()
        if parsed.netloc.endswith(":"):
            raise OutboundEndpointError()

        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise OutboundEndpointError() from None

        return parsed, host, port or (443 if scheme == "https" else 80)

    def _normalize_url(
        self,
        parsed: SplitResult,
        host: str,
        resolved_port: int,
    ) -> str:
        scheme = parsed.scheme.lower()
        display_host = f"[{host}]" if ":" in host else host
        explicit_port = parsed.port
        netloc = display_host
        if explicit_port is not None:
            netloc = f"{display_host}:{resolved_port}"
        # Signed URLs cover the exact path and query. The opt-in query mode
        # therefore preserves both; base-URL callers retain legacy trimming.
        path = parsed.path if self.allow_query else parsed.path.rstrip("/")
        query = parsed.query if self.allow_query else ""
        return urlunsplit((scheme, netloc, path, query, ""))

    @staticmethod
    def _parse_ip_address(
        host: str,
    ) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
        try:
            return ipaddress.ip_address(host)
        except ValueError:
            return None

    @staticmethod
    def _is_localhost_name(host: str) -> bool:
        return host == "localhost" or host.endswith(".localhost")

    def _ensure_address_allowed(
        self,
        address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ) -> None:
        if isinstance(address, ipaddress.IPv6Address):
            # Transition formats embed IPv4 addresses and have inconsistent
            # routing behavior across proxies and operating systems.
            if (
                address.ipv4_mapped is not None
                or address.sixtofour is not None
                or address.teredo is not None
            ):
                raise OutboundEndpointError()

        if address.is_multicast or address.is_reserved or address.is_unspecified:
            raise OutboundEndpointError()
        if not self.allow_private_networks and (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or (isinstance(address, ipaddress.IPv6Address) and address.is_site_local)
            or not address.is_global
        ):
            raise OutboundEndpointError()


class _PolicyAsyncNetworkBackend(httpcore.AsyncNetworkBackend):
    """Pin TCP connects to addresses validated at connection time."""

    def __init__(
        self,
        policy: OutboundEndpointPolicy,
        backend: httpcore.AsyncNetworkBackend,
    ) -> None:
        self._policy = policy
        self._backend = backend

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = await self._policy.resolve_connection_addresses(host, port)
        deadline = None if timeout is None else time.monotonic() + timeout
        last_error: Exception | None = None

        for address in addresses:
            remaining_timeout = (
                None if deadline is None else max(0.0, deadline - time.monotonic())
            )
            try:
                return await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=remaining_timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:
                last_error = exc

        if last_error is not None:
            raise last_error
        raise OutboundEndpointError()

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options=None,
    ) -> httpcore.AsyncNetworkStream:
        raise OutboundEndpointError()

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)
