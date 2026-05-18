import os
import json
from types import SimpleNamespace
from uuid import uuid4

os.environ["DEBUG"] = "false"

import pytest

from utils import audit_logger


@pytest.mark.asyncio
async def test_record_user_prompt_event_writes_jsonl_by_date_and_user(monkeypatch, tmp_path):
    user_id = uuid4()
    monkeypatch.setattr(audit_logger.settings, "AUDIT_LOG_DIR", str(tmp_path))

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
    assert record["user"]["name"] == "测试 用户"
    assert "email" not in record["user"]
    assert record["prompt"] == "一只发光的玻璃杯"
    assert record["metadata"] == {"size": "1024x1024"}


@pytest.mark.asyncio
async def test_record_user_prompt_event_ignores_write_failures(monkeypatch):
    monkeypatch.setattr(audit_logger.settings, "AUDIT_LOG_DIR", "/dev/null/audit")

    await audit_logger.record_user_prompt_event(
        event_type="chat_question",
        user=SimpleNamespace(id=uuid4()),
        prompt="不会因为日志失败影响请求",
    )
