"""Runtime-side policy for user-configurable model endpoints.

Dynamic model credentials cross a service boundary before the provider SDK
uses them.  The business backend validates endpoints when it resolves a model
binding; this module repeats the security-sensitive checks in the process that
actually opens the connection.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import time
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpcore
import httpx

OUTBOUND_ENDPOINT_ERROR_MESSAGE = "Model endpoint does not meet outbound security requirements"


class OutboundEndpointError(ValueError):
    """Raised when an outbound model endpoint cannot be used safely."""

    def __init__(self) -> None:
        super().__init__(OUTBOUND_ENDPOINT_ERROR_MESSAGE)


class _NoRedirectClient(httpx.Client):
    def send(self, request: httpx.Request, *args, **kwargs) -> httpx.Response:
        kwargs["follow_redirects"] = False
        return super().send(request, *args, **kwargs)


class _NoRedirectAsyncClient(httpx.AsyncClient):
    async def send(self, request: httpx.Request, *args, **kwargs) -> httpx.Response:
        kwargs["follow_redirects"] = False
        return await super().send(request, *args, **kwargs)


AddressResolver = Callable[[str, int], Sequence[str]]
AsyncAddressResolver = Callable[[str, int], Awaitable[Sequence[str]]]


def _resolve_addresses(host: str, port: int) -> Sequence[str]:
    records = socket.getaddrinfo(
        host,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    return tuple(str(record[4][0]) for record in records)


async def _resolve_addresses_async(host: str, port: int) -> Sequence[str]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(
        host,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    return tuple(str(record[4][0]) for record in records)


_DNS_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="lumen-model-dns")


@dataclass(frozen=True)
class _ParsedEndpoint:
    parsed: SplitResult
    host: str
    port: int


class OutboundEndpointPolicy:
    """Validate model endpoints immediately before outbound requests.

    The checks intentionally mirror ``backend/utils/outbound_endpoint_policy``.
    Runtime has a separate deployment artifact, so it cannot import the backend
    module; keeping the policy in one Runtime module avoids scattering copies
    across individual model integrations.
    """

    def __init__(
        self,
        *,
        allow_private_networks: bool = False,
        dns_timeout_seconds: float = 5.0,
        resolver: AddressResolver | None = None,
        async_resolver: AsyncAddressResolver | None = None,
    ) -> None:
        self.allow_private_networks = bool(allow_private_networks)
        self.dns_timeout_seconds = max(0.1, float(dns_timeout_seconds))
        self._resolver = resolver or _resolve_addresses
        self._async_resolver = async_resolver or _resolve_addresses_async

    @classmethod
    def from_environment(cls) -> OutboundEndpointPolicy:
        """Build a policy using the same escape-hatch variables as backend."""

        allow_private = str(os.getenv("MODEL_PROVIDER_ALLOW_PRIVATE_ENDPOINTS", "false")).strip().lower()
        try:
            dns_timeout = float(os.getenv("MODEL_PROVIDER_DNS_TIMEOUT_SECONDS", "5.0"))
        except (TypeError, ValueError):
            dns_timeout = 5.0
        return cls(
            allow_private_networks=allow_private in {"1", "true", "yes", "on"},
            dns_timeout_seconds=dns_timeout,
        )

    def validate_url(self, value: str) -> str:
        """Validate and normalize a configured base URL synchronously."""

        endpoint = self._parse_url(value, allow_query=False)
        self._validated_addresses(endpoint.host, self._resolve_sync(endpoint.host, endpoint.port))
        return self._normalize_url(endpoint)

    async def avalidate_url(self, value: str) -> str:
        """Validate and normalize a configured base URL asynchronously."""

        endpoint = self._parse_url(value, allow_query=False)
        addresses = await self._resolve_async(endpoint.host, endpoint.port)
        self._validated_addresses(endpoint.host, addresses)
        return self._normalize_url(endpoint)

    def validate_request_url(self, value: str) -> None:
        """Validate an already-built request URL immediately before sending."""

        endpoint = self._parse_url(value, allow_query=True)
        self._validated_addresses(endpoint.host, self._resolve_sync(endpoint.host, endpoint.port))

    async def avalidate_request_url(self, value: str) -> None:
        """Async counterpart of :meth:`validate_request_url`."""

        endpoint = self._parse_url(value, allow_query=True)
        addresses = await self._resolve_async(endpoint.host, endpoint.port)
        self._validated_addresses(endpoint.host, addresses)

    def resolve_connection_addresses(self, host: str, port: int) -> tuple[str, ...]:
        """Resolve, validate, and return IPs that may be used for ``connect``."""

        normalized_host, normalized_port = self._normalize_connection_target(host, port)
        return self._validated_addresses(
            normalized_host,
            self._resolve_sync(normalized_host, normalized_port),
        )

    async def aresolve_connection_addresses(self, host: str, port: int) -> tuple[str, ...]:
        """Async counterpart of :meth:`resolve_connection_addresses`."""

        normalized_host, normalized_port = self._normalize_connection_target(host, port)
        addresses = await self._resolve_async(normalized_host, normalized_port)
        return self._validated_addresses(normalized_host, addresses)

    def build_http_clients(self) -> tuple[httpx.Client, httpx.AsyncClient]:
        """Create clients that revalidate every request and never redirect."""

        def _validate_request(request: httpx.Request) -> None:
            self.validate_request_url(str(request.url))

        async def _validate_async_request(request: httpx.Request) -> None:
            await self.avalidate_request_url(str(request.url))

        sync_transport = httpx.HTTPTransport(trust_env=False)
        async_transport = httpx.AsyncHTTPTransport(trust_env=False)
        sync_pool = getattr(sync_transport, "_pool", None)
        async_pool = getattr(async_transport, "_pool", None)
        sync_backend = getattr(sync_pool, "_network_backend", None)
        async_backend = getattr(async_pool, "_network_backend", None)
        if not isinstance(sync_backend, httpcore.NetworkBackend) or not isinstance(async_backend, httpcore.AsyncNetworkBackend):
            raise RuntimeError("Installed httpx/httpcore does not support guarded model transports")
        sync_pool._network_backend = _PolicyNetworkBackend(self, sync_backend)
        async_pool._network_backend = _PolicyAsyncNetworkBackend(self, async_backend)

        # ``trust_env=False`` prevents HTTP(S)_PROXY from routing a validated
        # public hostname to an unchecked internal destination. The custom
        # network backends pin each TCP connection to an address returned by the
        # final policy resolution, eliminating DNS validation/connect TOCTOU.
        return (
            _NoRedirectClient(
                follow_redirects=False,
                trust_env=False,
                event_hooks={"request": [_validate_request]},
                transport=sync_transport,
            ),
            _NoRedirectAsyncClient(
                follow_redirects=False,
                trust_env=False,
                event_hooks={"request": [_validate_async_request]},
                transport=async_transport,
            ),
        )

    def _resolve_sync(self, host: str, port: int) -> Sequence[str] | None:
        if self._parse_ip_address(host) is not None:
            return None
        if self._is_localhost_name(host) and not self.allow_private_networks:
            raise OutboundEndpointError()

        future = _DNS_EXECUTOR.submit(self._resolver, host, port)
        try:
            return future.result(timeout=self.dns_timeout_seconds)
        except (FutureTimeoutError, OSError, UnicodeError, ValueError):
            future.cancel()
            raise OutboundEndpointError() from None
        except Exception:
            future.cancel()
            raise OutboundEndpointError() from None

    async def _resolve_async(self, host: str, port: int) -> Sequence[str] | None:
        if self._parse_ip_address(host) is not None:
            return None
        if self._is_localhost_name(host) and not self.allow_private_networks:
            raise OutboundEndpointError()

        try:
            return await asyncio.wait_for(
                self._async_resolver(host, port),
                timeout=self.dns_timeout_seconds,
            )
        except (TimeoutError, OSError, UnicodeError, ValueError):
            raise OutboundEndpointError() from None
        except Exception:
            raise OutboundEndpointError() from None

    def _validated_addresses(
        self,
        host: str,
        resolved_addresses: Sequence[str] | None,
    ) -> tuple[str, ...]:
        literal_address = self._parse_ip_address(host)
        if literal_address is not None:
            self._ensure_address_allowed(literal_address)
            return (str(literal_address),)

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
        if not normalized_host or "/" in normalized_host or "\\" in normalized_host or "%" in normalized_host or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in normalized_host):
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

    @staticmethod
    def _parse_url(value: str, *, allow_query: bool) -> _ParsedEndpoint:
        normalized = str(value or "").strip()
        if not normalized or "\\" in normalized or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in normalized):
            raise OutboundEndpointError()

        try:
            parsed = urlsplit(normalized)
            explicit_port = parsed.port
        except ValueError:
            raise OutboundEndpointError() from None

        scheme = parsed.scheme.lower()
        host = str(parsed.hostname or "").rstrip(".").lower()
        if scheme not in {"http", "https"} or not parsed.netloc or not host or parsed.username is not None or parsed.password is not None or (parsed.query and not allow_query) or parsed.fragment or "%" in host:
            raise OutboundEndpointError()
        if explicit_port is not None and not 1 <= explicit_port <= 65535:
            raise OutboundEndpointError()
        if parsed.netloc.endswith(":"):
            raise OutboundEndpointError()

        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise OutboundEndpointError() from None

        return _ParsedEndpoint(
            parsed=parsed,
            host=host,
            port=explicit_port or (443 if scheme == "https" else 80),
        )

    @staticmethod
    def _normalize_url(endpoint: _ParsedEndpoint) -> str:
        scheme = endpoint.parsed.scheme.lower()
        display_host = f"[{endpoint.host}]" if ":" in endpoint.host else endpoint.host
        netloc = display_host
        if endpoint.parsed.port is not None:
            netloc = f"{display_host}:{endpoint.port}"
        path = endpoint.parsed.path.rstrip("/")
        return urlunsplit((scheme, netloc, path, "", ""))

    @staticmethod
    def _parse_ip_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
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
        if isinstance(address, ipaddress.IPv6Address) and (address.ipv4_mapped is not None or address.sixtofour is not None or address.teredo is not None):
            raise OutboundEndpointError()

        if address.is_multicast or address.is_reserved or address.is_unspecified:
            raise OutboundEndpointError()
        if not self.allow_private_networks and (address.is_private or address.is_loopback or address.is_link_local or (isinstance(address, ipaddress.IPv6Address) and address.is_site_local) or not address.is_global):
            raise OutboundEndpointError()


class _PolicyNetworkBackend(httpcore.NetworkBackend):
    """Pin sync TCP connections to addresses validated by the policy."""

    def __init__(self, policy: OutboundEndpointPolicy, backend: httpcore.NetworkBackend) -> None:
        self._policy = policy
        self._backend = backend

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ) -> httpcore.NetworkStream:
        addresses = self._policy.resolve_connection_addresses(host, port)
        deadline = None if timeout is None else time.monotonic() + timeout
        last_error: Exception | None = None
        for address in addresses:
            remaining_timeout = None if deadline is None else max(0.0, deadline - time.monotonic())
            try:
                return self._backend.connect_tcp(
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

    def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options=None):
        raise OutboundEndpointError()

    def sleep(self, seconds: float) -> None:
        self._backend.sleep(seconds)


class _PolicyAsyncNetworkBackend(httpcore.AsyncNetworkBackend):
    """Pin async TCP connections to addresses validated by the policy."""

    def __init__(self, policy: OutboundEndpointPolicy, backend: httpcore.AsyncNetworkBackend) -> None:
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
        addresses = await self._policy.aresolve_connection_addresses(host, port)
        deadline = None if timeout is None else time.monotonic() + timeout
        last_error: Exception | None = None
        for address in addresses:
            remaining_timeout = None if deadline is None else max(0.0, deadline - time.monotonic())
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

    async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options=None):
        raise OutboundEndpointError()

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)
