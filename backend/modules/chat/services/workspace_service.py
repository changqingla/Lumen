"""Session-scoped workspace manifest service."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import mimetypes
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from schemas.workspace import (
    WorkspaceAttachmentInput,
    WorkspaceAttachmentRecord,
    WorkspaceManifest,
)
from utils.minio_client import download_file, object_exists, upload_file


_INLINE_IMAGE_PREFIXES = {
    "data:image/jpeg;base64,": ("image/jpeg", ".jpg"),
    "data:image/jpg;base64,": ("image/jpeg", ".jpg"),
    "data:image/png;base64,": ("image/png", ".png"),
    "data:image/webp;base64,": ("image/webp", ".webp"),
}

_MANIFEST_WRITE_MAX_MERGE_READS = 2
_manifest_locks: dict[str, asyncio.Lock] = {}
_manifest_lock_guard = asyncio.Lock()


def _load_workspace_asset_rules() -> dict[str, dict[str, object]]:
    explicit_path = (os.getenv("WORKSPACE_ASSET_RULES_PATH", "") or "").strip()
    candidate_paths: list[Path] = []
    if explicit_path:
        candidate_paths.append(Path(explicit_path))

    current_path = Path(__file__).resolve()
    for parent in current_path.parents:
        candidate_paths.append(parent / "config" / "workspace_asset_rules.json")
        candidate_paths.append(parent / "shared" / "config" / "workspace_asset_rules.json")

    rules_path: Optional[Path] = None
    for candidate in candidate_paths:
        if candidate.is_file():
            rules_path = candidate
            break
    if rules_path is None:
        searched_paths = ", ".join(str(path) for path in candidate_paths) or "<none>"
        raise FileNotFoundError(
            "workspace_asset_rules_not_found: searched paths: "
            f"{searched_paths}"
        )
    with rules_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise ValueError("workspace_asset_rules_invalid: root must be object")
    return payload


_WORKSPACE_ASSET_RULES = _load_workspace_asset_rules()
_MIME_EXACT_VIEW_TYPE = {
    str(key).strip().lower(): str(value).strip()
    for key, value in dict(_WORKSPACE_ASSET_RULES.get("mime_exact_view_type") or {}).items()
    if str(key).strip() and str(value).strip()
}
_SUFFIX_VIEW_TYPE = {
    str(key).strip().lower(): str(value).strip()
    for key, value in dict(_WORKSPACE_ASSET_RULES.get("suffix_view_type") or {}).items()
    if str(key).strip() and str(value).strip()
}
_MIME_PREFIX_VIEW_TYPE = {
    str(key).strip().lower(): str(value).strip()
    for key, value in dict(_WORKSPACE_ASSET_RULES.get("mime_prefix_view_type") or {}).items()
    if str(key).strip() and str(value).strip()
}
_AVAILABLE_VIEWS_BY_VIEW_TYPE = {
    str(key).strip(): [str(item).strip() for item in value if isinstance(item, str) and str(item).strip()]
    for key, value in dict(_WORKSPACE_ASSET_RULES.get("available_views_by_view_type") or {}).items()
    if str(key).strip() and isinstance(value, list)
}
_CAPABILITIES_BY_VIEW_TYPE = {
    str(key).strip(): [str(item).strip() for item in value if isinstance(item, str) and str(item).strip()]
    for key, value in dict(_WORKSPACE_ASSET_RULES.get("capabilities_by_view_type") or {}).items()
    if str(key).strip() and isinstance(value, list)
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compute_tenant_key(user_id: str) -> str:
    return hashlib.blake2b(user_id.encode("utf-8"), digest_size=8).hexdigest()


def _sanitize_segment(value: str, field_name: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    if "/" in normalized or "\\" in normalized or ".." in normalized:
        raise ValueError(f"{field_name} 包含非法路径字符")
    return normalized


def _normalize_string_list(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for item in values:
        text = (item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _infer_view_type(name: str, mime_type: Optional[str]) -> str:
    suffix = Path(name or "").suffix.lower()
    normalized_mime = (mime_type or "").strip().lower()
    if normalized_mime in _MIME_EXACT_VIEW_TYPE:
        return _MIME_EXACT_VIEW_TYPE[normalized_mime]
    if suffix in _SUFFIX_VIEW_TYPE:
        return _SUFFIX_VIEW_TYPE[suffix]
    for prefix, view_type in _MIME_PREFIX_VIEW_TYPE.items():
        if normalized_mime.startswith(prefix):
            return view_type
    return "binary"


def _infer_available_views(view_type: str) -> list[str]:
    return list(_AVAILABLE_VIEWS_BY_VIEW_TYPE.get(view_type, []))


def _infer_capabilities(view_type: str) -> list[str]:
    return list(_CAPABILITIES_BY_VIEW_TYPE.get(view_type, _CAPABILITIES_BY_VIEW_TYPE.get("binary", ["sandbox_process"])))


def _infer_parse_status(available_views: list[str]) -> str:
    return "ready" if available_views else "none"


async def _get_manifest_lock(manifest_object_path: str) -> asyncio.Lock:
    async with _manifest_lock_guard:
        lock = _manifest_locks.get(manifest_object_path)
        if lock is None:
            lock = asyncio.Lock()
            _manifest_locks[manifest_object_path] = lock
        return lock


class WorkspaceService:
    """Manage Lumen session workspace assets backed by MinIO manifest."""

    def __init__(self, session_id: str, user_id: str) -> None:
        self.session_id = _sanitize_segment(session_id, "session_id")
        self.user_id = _sanitize_segment(user_id, "user_id")
        self.tenant_key = _compute_tenant_key(self.user_id)
        self.base_prefix = f"v2/tenants/{self.tenant_key}/sessions/{self.session_id}"
        self.files_prefix = f"{self.base_prefix}/files"
        self.manifest_object_path = f"{self.base_prefix}/workspace/manifest.json"

    async def load_manifest(self) -> WorkspaceManifest:
        if not await object_exists(self.manifest_object_path):
            return WorkspaceManifest(
                session_id=self.session_id,
                user_id=self.user_id,
                updated_at=_utcnow_iso(),
                assets=[],
            )

        try:
            raw_payload = (await download_file(self.manifest_object_path)).decode("utf-8")
        except Exception as exc:
            raise ValueError("workspace_manifest_read_failed") from exc

        try:
            payload = json.loads(raw_payload)
        except Exception as exc:
            raise ValueError("workspace_manifest_parse_failed") from exc

        try:
            manifest = WorkspaceManifest.model_validate(payload)
        except Exception as exc:
            raise ValueError("workspace_manifest_validate_failed") from exc
        if manifest.session_id != self.session_id or manifest.user_id != self.user_id:
            raise ValueError("workspace_manifest_mismatch: session/user mismatch")
        for asset in manifest.assets:
            if not asset.object_path.startswith(f"{self.files_prefix}/"):
                raise ValueError("workspace_manifest_invalid_asset")
        return manifest

    async def save_manifest(self, manifest: WorkspaceManifest) -> None:
        latest_manifest = manifest
        for _ in range(_MANIFEST_WRITE_MAX_MERGE_READS):
            current_latest = await self.load_manifest()
            latest_manifest = self._merge_manifests(current_latest, latest_manifest)

        latest_manifest.updated_at = _utcnow_iso()
        await upload_file(
            self.manifest_object_path,
            json.dumps(latest_manifest.model_dump(exclude_none=True), ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json",
        )

    async def resolve_request_assets(
        self,
        attachments: Optional[List[WorkspaceAttachmentInput]] = None,
        image_data_urls: Optional[List[str]] = None,
    ) -> List[WorkspaceAttachmentRecord]:
        """Resolve assets touched by the current request and persist them into the manifest."""

        lock = await _get_manifest_lock(self.manifest_object_path)
        async with lock:
            manifest = await self.load_manifest()
            request_assets, mutated = await self._resolve_request_assets_locked(
                manifest,
                attachments=attachments,
                image_data_urls=image_data_urls,
            )

            if mutated:
                await self.save_manifest(manifest)
            return request_assets

    async def _resolve_request_assets_locked(
        self,
        manifest: WorkspaceManifest,
        *,
        attachments: Optional[List[WorkspaceAttachmentInput]] = None,
        image_data_urls: Optional[List[str]] = None,
    ) -> tuple[List[WorkspaceAttachmentRecord], bool]:
        mutated = False
        request_assets: list[WorkspaceAttachmentRecord] = []

        for attachment in attachments or []:
            mutated = await self._register_existing_attachment(manifest, attachment) or mutated
            resolved_asset = self._find_existing_asset(manifest, attachment)
            if resolved_asset is not None:
                request_assets.append(resolved_asset)

        for image_data_url in image_data_urls or []:
            mutated = await self._register_inline_image(manifest, image_data_url) or mutated
            resolved_asset = self._find_inline_image_asset(manifest, image_data_url)
            if resolved_asset is not None:
                request_assets.append(resolved_asset)

        return self._dedupe_assets(request_assets), mutated

    async def _register_existing_attachment(
        self,
        manifest: WorkspaceManifest,
        attachment: WorkspaceAttachmentInput,
    ) -> bool:
        if not attachment.object_path:
            raise ValueError(f"附件 {attachment.name} 缺少 object_path")
        if not attachment.object_path.startswith(f"{self.files_prefix}/"):
            raise ValueError(f"附件对象不属于当前会话工作区: {attachment.object_path}")
        if not await object_exists(attachment.object_path):
            raise ValueError(f"附件对象不存在: {attachment.object_path}")

        existing = self._find_existing_asset(manifest, attachment)
        requested_workspace_path = (
            attachment.workspace_path
            or (existing.workspace_path if existing is not None else None)
            or self._default_workspace_path(
                role=attachment.role,
                filename=attachment.name,
            )
        )
        workspace_path = (
            requested_workspace_path
            if attachment.workspace_path
            else self._allocate_workspace_path(
                manifest,
                requested_workspace_path,
                attachment.object_path,
            )
        )
        resolved_mime_type = (
            attachment.mime_type
            or (existing.mime_type if existing is not None else None)
            or mimetypes.guess_type(attachment.name)[0]
        )
        resolved_view_type = self._resolve_asset_view_type(
            name=attachment.name,
            mime_type=resolved_mime_type,
            explicit_value=attachment.view_type if "view_type" in attachment.model_fields_set else None,
            existing=existing,
        )
        resolved_available_views = self._resolve_asset_available_views(
            explicit_value=attachment.available_views if "available_views" in attachment.model_fields_set else None,
            existing=existing,
            inferred_view_type=resolved_view_type,
        )
        resolved_capabilities = self._resolve_asset_capabilities(
            explicit_value=attachment.capabilities if "capabilities" in attachment.model_fields_set else None,
            existing=existing,
            inferred_view_type=resolved_view_type,
        )
        resolved_parse_status = self._resolve_asset_parse_status(
            explicit_value=attachment.parse_status if "parse_status" in attachment.model_fields_set else None,
            existing=existing,
            available_views=resolved_available_views,
        )
        record = WorkspaceAttachmentRecord(
            attachment_id=(
                attachment.attachment_id
                or (existing.attachment_id if existing is not None else None)
                or self._generate_attachment_id()
            ),
            session_id=self.session_id,
            user_id=self.user_id,
            name=attachment.name,
            object_path=attachment.object_path,
            workspace_path=workspace_path,
            mime_type=resolved_mime_type,
            source_kind=(
                attachment.source_kind
                if "source_kind" in attachment.model_fields_set
                else (existing.source_kind if existing is not None else "user_upload")
            ),
            role=(
                attachment.role
                if "role" in attachment.model_fields_set
                else (existing.role if existing is not None else "source")
            ),
            input_mode=(
                attachment.input_mode
                if "input_mode" in attachment.model_fields_set
                else (existing.input_mode if existing is not None else "workspace_file")
            ),
            size_bytes=attachment.size_bytes if attachment.size_bytes is not None else (existing.size_bytes if existing is not None else None),
            sha256=attachment.sha256 or (existing.sha256 if existing is not None else None),
            parent_attachment_id=(
                attachment.parent_attachment_id
                if "parent_attachment_id" in attachment.model_fields_set
                else (existing.parent_attachment_id if existing is not None else None)
            ),
            view_type=resolved_view_type,
            available_views=resolved_available_views,
            capabilities=resolved_capabilities,
            parse_status=resolved_parse_status,
            created_at=existing.created_at if existing is not None else _utcnow_iso(),
            metadata={
                **(existing.metadata if existing is not None else {}),
                **dict(attachment.metadata or {}),
            },
        )
        return self._upsert_manifest_asset(manifest, record)

    async def _register_inline_image(
        self,
        manifest: WorkspaceManifest,
        image_data_url: str,
    ) -> bool:
        parsed = self._parse_inline_image_data_url(image_data_url)
        if parsed is None:
            return False

        mime_type, suffix, payload = parsed
        sha256 = hashlib.sha256(payload).hexdigest()
        existing = next(
            (
                asset
                for asset in manifest.assets
                if asset.sha256 == sha256 and asset.mime_type == mime_type and asset.role == "source"
            ),
            None,
        )
        if existing is not None:
            return False

        attachment_id = self._generate_attachment_id()
        filename = f"image-{sha256[:12]}{suffix}"
        workspace_path = f"input/{filename}"
        object_path = f"{self.files_prefix}/{workspace_path}"
        await upload_file(object_path, payload, mime_type)

        record = WorkspaceAttachmentRecord(
            attachment_id=attachment_id,
            session_id=self.session_id,
            user_id=self.user_id,
            name=filename,
            object_path=object_path,
            workspace_path=workspace_path,
            mime_type=mime_type,
            source_kind="user_upload",
            role="source",
            input_mode="both",
            size_bytes=len(payload),
            sha256=sha256,
            view_type="image",
            available_views=["vision"],
            capabilities=["vision_read", "image_transform"],
            parse_status="ready",
            created_at=_utcnow_iso(),
            metadata={"origin": "image_data_url"},
        )
        return self._upsert_manifest_asset(manifest, record)

    @staticmethod
    def _resolve_asset_view_type(
        *,
        name: str,
        mime_type: Optional[str],
        explicit_value: Optional[str],
        existing: Optional[WorkspaceAttachmentRecord],
    ) -> str:
        if explicit_value:
            return explicit_value
        inferred_view_type = _infer_view_type(name, mime_type)
        if inferred_view_type != "binary":
            return inferred_view_type
        if existing is not None and existing.view_type:
            return existing.view_type
        return inferred_view_type

    @staticmethod
    def _resolve_asset_available_views(
        *,
        explicit_value: Optional[list[str]],
        existing: Optional[WorkspaceAttachmentRecord],
        inferred_view_type: str,
    ) -> list[str]:
        if explicit_value is not None:
            return list(explicit_value)
        inferred_available_views = _infer_available_views(inferred_view_type)
        if inferred_available_views:
            return inferred_available_views
        if existing is not None and existing.available_views:
            return list(existing.available_views)
        return inferred_available_views

    @staticmethod
    def _resolve_asset_capabilities(
        *,
        explicit_value: Optional[list[str]],
        existing: Optional[WorkspaceAttachmentRecord],
        inferred_view_type: str,
    ) -> list[str]:
        if explicit_value is not None:
            return _normalize_string_list(explicit_value)
        inferred_capabilities = _infer_capabilities(inferred_view_type)
        if inferred_view_type != "binary":
            return inferred_capabilities
        if existing is not None and existing.capabilities:
            return list(existing.capabilities)
        return inferred_capabilities

    @staticmethod
    def _resolve_asset_parse_status(
        *,
        explicit_value: Optional[str],
        existing: Optional[WorkspaceAttachmentRecord],
        available_views: list[str],
    ) -> str:
        if explicit_value:
            return explicit_value
        inferred_parse_status = _infer_parse_status(available_views)
        if inferred_parse_status != "none":
            return inferred_parse_status
        if existing is not None and existing.parse_status:
            return existing.parse_status
        return inferred_parse_status

    def _upsert_manifest_asset(
        self,
        manifest: WorkspaceManifest,
        record: WorkspaceAttachmentRecord,
    ) -> bool:
        for index, current in enumerate(manifest.assets):
            if (
                current.attachment_id == record.attachment_id
                or current.workspace_path == record.workspace_path
                or current.object_path == record.object_path
            ):
                if current == record:
                    return False
                manifest.assets[index] = record
                return True
        else:
            manifest.assets.append(record)
            return True

    def _default_workspace_path(self, *, role: str, filename: str) -> str:
        normalized_name = _sanitize_segment(filename, "filename")
        if role == "artifact":
            return f"artifacts/{normalized_name}"
        if role == "derived":
            return f"derived/{normalized_name}"
        return f"input/{normalized_name}"

    @staticmethod
    def _allocate_workspace_path(
        manifest: WorkspaceManifest,
        workspace_path: str,
        object_path: str,
    ) -> str:
        existing = next(
            (asset for asset in manifest.assets if asset.workspace_path == workspace_path),
            None,
        )
        if existing is None or existing.object_path == object_path:
            return workspace_path

        path_prefix, dot, suffix = workspace_path.rpartition(".")
        base = path_prefix if dot else workspace_path
        extension = f".{suffix}" if dot else ""
        index = 2
        while True:
            candidate = f"{base}-{index}{extension}"
            collision = next(
                (asset for asset in manifest.assets if asset.workspace_path == candidate),
                None,
            )
            if collision is None or collision.object_path == object_path:
                return candidate
            index += 1

    @staticmethod
    def _generate_attachment_id() -> str:
        return f"att_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _find_existing_asset(
        manifest: WorkspaceManifest,
        attachment: WorkspaceAttachmentInput,
    ) -> Optional[WorkspaceAttachmentRecord]:
        for asset in manifest.assets:
            if attachment.attachment_id and asset.attachment_id == attachment.attachment_id:
                return asset
        for asset in manifest.assets:
            if attachment.object_path and asset.object_path == attachment.object_path:
                return asset
        for asset in manifest.assets:
            if attachment.workspace_path and asset.workspace_path == attachment.workspace_path:
                return asset
        return None

    @staticmethod
    def _find_inline_image_asset(
        manifest: WorkspaceManifest,
        image_data_url: str,
    ) -> Optional[WorkspaceAttachmentRecord]:
        parsed = WorkspaceService._parse_inline_image_data_url(image_data_url)
        if parsed is None:
            return None

        mime_type, _suffix, payload = parsed
        sha256 = hashlib.sha256(payload).hexdigest()
        return next(
            (
                asset
                for asset in manifest.assets
                if asset.sha256 == sha256 and asset.mime_type == mime_type and asset.role == "source"
            ),
            None,
        )

    @staticmethod
    def _dedupe_assets(
        assets: Iterable[WorkspaceAttachmentRecord],
    ) -> list[WorkspaceAttachmentRecord]:
        deduped: list[WorkspaceAttachmentRecord] = []
        seen_keys: set[str] = set()
        for asset in assets:
            key = asset.attachment_id or asset.object_path or asset.workspace_path
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(asset)
        return deduped

    @staticmethod
    def _merge_manifests(
        base: WorkspaceManifest,
        overlay: WorkspaceManifest,
    ) -> WorkspaceManifest:
        merged_assets = list(base.assets)

        for record in overlay.assets:
            matched_index = next(
                (
                    index
                    for index, current in enumerate(merged_assets)
                    if (
                        current.attachment_id == record.attachment_id
                        or current.object_path == record.object_path
                        or current.workspace_path == record.workspace_path
                    )
                ),
                None,
            )
            if matched_index is None:
                merged_assets.append(record)
            else:
                merged_assets[matched_index] = record

        return WorkspaceManifest(
            session_id=base.session_id,
            user_id=base.user_id,
            version=max(base.version, overlay.version),
            updated_at=_utcnow_iso(),
            assets=merged_assets,
        )

    @staticmethod
    def _parse_inline_image_data_url(image_data_url: str) -> Optional[tuple[str, str, bytes]]:
        normalized = (image_data_url or "").strip()
        lowered = normalized.lower()
        for prefix, (mime_type, suffix) in _INLINE_IMAGE_PREFIXES.items():
            if lowered.startswith(prefix):
                b64_part = normalized.split(",", 1)[1] if "," in normalized else ""
                try:
                    payload = base64.b64decode(b64_part, validate=True)
                except binascii.Error as exc:
                    raise ValueError("image_data_urls 包含非法的 base64 图片数据") from exc
                return mime_type, suffix, payload
        return None
