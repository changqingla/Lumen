import os
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

os.environ["DEBUG"] = "false"

import pytest

from modules.knowledge import controller as knowledge_controller
from utils import audit_logger


@pytest.mark.asyncio
async def test_knowledge_chat_records_question_to_audit_log(monkeypatch, tmp_path):
    user_id = uuid4()
    monkeypatch.setattr(audit_logger.settings, "AUDIT_LOG_DIR", str(tmp_path))

    search_service = MagicMock()
    search_service.search_in_kb = AsyncMock(
        return_value={"references": [{"doc_id": "doc-1"}]}
    )
    monkeypatch.setattr(
        knowledge_controller,
        "_create_search_service",
        lambda db: search_service,
    )

    response = await knowledge_controller.chat_with_kb(
        kbId="kb-1",
        request=knowledge_controller.KnowledgeChatSearchRequest(
            question="这篇文档的结论是什么？",
            top_n=3,
        ),
        current_user=SimpleNamespace(id=user_id, name="bob", email="bob@example.com"),
        db=object(),
    )

    assert response["references"] == [{"doc_id": "doc-1"}]
    search_service.search_in_kb.assert_awaited_once_with(
        "kb-1",
        str(user_id),
        "这篇文档的结论是什么？",
        top_n=3,
    )

    [log_file] = list(tmp_path.glob("*/user-*.jsonl"))
    record = json.loads(log_file.read_text(encoding="utf-8"))
    assert record["event_type"] == "knowledge_chat_question"
    assert record["user"]["id"] == str(user_id)
    assert record["prompt"] == "这篇文档的结论是什么？"
    assert record["metadata"] == {"kb_id": "kb-1", "top_n": 3}
