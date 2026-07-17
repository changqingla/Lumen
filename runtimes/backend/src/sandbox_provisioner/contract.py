"""Shared validation for sandbox provisioner clients and servers."""

from __future__ import annotations

import hashlib
import ipaddress
import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit, urlunsplit

INTERNAL_TOKEN_ENV = "SANDBOX_PROVISIONER_INTERNAL_TOKEN"
INTERNAL_TOKEN_HEADER = "X-Sandbox-Provisioner-Token"
MIN_INTERNAL_TOKEN_LENGTH = 32
MAX_RESPONSE_BYTES = 16 * 1024
CURRENT_SANDBOX_ID_HEX_LENGTH = 32
LEGACY_SANDBOX_ID_HEX_LENGTH = 8

_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SANDBOX_ID_RE = re.compile(r"^(?:[0-9a-f]{8}|[0-9a-f]{32})$")
_IMAGE_DIGEST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]*@sha256:[0-9a-f]{64}$")
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$")
_FORBIDDEN_HOST_ROOTS = (
    PurePosixPath("/boot"),
    PurePosixPath("/dev"),
    PurePosixPath("/etc"),
    PurePosixPath("/proc"),
    PurePosixPath("/run"),
    PurePosixPath("/sys"),
)


def validate_internal_token(value: str | None) -> str:
    """Reject absent, non-ASCII, weak, or obvious template credentials."""
    token = str(value or "")
    if not token:
        raise RuntimeError(f"{INTERNAL_TOKEN_ENV} is required")
    if token != token.strip() or not token.isascii() or not token.isprintable():
        raise RuntimeError(
            f"{INTERNAL_TOKEN_ENV} must contain only printable ASCII characters "
            "without surrounding whitespace"
        )
    lowered = token.lower()
    if len(token) < MIN_INTERNAL_TOKEN_LENGTH or lowered.startswith(
        ("change-me", "replace-with-", "example", "template", "your-")
    ):
        raise RuntimeError(
            f"{INTERNAL_TOKEN_ENV} must be a random token of at least "
            f"{MIN_INTERNAL_TOKEN_LENGTH} characters"
        )
    return token


def validate_thread_id(value: str) -> str:
    if not isinstance(value, str) or _THREAD_ID_RE.fullmatch(value) is None:
        raise ValueError(
            "thread_id must be 1-128 ASCII alphanumeric, underscore, or hyphen characters"
        )
    return value


def validate_sandbox_id(value: str) -> str:
    if not isinstance(value, str) or _SANDBOX_ID_RE.fullmatch(value) is None:
        raise ValueError(
            "sandbox_id must be 32 lowercase hexadecimal characters "
            "or an 8-character legacy identifier"
        )
    return value


def validate_sandbox_image(value: str | None) -> str:
    """Require an immutable sha256 image reference for the socket-owning service."""
    image = str(value or "")
    if (
        not image
        or image != image.strip()
        or not image.isascii()
        or not image.isprintable()
        or _IMAGE_DIGEST_RE.fullmatch(image) is None
    ):
        raise RuntimeError(
            "SANDBOX_PROVISIONER_IMAGE must be pinned by an immutable sha256 digest"
        )
    return image


def deterministic_sandbox_id(thread_id: str) -> str:
    validated = validate_thread_id(thread_id)
    return hashlib.sha256(f"mount-v2:{validated}".encode("ascii")).hexdigest()[
        :CURRENT_SANDBOX_ID_HEX_LENGTH
    ]


def legacy_deterministic_sandbox_id(thread_id: str) -> str:
    """Return the former 32-bit identifier for lookup and cleanup only."""

    return deterministic_sandbox_id(thread_id)[:LEGACY_SANDBOX_ID_HEX_LENGTH]


def validate_sandbox_binding(thread_id: str, sandbox_id: str) -> tuple[str, str]:
    validated_thread = validate_thread_id(thread_id)
    validated_sandbox = validate_sandbox_id(sandbox_id)
    if deterministic_sandbox_id(validated_thread) != validated_sandbox:
        raise ValueError("sandbox_id does not match the deterministic thread binding")
    return validated_thread, validated_sandbox


def validate_host_root(value: str | None, *, setting_name: str) -> PurePosixPath:
    """Validate a Docker-daemon-visible bind root without touching host files."""
    raw = str(value or "")
    if not raw:
        raise RuntimeError(f"{setting_name} is required")
    if raw != raw.strip() or not raw.isascii() or not raw.isprintable():
        raise RuntimeError(f"{setting_name} must be a printable ASCII absolute path")
    if ":" in raw or "\\" in raw or "//" in raw:
        raise RuntimeError(f"{setting_name} contains an unsafe path character")

    path = PurePosixPath(raw)
    if not path.is_absolute() or str(path) != raw or path == PurePosixPath("/"):
        raise RuntimeError(f"{setting_name} must be a normalized, non-root absolute path")
    if ".." in path.parts:
        raise RuntimeError(f"{setting_name} must not contain parent traversal")
    if any(path == root or root in path.parents for root in _FORBIDDEN_HOST_ROOTS):
        raise RuntimeError(f"{setting_name} points into a protected host filesystem root")
    if path == PurePosixPath("/var/run/docker.sock"):
        raise RuntimeError(f"{setting_name} must not reference the Docker socket")
    return path


def _validate_http_origin(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value or not value.isascii():
        raise ValueError(f"{field_name} must be a non-empty ASCII HTTP(S) URL")
    if value != value.strip() or "\\" in value:
        raise ValueError(f"{field_name} contains an unsafe URL character")

    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError(f"{field_name} must use http or https")
    if not parsed.netloc or parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field_name} must be an unauthenticated HTTP origin")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError(f"{field_name} must not include a path, query, or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field_name} contains an invalid port") from exc

    hostname = parsed.hostname
    if not hostname or "%" in hostname:
        raise ValueError(f"{field_name} contains an invalid hostname")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        if _HOSTNAME_RE.fullmatch(hostname) is None:
            raise ValueError(f"{field_name} contains an invalid hostname") from None

    host = f"[{hostname.lower()}]" if ":" in hostname else hostname.lower()
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


def validate_provisioner_url(value: str) -> str:
    return _validate_http_origin(value, field_name="provisioner_url")


def validate_sandbox_url(value: str) -> str:
    return _validate_http_origin(value, field_name="sandbox_url")


def validate_sandbox_response(data: object, *, expected_sandbox_id: str) -> dict[str, str]:
    expected = validate_sandbox_id(expected_sandbox_id)
    if not isinstance(data, dict):
        raise ValueError("Provisioner response must be a JSON object")

    sandbox_id = validate_sandbox_id(data.get("sandbox_id"))
    if sandbox_id != expected:
        raise ValueError("Provisioner response sandbox_id does not match the request")
    provisioned_sandbox_id = validate_sandbox_id(
        data.get("provisioned_sandbox_id") or sandbox_id
    )
    if provisioned_sandbox_id not in {sandbox_id, sandbox_id[:8]}:
        raise ValueError(
            "Provisioner response contains an unrelated provisioned_sandbox_id"
        )
    sandbox_url = validate_sandbox_url(data.get("sandbox_url"))
    status = data.get("status")
    if status not in {"Pending", "Running", "Succeeded", "Failed", "Unknown"}:
        raise ValueError("Provisioner response contains an invalid sandbox status")
    return {
        "sandbox_id": sandbox_id,
        "provisioned_sandbox_id": provisioned_sandbox_id,
        "sandbox_url": sandbox_url,
        "status": status,
    }
