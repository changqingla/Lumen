"""Minimal Docker control plane for policy-constrained Lumen sandboxes."""

from __future__ import annotations

import hmac
import logging
import os
import subprocess
import threading
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from src.community.aio_sandbox.local_backend import LocalContainerBackend
from src.community.aio_sandbox.sandbox_info import SandboxInfo
from src.sandbox_provisioner.contract import (
    INTERNAL_TOKEN_ENV,
    INTERNAL_TOKEN_HEADER,
    validate_host_root,
    validate_internal_token,
    validate_sandbox_binding,
    validate_sandbox_id,
    validate_sandbox_image,
    validate_sandbox_url,
    validate_thread_id,
)

logger = logging.getLogger(__name__)

_DEFAULT_IMAGE = (
    "crpi-wh1i56a4x558rrhm.cn-hangzhou.personal.cr.aliyuncs.com/"
    "changqinga/sandbox@sha256:"
    "742062f99915e5495df8d4bfeaf40a93197c87c7c47b4e2407cd2b6356df8f48"
)
_MANAGED_LABELS = {
    "com.lumen.managed-by": "sandbox-provisioner",
    "com.lumen.mount-layout": "v2",
}
_SANDBOX_ENVIRONMENT = {
    # Chromium's setuid sandbox cannot start under no-new-privileges. Keep the
    # container boundary and minimal capabilities instead of adding SYS_ADMIN.
    "BROWSER_NO_SANDBOX": "--no-sandbox",
}
_COMPATIBILITY_CAPABILITIES = (
    "CHOWN",
    "DAC_OVERRIDE",
    "FOWNER",
    "SETGID",
    "SETUID",
)
_TMPFS_MOUNTS = (
    "/tmp:rw,nosuid,nodev,size=512m",
    "/run:rw,nosuid,nodev,size=64m",
)


@dataclass(frozen=True)
class ProvisionerSettings:
    internal_token: str = field(repr=False)
    image: str
    host_state_root: PurePosixPath
    visible_state_root: Path
    host_skills_root: PurePosixPath
    visible_skills_root: Path
    base_port: int = 18080
    pids_limit: int = 512


def _validate_visible_root(value: str | None, *, setting_name: str) -> Path:
    raw = str(value or "")
    if not raw:
        raise RuntimeError(f"{setting_name} is required")
    path = Path(raw)
    if not path.is_absolute() or path == Path("/") or path.is_symlink():
        raise RuntimeError(f"{setting_name} must be a non-symlink, non-root absolute path")
    resolved = path.resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise RuntimeError(f"{setting_name} must reference an existing directory")
    return resolved


def _bounded_int(value: str | None, *, setting_name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value or ""))
    except ValueError as exc:
        raise RuntimeError(f"{setting_name} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise RuntimeError(f"{setting_name} must be between {minimum} and {maximum}")
    return parsed


def load_settings(environ: dict[str, str] | os._Environ[str] | None = None) -> ProvisionerSettings:
    env = environ if environ is not None else os.environ
    host_state_root = validate_host_root(
        env.get("SANDBOX_PROVISIONER_HOST_STATE_ROOT"),
        setting_name="SANDBOX_PROVISIONER_HOST_STATE_ROOT",
    )
    host_skills_root = validate_host_root(
        env.get("SANDBOX_PROVISIONER_HOST_SKILLS_ROOT"),
        setting_name="SANDBOX_PROVISIONER_HOST_SKILLS_ROOT",
    )
    if (
        host_state_root == host_skills_root
        or host_state_root in host_skills_root.parents
        or host_skills_root in host_state_root.parents
    ):
        raise RuntimeError("Sandbox state and skills host roots must not overlap")

    visible_state_root = _validate_visible_root(
        env.get("SANDBOX_PROVISIONER_VISIBLE_STATE_ROOT"),
        setting_name="SANDBOX_PROVISIONER_VISIBLE_STATE_ROOT",
    )
    visible_skills_root = _validate_visible_root(
        env.get("SANDBOX_PROVISIONER_VISIBLE_SKILLS_ROOT"),
        setting_name="SANDBOX_PROVISIONER_VISIBLE_SKILLS_ROOT",
    )
    if (
        visible_state_root == visible_skills_root
        or visible_state_root in visible_skills_root.parents
        or visible_skills_root in visible_state_root.parents
    ):
        raise RuntimeError("Sandbox state and skills visible roots must not overlap")

    return ProvisionerSettings(
        internal_token=validate_internal_token(env.get(INTERNAL_TOKEN_ENV)),
        image=validate_sandbox_image(
            env.get("SANDBOX_PROVISIONER_IMAGE", _DEFAULT_IMAGE)
        ),
        host_state_root=host_state_root,
        visible_state_root=visible_state_root,
        host_skills_root=host_skills_root,
        visible_skills_root=visible_skills_root,
        base_port=_bounded_int(
            env.get("SANDBOX_PROVISIONER_BASE_PORT", "18080"),
            setting_name="SANDBOX_PROVISIONER_BASE_PORT",
            minimum=1024,
            maximum=60000,
        ),
        pids_limit=_bounded_int(
            env.get("SANDBOX_PROVISIONER_PIDS_LIMIT", "512"),
            setting_name="SANDBOX_PROVISIONER_PIDS_LIMIT",
            minimum=32,
            maximum=4096,
        ),
    )


class _KeyedLocks:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._entries: dict[str, tuple[threading.Lock, int]] = {}

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        with self._guard:
            lock, users = self._entries.get(key, (threading.Lock(), 0))
            self._entries[key] = (lock, users + 1)
        try:
            with lock:
                yield
        finally:
            with self._guard:
                current_lock, current_users = self._entries[key]
                if current_users == 1:
                    del self._entries[key]
                else:
                    self._entries[key] = (current_lock, current_users - 1)


class DockerSandboxProvisioner:
    def __init__(self, settings: ProvisionerSettings) -> None:
        self._settings = settings
        self._locks = _KeyedLocks()
        self._backend = LocalContainerBackend(
            image=settings.image,
            base_port=settings.base_port,
            container_prefix="lumen-sandbox",
            config_mounts=[],
            environment=_SANDBOX_ENVIRONMENT,
            pids_limit=settings.pids_limit,
            drop_all_capabilities=True,
            capability_additions=_COMPATIBILITY_CAPABILITIES,
            labels=_MANAGED_LABELS,
            read_only_rootfs=False,
            tmpfs_mounts=_TMPFS_MOUNTS,
        )

    @property
    def backend(self) -> LocalContainerBackend:
        return self._backend

    def validate_runtime(self) -> None:
        socket_path = Path("/var/run/docker.sock")
        if not socket_path.exists() or not socket_path.is_socket():
            raise RuntimeError("Docker socket is unavailable to the sandbox provisioner")
        if self._backend.runtime != "docker":
            raise RuntimeError("The sandbox provisioner requires the Docker runtime")
        try:
            subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("The Docker daemon is unavailable to the sandbox provisioner") from exc

    def _thread_mounts(self, thread_id: str) -> list[tuple[str, str, bool]]:
        validated = validate_thread_id(thread_id)
        relative_root = Path("threads") / validated / "user-data"
        visible_user_data = self._settings.visible_state_root / relative_root
        mounts: list[tuple[str, str, bool]] = []
        for directory, read_only in (
            ("workspace", False),
            ("uploads", False),
            ("knowledge", True),
            ("outputs", False),
        ):
            visible_directory = visible_user_data / directory
            visible_directory.mkdir(parents=True, exist_ok=True)
            visible_directory.chmod(0o777)
            host_directory = (
                self._settings.host_state_root
                / "threads"
                / validated
                / "user-data"
                / directory
            )
            mounts.append(
                (
                    str(host_directory),
                    f"/mnt/user-data/{directory}",
                    read_only,
                )
            )
        mounts.append((str(self._settings.host_skills_root), "/mnt/skills", True))
        return mounts

    def create(self, thread_id: str, sandbox_id: str) -> SandboxInfo:
        thread_id, sandbox_id = validate_sandbox_binding(thread_id, sandbox_id)
        with self._locks.hold(sandbox_id):
            existing = self._backend.discover(sandbox_id)
            if existing is not None:
                return existing
            mounts = self._thread_mounts(thread_id)
            return self._backend.create(thread_id, sandbox_id, extra_mounts=mounts)

    def discover(self, sandbox_id: str) -> SandboxInfo | None:
        sandbox_id = validate_sandbox_id(sandbox_id)
        with self._locks.hold(sandbox_id):
            return self._backend.discover(sandbox_id)

    def destroy(self, sandbox_id: str) -> None:
        sandbox_id = validate_sandbox_id(sandbox_id)
        with self._locks.hold(sandbox_id):
            container_name = self._backend._container_name(sandbox_id)
            if not self._backend._is_container_running(container_name):
                return
            if not self._backend._has_required_labels(container_name):
                raise RuntimeError("Refusing to destroy a container not owned by this provisioner")
            self._backend.destroy(
                SandboxInfo(
                    sandbox_id=sandbox_id,
                    sandbox_url="",
                    container_name=container_name,
                )
            )


class CreateSandboxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sandbox_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    thread_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class SandboxResponse(BaseModel):
    sandbox_id: str
    provisioned_sandbox_id: str | None = None
    sandbox_url: str
    status: str


class ProvisionerInternalAuthMiddleware:
    def __init__(self, app: object) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: object, send: object) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method", "")
        if path == "/health" and method in {"GET", "HEAD"}:
            await self.app(scope, receive, send)
            return

        settings = scope["app"].state.provisioner_settings
        header_name = INTERNAL_TOKEN_HEADER.lower().encode("ascii")
        supplied_values = [
            value.decode("latin-1")
            for key, value in scope.get("headers", [])
            if key.lower() == header_name
        ]
        if len(supplied_values) != 1 or not hmac.compare_digest(
            supplied_values[0],
            settings.internal_token,
        ):
            response = JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def create_app(
    *,
    settings: ProvisionerSettings | None = None,
    service: DockerSandboxProvisioner | None = None,
    validate_runtime: bool = True,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        loaded_settings = settings or load_settings()
        loaded_service = service or DockerSandboxProvisioner(loaded_settings)
        if validate_runtime:
            loaded_service.validate_runtime()
        application.state.provisioner_settings = loaded_settings
        application.state.provisioner_service = loaded_service
        logger.info("Sandbox provisioner is ready")
        yield

    application = FastAPI(
        title="Lumen Docker Sandbox Provisioner",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.add_middleware(ProvisionerInternalAuthMiddleware)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/api/sandboxes", response_model=SandboxResponse)
    def create_sandbox(payload: CreateSandboxRequest, request: Request) -> SandboxResponse:
        try:
            info = request.app.state.provisioner_service.create(
                payload.thread_id,
                payload.sandbox_id,
            )
            return SandboxResponse(
                sandbox_id=info.sandbox_id,
                provisioned_sandbox_id=info.provisioned_sandbox_id,
                sandbox_url=validate_sandbox_url(info.sandbox_url),
                status="Running",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            logger.warning("Sandbox creation failed for %s", payload.sandbox_id)
            raise HTTPException(status_code=503, detail="Sandbox creation failed") from exc

    @application.get("/api/sandboxes/{sandbox_id}", response_model=SandboxResponse)
    def get_sandbox(sandbox_id: str, request: Request) -> SandboxResponse:
        try:
            info = request.app.state.provisioner_service.discover(sandbox_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if info is None:
            raise HTTPException(status_code=404, detail="Sandbox not found")
        return SandboxResponse(
            sandbox_id=info.sandbox_id,
            provisioned_sandbox_id=info.provisioned_sandbox_id,
            sandbox_url=validate_sandbox_url(info.sandbox_url),
            status="Running",
        )

    @application.delete("/api/sandboxes/{sandbox_id}")
    def destroy_sandbox(sandbox_id: str, request: Request) -> dict[str, object]:
        try:
            request.app.state.provisioner_service.destroy(sandbox_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            logger.warning("Sandbox destruction failed for %s", sandbox_id)
            raise HTTPException(status_code=503, detail="Sandbox destruction failed") from exc
        return {"ok": True, "sandbox_id": sandbox_id}

    return application


app = create_app()
