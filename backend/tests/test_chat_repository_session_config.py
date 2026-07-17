from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from modules.chat.repositories.chat_repository import ChatRepository


@pytest.mark.asyncio
async def test_session_ownership_query_does_not_join_message_history():
    session = SimpleNamespace(id=uuid4())
    result = MagicMock()
    result.scalar_one_or_none.return_value = session
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    repository = ChatRepository(db)

    loaded = await repository.get_session_for_user(uuid4(), uuid4())

    statement = db.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert loaded is session
    assert "chat_sessions.user_id" in sql
    assert "JOIN chat_messages" not in sql


@pytest.mark.asyncio
async def test_session_config_update_uses_atomic_jsonb_merge_and_ownership():
    session = SimpleNamespace(id=uuid4())
    result = MagicMock()
    result.scalar_one_or_none.return_value = session
    db = SimpleNamespace(
        execute=AsyncMock(return_value=result),
        commit=AsyncMock(),
    )
    repository = ChatRepository(db)

    updated = await repository.update_session_config(
        uuid4(),
        uuid4(),
        {"runtimeKnowledgeFiles": []},
    )

    statement = db.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert updated is session
    assert " || " in sql
    assert "chat_sessions.user_id" in sql
    assert "RETURNING chat_sessions" in sql
    db.commit.assert_awaited_once()
