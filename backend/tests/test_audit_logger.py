import os
import json
import logging
from types import SimpleNamespace
from uuid import uuid4

os.environ["DEBUG"] = "false"

import pytest

from utils import audit_logger


@pytest.mark.asyncio
async def test_record_user_prompt_event_writes_jsonl_by_date_and_user(monkeypatch, tmp_path):
    user_id = uuid4()
    monkeypatch.setattr(audit_logger.settings, "AUDIT_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(audit_logger.settings, "AUDIT_LOG_INCLUDE_PROMPTS", False)
    monkeypatch.setattr(audit_logger, "_last_pruned_on", None)

    await audit_logger.record_user_prompt_event(
        event_type="image2_prompt",
        user=SimpleNamespace(id=user_id, name="测试 用户", email="demo@example.com"),
        prompt="  一只发光的玻璃杯  ",
        metadata={"size": "1024x1024"},
    )

    files = list(tmp_path.glob("*/user-*.jsonl"))
    assert len(files) == 1
    assert str(user_id) in files[0].name
    assert "测试_用户" not in files[0].name

    record = json.loads(files[0].read_text(encoding="utf-8"))
    assert record["event_type"] == "image2_prompt"
    assert record["user"]["id"] == str(user_id)
    assert record["user"] == {"id": str(user_id)}
    assert "prompt" not in record
    assert record["prompt_length"] == len("一只发光的玻璃杯")
    assert len(record["prompt_fingerprint"]) == 64
    assert record["metadata"] == {"size": "1024x1024"}


@pytest.mark.asyncio
async def test_record_user_prompt_event_redacts_sensitive_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(audit_logger.settings, "AUDIT_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(audit_logger.settings, "AUDIT_LOG_INCLUDE_PROMPTS", True)
    monkeypatch.setattr(audit_logger, "_last_pruned_on", None)

    await audit_logger.record_user_prompt_event(
        event_type="chat_question",
        user=SimpleNamespace(id=uuid4()),
        prompt="allowed only by explicit setting",
        metadata={"nested": {"api_key": "secret", "model": "demo"}},
    )

    [log_file] = list(tmp_path.glob("*/user-*.jsonl"))
    record = json.loads(log_file.read_text(encoding="utf-8"))
    assert record["prompt"] == "allowed only by explicit setting"
    assert record["metadata"] == {
        "nested": {"api_key": "[REDACTED]", "model": "demo"},
    }


@pytest.mark.asyncio
async def test_record_user_prompt_event_ignores_and_redacts_write_failures(
    monkeypatch,
    caplog,
):
    marker = "private-filesystem-detail"

    async def fail_to_thread(*_args, **_kwargs):
        raise RuntimeError(marker)

    monkeypatch.setattr(audit_logger.asyncio, "to_thread", fail_to_thread)

    with caplog.at_level(logging.WARNING):
        await audit_logger.record_user_prompt_event(
            event_type="chat_question",
            user=SimpleNamespace(id=uuid4()),
            prompt="不会因为日志失败影响请求",
        )

    assert marker not in caplog.text
    assert "RuntimeError" in caplog.text
