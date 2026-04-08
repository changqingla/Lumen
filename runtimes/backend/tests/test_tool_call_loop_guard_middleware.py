import asyncio

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agents.middlewares.tool_call_loop_guard_middleware import ToolCallLoopGuardMiddleware


def _request(messages, tool_call):
    return ToolCallRequest(
        tool_call=tool_call,
        tool=None,
        state={"messages": messages},
        runtime=None,
    )


def test_wrap_tool_call_blocks_repeated_identical_calls_in_same_turn():
    middleware = ToolCallLoopGuardMiddleware(max_identical_calls=2)
    messages = [
        HumanMessage(content="Please update the file"),
        AIMessage(content="", tool_calls=[{"id": "tc-1", "name": "write_file", "args": {"path": "/tmp/a.txt", "content": "hello"}}]),
        ToolMessage(content="OK", tool_call_id="tc-1", name="write_file"),
        AIMessage(content="", tool_calls=[{"id": "tc-2", "name": "write_file", "args": {"path": "/tmp/a.txt", "content": "hello"}}]),
        ToolMessage(content="OK", tool_call_id="tc-2", name="write_file"),
        AIMessage(content="", tool_calls=[{"id": "tc-3", "name": "write_file", "args": {"path": "/tmp/a.txt", "content": "hello"}}]),
    ]
    request = _request(messages, {"id": "tc-3", "name": "write_file", "args": {"path": "/tmp/a.txt", "content": "hello"}})

    result = middleware.wrap_tool_call(
        request,
        handler=lambda req: ToolMessage(content="should not run", tool_call_id=req.tool_call["id"], name=req.tool_call["name"]),
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "Repeated identical tool call blocked" in str(result.content)


def test_wrap_tool_call_allows_same_tool_with_different_args():
    middleware = ToolCallLoopGuardMiddleware(max_identical_calls=1)
    messages = [
        HumanMessage(content="Please read files"),
        AIMessage(content="", tool_calls=[{"id": "tc-1", "name": "read_file", "args": {"path": "/tmp/a.txt"}}]),
        ToolMessage(content="a", tool_call_id="tc-1", name="read_file"),
        AIMessage(content="", tool_calls=[{"id": "tc-2", "name": "read_file", "args": {"path": "/tmp/b.txt"}}]),
    ]
    request = _request(messages, {"id": "tc-2", "name": "read_file", "args": {"path": "/tmp/b.txt"}})

    result = middleware.wrap_tool_call(
        request,
        handler=lambda req: ToolMessage(content="b", tool_call_id=req.tool_call["id"], name=req.tool_call["name"]),
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "success"
    assert result.content == "b"


def test_wrap_tool_call_does_not_count_previous_turns():
    middleware = ToolCallLoopGuardMiddleware(max_identical_calls=1)
    messages = [
        HumanMessage(content="Turn 1"),
        AIMessage(content="", tool_calls=[{"id": "tc-1", "name": "bash", "args": {"command": "pwd"}}]),
        ToolMessage(content="/tmp", tool_call_id="tc-1", name="bash"),
        HumanMessage(content="Turn 2"),
        AIMessage(content="", tool_calls=[{"id": "tc-2", "name": "bash", "args": {"command": "pwd"}}]),
    ]
    request = _request(messages, {"id": "tc-2", "name": "bash", "args": {"command": "pwd"}})

    result = middleware.wrap_tool_call(
        request,
        handler=lambda req: ToolMessage(content="/workspace", tool_call_id=req.tool_call["id"], name=req.tool_call["name"]),
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "success"
    assert result.content == "/workspace"


def test_awrap_tool_call_blocks_repeated_identical_calls():
    middleware = ToolCallLoopGuardMiddleware(max_identical_calls=1)
    messages = [
        HumanMessage(content="Repeat"),
        AIMessage(content="", tool_calls=[{"id": "tc-1", "name": "ls", "args": {"path": "/tmp"}}]),
        ToolMessage(content="a.txt", tool_call_id="tc-1", name="ls"),
        AIMessage(content="", tool_calls=[{"id": "tc-2", "name": "ls", "args": {"path": "/tmp"}}]),
    ]
    request = _request(messages, {"id": "tc-2", "name": "ls", "args": {"path": "/tmp"}})

    async def _run():
        return await middleware.awrap_tool_call(
            request,
            handler=lambda req: asyncio.sleep(0, result=ToolMessage(content="should not run", tool_call_id=req.tool_call["id"], name=req.tool_call["name"])),
        )

    result = asyncio.run(_run())

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
