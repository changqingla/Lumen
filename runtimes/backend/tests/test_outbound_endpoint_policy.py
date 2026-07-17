"""Runtime enforcement tests for user-configurable model endpoints."""

from __future__ import annotations

import asyncio
import socket

import httpcore
import httpx
import pytest

from src.utils import outbound_endpoint_policy as policy_module
from src.utils.outbound_endpoint_policy import (
    OUTBOUND_ENDPOINT_ERROR_MESSAGE,
    OutboundEndpointError,
    OutboundEndpointPolicy,
)

PUBLIC_ADDRESSES = ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946")


def _resolve_public(_host: str, _port: int) -> tuple[str, ...]:
    return PUBLIC_ADDRESSES


async def _resolve_public_async(_host: str, _port: int) -> tuple[str, ...]:
    return PUBLIC_ADDRESSES


class _RecordingStream(httpcore.NetworkStream):
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.server_hostnames: list[str | None] = []
        self._response_sent = False

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        if self._response_sent:
            return b""
        self._response_sent = True
        return b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.writes.append(buffer)

    def close(self) -> None:
        return None

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.server_hostnames.append(server_hostname)
        return self


class _RecordingBackend(httpcore.NetworkBackend):
    def __init__(self, stream: _RecordingStream | None = None) -> None:
        self.stream = stream or _RecordingStream()
        self.connections: list[tuple[str, int]] = []

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connections.append((host, port))
        return self.stream

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise AssertionError("Unix sockets must not be used")


class _RecordingAsyncBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.connections: list[tuple[str, int]] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connections.append((host, port))
        return object()

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise AssertionError("Unix sockets must not be used")

    async def sleep(self, seconds):
        return None


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/v1",
        "https://user:secret@example.com/v1",
        "https://example.com:99999/v1",
        "https://example.com:/v1",
        "https://example.com/v1?target=http://127.0.0.1",
        "https://example.com/v1#fragment",
        "https://example.com\\@127.0.0.1/v1",
        "https://127.0.0.1/v1",
        "https://10.0.0.1/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://224.0.0.1/v1",
        "https://192.0.2.1/v1",
        "https://[::1]/v1",
        "https://[fec0::1]/v1",
        "https://[::ffff:127.0.0.1]/v1",
        "https://[::ffff:8.8.8.8]/v1",
        "https://[2002:7f00:1::]/v1",
        "https://[2001:0000:4136:e378:8000:63bf:3fff:fdd2]/v1",
    ],
)
def test_policy_rejects_unsafe_or_invalid_urls(url):
    policy = OutboundEndpointPolicy(resolver=_resolve_public)

    with pytest.raises(OutboundEndpointError) as exc_info:
        policy.validate_url(url)

    assert str(exc_info.value) == OUTBOUND_ENDPOINT_ERROR_MESSAGE


def test_policy_rejects_hostname_if_any_dns_address_is_not_public():
    policy = OutboundEndpointPolicy(
        resolver=lambda _host, _port: ("93.184.216.34", "10.20.30.40"),
    )

    with pytest.raises(OutboundEndpointError):
        policy.validate_url("https://models.example.com/v1")


def test_policy_normalizes_public_endpoint_and_resolves_effective_port():
    resolved = []

    def resolver(host: str, port: int) -> tuple[str, ...]:
        resolved.append((host, port))
        return ("93.184.216.34",)

    policy = OutboundEndpointPolicy(resolver=resolver)

    assert policy.validate_url("HTTPS://Models.Example.COM:443/v1/") == "https://models.example.com:443/v1"
    assert resolved == [("models.example.com", 443)]


def test_dns_failure_uses_non_sensitive_error_message():
    def fail_resolution(_host: str, _port: int) -> tuple[str, ...]:
        raise socket.gaierror("internal resolver details for models.secret.example")

    policy = OutboundEndpointPolicy(resolver=fail_resolution)

    with pytest.raises(OutboundEndpointError) as exc_info:
        policy.validate_url("https://models.secret.example/v1")

    assert str(exc_info.value) == OUTBOUND_ENDPOINT_ERROR_MESSAGE
    assert "secret.example" not in str(exc_info.value)


def test_private_network_escape_hatch_requires_explicit_opt_in():
    policy = OutboundEndpointPolicy(
        allow_private_networks=True,
        resolver=lambda _host, _port: ("127.0.0.1", "10.20.30.40"),
    )

    assert policy.validate_url("http://localhost:8000/v1/") == "http://localhost:8000/v1"


def test_sync_client_revalidates_every_request_and_never_follows_redirects():
    addresses = [["93.184.216.34"]]
    transport_requests = []

    def resolver(_host: str, _port: int):
        return tuple(addresses[-1])

    policy = OutboundEndpointPolicy(resolver=resolver, async_resolver=_resolve_public_async)
    client, async_client = policy.build_http_clients()
    guarded_transport = client._transport
    guarded_async_transport = async_client._transport
    assert isinstance(guarded_transport._pool._network_backend, policy_module._PolicyNetworkBackend)
    assert isinstance(guarded_async_transport._pool._network_backend, policy_module._PolicyAsyncNetworkBackend)
    client._transport = httpx.MockTransport(lambda request: (transport_requests.append(request) or httpx.Response(302, headers={"Location": "http://127.0.0.1/metadata"}, request=request)))
    try:
        request = client.build_request("GET", "https://models.example.com/v1/chat/completions?stream=true")
        response = client.send(request, follow_redirects=True)
        assert response.status_code == 302
        assert len(transport_requests) == 1

        addresses.append(["127.0.0.1"])
        with pytest.raises(OutboundEndpointError):
            client.get("https://models.example.com/v1/chat/completions")
        assert len(transport_requests) == 1
        assert client.follow_redirects is False
    finally:
        client.close()
        asyncio.run(async_client.aclose())
        guarded_transport.close()
        asyncio.run(guarded_async_transport.aclose())


@pytest.mark.asyncio
async def test_async_client_revalidates_before_transport():
    addresses = [["93.184.216.34"]]

    async def resolver(_host: str, _port: int):
        return tuple(addresses[-1])

    policy = OutboundEndpointPolicy(resolver=_resolve_public, async_resolver=resolver)
    client, async_client = policy.build_http_clients()
    guarded_transport = client._transport
    guarded_async_transport = async_client._transport
    requests = []
    async_client._transport = httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(200, request=request))
    try:
        assert (await async_client.get("https://models.example.com/v1/models")).status_code == 200
        addresses.append(["169.254.169.254"])
        with pytest.raises(OutboundEndpointError):
            await async_client.get("https://models.example.com/v1/models")
        assert len(requests) == 1
    finally:
        client.close()
        await async_client.aclose()
        guarded_transport.close()
        await guarded_async_transport.aclose()


def test_guarded_transport_pins_connect_to_validated_ip_and_preserves_host_and_sni():
    policy = OutboundEndpointPolicy(
        resolver=lambda _host, _port: ("93.184.216.34",),
        async_resolver=_resolve_public_async,
    )
    client, async_client = policy.build_http_clients()
    backend = client._transport._pool._network_backend
    recording_backend = _RecordingBackend()
    backend._backend = recording_backend
    try:
        response = client.get("https://models.example.com/v1/models")

        assert response.status_code == 200
        assert recording_backend.connections == [("93.184.216.34", 443)]
        assert recording_backend.stream.server_hostnames == ["models.example.com"]
        wire_request = b"".join(recording_backend.stream.writes).lower()
        assert b"host: models.example.com\r\n" in wire_request
    finally:
        client.close()
        asyncio.run(async_client.aclose())


def test_dns_rebinding_between_request_check_and_connect_is_blocked():
    answers = iter(
        [
            ("93.184.216.34",),
            ("169.254.169.254",),
        ]
    )
    policy = OutboundEndpointPolicy(
        resolver=lambda _host, _port: next(answers),
        async_resolver=_resolve_public_async,
    )
    client, async_client = policy.build_http_clients()
    backend = client._transport._pool._network_backend
    recording_backend = _RecordingBackend()
    backend._backend = recording_backend
    try:
        with pytest.raises(OutboundEndpointError):
            client.get("https://models.example.com/v1/models")

        assert recording_backend.connections == []
    finally:
        client.close()
        asyncio.run(async_client.aclose())


@pytest.mark.asyncio
async def test_async_dns_rebinding_between_request_check_and_connect_is_blocked():
    answers = iter(
        [
            ("93.184.216.34",),
            ("10.20.30.40",),
        ]
    )

    async def resolver(_host: str, _port: int):
        return next(answers)

    policy = OutboundEndpointPolicy(resolver=_resolve_public, async_resolver=resolver)
    client, async_client = policy.build_http_clients()
    backend = async_client._transport._pool._network_backend
    recording_backend = _RecordingAsyncBackend()
    backend._backend = recording_backend
    try:
        with pytest.raises(OutboundEndpointError):
            await async_client.get("https://models.example.com/v1/models")

        assert recording_backend.connections == []
    finally:
        client.close()
        await async_client.aclose()


@pytest.mark.asyncio
async def test_async_network_backend_connects_to_validated_ip():
    policy = OutboundEndpointPolicy(
        resolver=_resolve_public,
        async_resolver=lambda _host, _port: _resolve_public_async(_host, _port),
    )
    recording_backend = _RecordingAsyncBackend()
    backend = policy_module._PolicyAsyncNetworkBackend(policy, recording_backend)

    result = await backend.connect_tcp("models.example.com", 443)

    assert result is not None
    assert recording_backend.connections == [("93.184.216.34", 443)]
