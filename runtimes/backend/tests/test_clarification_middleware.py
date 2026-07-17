"""Privacy regressions for clarification interception."""

from unittest.mock import MagicMock

from src.agents.middlewares.clarification_middleware import ClarificationMiddleware


def test_clarification_question_is_not_printed(capsys):
    question = "private clarification body"
    request = MagicMock(
        tool_call={
            "id": "call-1",
            "name": "ask_clarification",
            "args": {"question": question},
        }
    )

    result = ClarificationMiddleware()._handle_clarification(request)

    assert result is not None
    captured = capsys.readouterr()
    assert question not in captured.out
    assert question not in captured.err
