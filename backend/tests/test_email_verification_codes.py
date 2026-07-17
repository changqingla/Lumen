import os
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ["DEBUG"] = "false"

import pytest

from infrastructure.services import email_service as email_service_module
from infrastructure.services.email_service import EmailService


def test_verification_keys_are_scoped_by_purpose_and_normalized():
    assert EmailService._verification_key(" User@Example.COM ", "register") == (
        "verify_code:register:user@example.com"
    )
    assert EmailService._verification_key("user@example.com", "reset") == (
        "verify_code:reset:user@example.com"
    )

    with pytest.raises(ValueError, match="Unsupported"):
        EmailService._verification_key("user@example.com", "login")


@pytest.mark.asyncio
async def test_verify_code_uses_atomic_single_use_script(monkeypatch):
    redis = AsyncMock()
    redis.eval.return_value = 1
    monkeypatch.setattr(email_service_module, "get_redis_client", AsyncMock(return_value=redis))

    result = await EmailService().verify_code("User@Example.com", "123456", "reset")

    assert result is True
    args = redis.eval.await_args.args
    assert args[1:] == (1, "verify_code:reset:user@example.com", "123456")
    redis.get.assert_not_awaited()
    redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_delivery_never_logs_or_accepts_debug_code(monkeypatch, caplog):
    marker = "private-verification-code-482913"
    redis = AsyncMock()
    service = EmailService()
    monkeypatch.setattr(service, "_generate_code", lambda: marker)
    monkeypatch.setattr(service, "_send_email_smtp", lambda *_args: False)
    monkeypatch.setattr(email_service_module, "get_redis_client", AsyncMock(return_value=redis))

    async def immediate_to_thread(function, *args):
        return function(*args)

    monkeypatch.setattr(email_service_module.asyncio, "to_thread", immediate_to_thread)

    with caplog.at_level(logging.WARNING):
        result = await service.send_verification_code(
            "private-user@example.test",
            "register",
        )

    assert result is False
    redis.delete.assert_awaited_once_with(
        "verify_code:register:private-user@example.test"
    )
    assert marker not in caplog.text
    assert "private-user@example.test" not in caplog.text


def test_smtp_exception_log_contains_only_error_type(monkeypatch, caplog):
    marker = "private-smtp-provider-detail"

    def fail_smtp(*_args, **_kwargs):
        raise RuntimeError(marker)

    monkeypatch.setattr(email_service_module.smtplib, "SMTP_SSL", fail_smtp)
    monkeypatch.setattr(
        email_service_module,
        "settings",
        SimpleNamespace(
            SMTP_FROM_NAME="Lumen",
            SMTP_USERNAME="sender@example.test",
            SMTP_PASSWORD="private-password",
            SMTP_USE_SSL=True,
            SMTP_HOST="smtp.example.test",
            SMTP_PORT=465,
            SMTP_TIMEOUT=1,
        ),
    )

    with caplog.at_level(logging.ERROR):
        result = EmailService()._send_email_smtp(
            "private-recipient@example.test",
            "subject",
            "body",
        )

    assert result is False
    assert marker not in caplog.text
    assert "private-recipient@example.test" not in caplog.text
    assert "RuntimeError" in caplog.text
