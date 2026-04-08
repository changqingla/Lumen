import os

os.environ.setdefault("DEBUG", "false")

from modules.chat.services.insight_runtime_service import InsightRuntimeService


def test_build_run_request_template_uses_messages_tuple_stream_mode():
    service = InsightRuntimeService()

    payload = service.build_run_request_template(
        thread_id="thread-123",
        assistant_id="assistant-123",
        model_name="gpt-5.4",
        thinking_enabled=True,
        is_plan_mode=False,
    )

    assert payload["stream_mode"] == ["messages-tuple", "values", "custom"]
