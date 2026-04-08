"""Security helpers for model configuration encryption and signed runtime tokens."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
import uuid

from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt

from config.settings import settings

_MODEL_CONFIG_TOKEN_PURPOSE = "runtime_model_binding"


def _get_fernet() -> Fernet:
    return Fernet(settings.model_config_fernet_key.encode("utf-8"))


def encrypt_api_key(api_key: str) -> str:
    normalized = str(api_key or "").strip()
    if not normalized:
        raise ValueError("API Key 不能为空")
    return _get_fernet().encrypt(normalized.encode("utf-8")).decode("utf-8")


def decrypt_api_key(ciphertext: str) -> str:
    try:
        return _get_fernet().decrypt(str(ciphertext or "").encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("模型 API Key 解密失败") from exc


def mask_api_key(api_key: str) -> str:
    normalized = str(api_key or "").strip()
    if len(normalized) <= 8:
        return "*" * len(normalized)
    return f"{normalized[:4]}{'*' * max(4, len(normalized) - 8)}{normalized[-4:]}"


def create_runtime_model_binding_token(
    *,
    binding_id: str,
    user_id: str,
    thread_id: str | None = None,
) -> str:
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(seconds=settings.MODEL_CONFIG_TOKEN_EXPIRE_SECONDS)
    payload = {
        "purpose": _MODEL_CONFIG_TOKEN_PURPOSE,
        "binding_id": str(binding_id),
        "user_id": str(user_id),
        "thread_id": str(thread_id or "").strip() or None,
        "iat": issued_at,
        "exp": expires_at,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.model_config_encryption_secret, algorithm=settings.ALGORITHM)


def decode_runtime_model_binding_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.model_config_encryption_secret,
            algorithms=[settings.ALGORITHM],
        )
    except JWTError as exc:
        raise ValueError("模型绑定令牌无效或已过期") from exc

    if payload.get("purpose") != _MODEL_CONFIG_TOKEN_PURPOSE:
        raise ValueError("模型绑定令牌用途不匹配")
    return payload
