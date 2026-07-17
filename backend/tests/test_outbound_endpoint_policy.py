import socket
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpcore
import httpx
import pytest

from utils import outbound_endpoint_policy as policy_module
from utils.outbound_endpoint_policy import (
    OUTBOUND_ENDPOINT_ERROR_MESSAGE,
    OutboundEndpointError,
    OutboundEndpointPolicy,
)


async def resolve_public(_host: str, _port: int) -> tuple[str, ...]:
    return ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946")


class _RecordingAsyncStream(httpcore.AsyncNetworkStream):
    def __init__(self, response: bytes | None = None) -> None:
        self.writes: list[bytes] = []
        self.server_hostnames: list[str | None] = []
        self.closed = False
        self._response = response or (
            b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        )

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        response, self._response = self._response, b""
        return response

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.writes.append(buffer)

    async def aclose(self) -> None:
        self.closed = True

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.server_hostnames.append(server_hostname)
        return self

    def get_extra_info(self, info: str):
        return None


class _RecordingAsyncBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, stream: _RecordingAsyncStream | None = None) -> None:
        self.stream = stream or _RecordingAsyncStream()
        self.connections: list[tuple[str, int]] = []

    async def connect_tcp(
        self,
        host,
        port,
        timeout=None,
        local_address=None,
        socket_options=None,
    ):
        self.connections.append((host, port))
        return self.stream

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise AssertionError("Unix sockets must not be used")

    async def sleep(self, seconds):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/v1",
        "https://user:secret@example.com/v1",
        "https://example.com/v1?token=secret",
        "https://example.com/v1#fragment",
        "https://example.com:99999/v1",
        "https://example.com:/v1",
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
    ],
)
async def test_policy_rejects_unsafe_or_invalid_urls(url):
    policy = OutboundEndpointPolicy(resolver=resolve_public)

    with pytest.raises(OutboundEndpointError) as exc_info:
        await policy.validate_url(url)

    assert str(exc_info.value) == OUTBOUND_ENDPOINT_ERROR_MESSAGE


@pytest.mark.asyncio
async def test_policy_rejects_localhost_names_without_resolving():
    resolver = AsyncMock(return_value=("127.0.0.1",))
    policy = OutboundEndpointPolicy(resolver=resolver)

    with pytest.raises(OutboundEndpointError):
        await policy.validate_url("http://api.localhost/v1")

    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_policy_rejects_hostname_if_any_dns_address_is_not_public():
    async def resolve_mixed(_host: str, _port: int) -> tuple[str, ...]:
        return ("93.184.216.34", "10.20.30.40")

    policy = OutboundEndpointPolicy(resolver=resolve_mixed)

    with pytest.raises(OutboundEndpointError):
        await policy.validate_url("https://models.example.com/v1")


@pytest.mark.asyncio
async def test_policy_normalizes_public_endpoint_and_resolves_effective_port():
    resolved: list[tuple[str, int]] = []

    async def resolve_endpoint(host: str, port: int) -> tuple[str, ...]:
        resolved.append((host, port))
        return ("93.184.216.34",)

    policy = OutboundEndpointPolicy(resolver=resolve_endpoint)

    result = await policy.validate_url("HTTPS://Models.Example.COM:443/v1/")

    assert result == "https://models.example.com:443/v1"
    assert resolved == [("models.example.com", 443)]


@pytest.mark.asyncio
async def test_query_opt_in_preserves_signed_path_and_query_exactly():
    policy = OutboundEndpointPolicy(
        allow_query=True,
        require_https=True,
        resolver=resolve_public,
    )
    signed_url = (
        "HTTPS://Objects.Example.COM/result/"
        "?X-Amz-Credential=a%2Fb&X-Amz-Signature=AbC123&partNumber=1"
    )

    result = await policy.validate_url(signed_url)

    assert result == (
        "https://objects.example.com/result/"
        "?X-Amz-Credential=a%2Fb&X-Amz-Signature=AbC123&partNumber=1"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://objects.example.com/result?signature=secret",
        "ftp://objects.example.com/result?signature=secret",
        "https://user@objects.example.com/result?signature=secret",
        "https://objects.example.com/result?signature=secret#fragment",
    ],
)
async def test_https_signed_url_policy_rejects_unsafe_syntax(url):
    policy = OutboundEndpointPolicy(
        allow_query=True,
        require_https=True,
        resolver=resolve_public,
    )

    with pytest.raises(OutboundEndpointError):
        await policy.validate_url(url)


@pytest.mark.asyncio
async def test_policy_dns_failure_uses_non_sensitive_error_message():
    async def fail_resolution(_host: str, _port: int) -> tuple[str, ...]:
        raise socket.gaierror("internal resolver details for models.secret.example")

    policy = OutboundEndpointPolicy(resolver=fail_resolution)

    with pytest.raises(OutboundEndpointError) as exc_info:
        await policy.validate_url("https://models.secret.example/v1")

    assert str(exc_info.value) == OUTBOUND_ENDPOINT_ERROR_MESSAGE
    assert "secret.example" not in str(exc_info.value)
    assert "resolver" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_private_network_escape_hatch_requires_explicit_opt_in():
    async def resolve_private(_host: str, _port: int) -> tuple[str, ...]:
        return ("127.0.0.1", "10.20.30.40")

    policy = OutboundEndpointPolicy(
        allow_private_networks=True,
        resolver=resolve_private,
    )

    assert (
        await policy.validate_url("http://localhost:8000/v1/")
        == "http://localhost:8000/v1"
    )


@pytest.mark.asyncio
async def test_policy_never_follows_redirects(monkeypatch):
    redirect_response = (
        b"HTTP/1.1 302 Found\r\n"
        b"Location: http://127.0.0.1/latest/meta-data\r\n"
        b"Content-Length: 0\r\n"
        b"Connection: close\r\n\r\n"
    )
    policy = OutboundEndpointPolicy(resolver=resolve_public)
    transport = policy._build_guarded_transport()
    recording_backend = _RecordingAsyncBackend(
        _RecordingAsyncStream(response=redirect_response)
    )
    transport._pool._network_backend._backend = recording_backend
    monkeypatch.setattr(policy, "_build_guarded_transport", lambda: transport)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: None)
    ) as client:
        response = await policy.request(
            client,
            "GET",
            "https://models.example.com/v1/models",
            follow_redirects=True,
        )

    assert response.status_code == 302
    assert recording_backend.connections == [("93.184.216.34", 443)]


@pytest.mark.asyncio
async def test_guarded_transport_pins_ip_and_preserves_host_and_sni(
    monkeypatch,
):
    resolutions: list[tuple[str, int]] = []

    async def resolver(host: str, port: int) -> tuple[str, ...]:
        resolutions.append((host, port))
        return ("93.184.216.34",)

    policy = OutboundEndpointPolicy(resolver=resolver)
    transport = policy._build_guarded_transport()
    recording_backend = _RecordingAsyncBackend()
    transport._pool._network_backend._backend = recording_backend
    monkeypatch.setattr(policy, "_build_guarded_transport", lambda: transport)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:3128")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: None)
    ) as client:
        response = await policy.request(
            client,
            "GET",
            "https://models.example.com/v1/models",
        )

    assert response.status_code == 200
    assert resolutions == [
        ("models.example.com", 443),
        ("models.example.com", 443),
    ]
    assert recording_backend.connections == [("93.184.216.34", 443)]
    assert recording_backend.stream.server_hostnames == ["models.example.com"]
    wire_request = b"".join(recording_backend.stream.writes).lower()
    assert b"host: models.example.com\r\n" in wire_request


@pytest.mark.asyncio
async def test_guarded_stream_pins_ip_preserves_query_and_closes_response(
    monkeypatch,
    caplog,
):
    body = b"zip"
    stream_response = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Length: 3\r\n"
        b"Connection: close\r\n\r\n"
        + body
    )
    policy = OutboundEndpointPolicy(
        allow_query=True,
        require_https=True,
        resolver=resolve_public,
    )
    transport = policy._build_guarded_transport()
    recording_backend = _RecordingAsyncBackend(
        _RecordingAsyncStream(response=stream_response)
    )
    transport._pool._network_backend._backend = recording_backend
    monkeypatch.setattr(policy, "_build_guarded_transport", lambda: transport)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:3128")
    signed_url = "https://objects.example.com/result.zip?X-Signature=a%2Fb"

    with caplog.at_level(logging.INFO, logger="httpx"):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: None)
        ) as client:
            async with policy.stream(client, "GET", signed_url) as response:
                assert response.is_closed is False
                assert b"".join([chunk async for chunk in response.aiter_raw()]) == body
            assert response.is_closed is True

    assert recording_backend.connections == [("93.184.216.34", 443)]
    wire_request = b"".join(recording_backend.stream.writes)
    assert b"GET /result.zip?X-Signature=a%2Fb HTTP/1.1\r\n" in wire_request
    assert recording_backend.stream.closed is True
    assert "X-Signature" not in caplog.text
    assert "a%2Fb" not in caplog.text
    assert "https://objects.example.com/result.zip" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("rebound_address", ["10.20.30.40", "169.254.169.254"])
async def test_dns_rebinding_is_blocked_before_underlying_connect(
    monkeypatch,
    rebound_address,
):
    answers = iter(
        [
            ("93.184.216.34",),
            (rebound_address,),
        ]
    )

    async def resolver(_host: str, _port: int) -> tuple[str, ...]:
        return next(answers)

    policy = OutboundEndpointPolicy(resolver=resolver)
    transport = policy._build_guarded_transport()
    recording_backend = _RecordingAsyncBackend()
    transport._pool._network_backend._backend = recording_backend
    monkeypatch.setattr(policy, "_build_guarded_transport", lambda: transport)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: None)
    ) as client:
        with pytest.raises(OutboundEndpointError):
            await policy.request(
                client,
                "GET",
                "https://models.example.com/v1/models",
            )

    assert recording_backend.connections == []


def test_guarded_transport_fails_closed_for_incompatible_httpcore(monkeypatch):
    unsupported_transport = SimpleNamespace(
        _pool=SimpleNamespace(_network_backend=object()),
    )
    monkeypatch.setattr(
        policy_module.httpx,
        "AsyncHTTPTransport",
        lambda **_kwargs: unsupported_transport,
    )

    policy = OutboundEndpointPolicy(resolver=resolve_public)

    with pytest.raises(
        RuntimeError, match="does not support guarded outbound transports"
    ):
        policy._build_guarded_transport()
