# lumen 中使用的 LangChain 与 LangGraph 函数清单技术文档

## 1. 文档目标

这份文档的目标很明确：

- 列出这个项目中**直接使用到的** LangChain / LangGraph 生态 API
- 对重复导入进行去重
- 给出每个 API 在本项目中的用途
- 给出一个尽量简单的调用示例

这份文档适合以下场景：

- 新人快速熟悉项目的 Agent 技术栈
- 排查某个能力到底依赖 LangChain 还是 LangGraph
- 代码评审时快速确认某个 API 的职责
- 编写新模块时复用已有的框架模式

---

## 2. 统计口径

为了避免“列了很多其实没直接用到的方法”这种问题，本文采用以下统计口径：

- 统计范围：`backend/src` 中直接从 `langchain*`、`langchain_core*`、`langgraph*`、`langgraph_sdk`、`langchain_deepseek`、`langchain_mcp_adapters` 导入的符号
- 去重规则：按“`模块路径 + 符号名`”去重
- 包含内容：
  - 顶层函数
  - 装饰器
  - 工厂函数
  - 运行时类
  - 消息类
  - 类型/协议类
  - 持久化后端类
- 不包含内容：
  - 实例方法，例如 `.invoke()`、`.stream()`、`.setup()`、`.read_file()`
  - 项目自定义函数
  - 仅出现在注释或文档中的 API

也就是说，这份文档列出的不是“框架全部能力”，而是“这个项目当前代码里真实引入并使用的 LangChain / LangGraph 生态 API”。

---

## 3. 总览

按去重后的结果，这个项目当前使用到的 LangChain / LangGraph 生态 API 可以分为六组：

1. Agent 构建与中间件 API
2. Tool 定义与工具运行时 API
3. 消息与 Runnable API
4. LangGraph 运行时与控制流 API
5. Checkpointer 与持久化 API
6. 生态扩展 API

---

## 4. Agent 构建与中间件 API

## 4.1 `langchain.agents.create_agent`

**作用**：
创建项目的主 Agent 和子 Agent。它是整个系统 Agent loop 的入口工厂。

**本项目用途**：

- 主智能体：`runtimes/backend/src/agents/lead_agent/agent.py`
- 内嵌客户端：`runtimes/backend/src/client.py`
- 子 Agent 执行器：`runtimes/backend/src/subagents/executor.py`

**最小示例**：

```python
from langchain.agents import create_agent

agent = create_agent(
    model=my_model,
    tools=my_tools,
    middleware=my_middlewares,
    system_prompt="You are a helpful agent.",
    state_schema=MyState,
)
```

## 4.2 `langchain.agents.AgentState`

**作用**：
Agent 运行状态的基础类型。项目里的 `ThreadState` 就是在它之上扩展出来的。

**本项目用途**：

- 自定义线程状态
- 自定义中间件状态
- 沙箱状态扩展

**最小示例**：

```python
from typing import TypedDict
from langchain.agents import AgentState

class MyState(AgentState):
    title: str | None
```

## 4.3 `langchain.agents.middleware.AgentMiddleware`

**作用**：
自定义 Agent 中间件的基类。

**本项目用途**：

- `ThreadDataMiddleware`
- `UploadsMiddleware`
- `MemoryMiddleware`
- `ClarificationMiddleware`
- `ViewImageMiddleware`
- `SandboxMiddleware`

**最小示例**：

```python
from langchain.agents.middleware import AgentMiddleware

class MyMiddleware(AgentMiddleware):
    def before_agent(self, state, runtime):
        return {"extra": "value"}
```

## 4.4 `langchain.agents.middleware.SummarizationMiddleware`

**作用**：
在上下文过长时自动进行会话摘要压缩。

**本项目用途**：

- 主 Agent 长对话时裁剪消息窗口

**最小示例**：

```python
from langchain.agents.middleware import SummarizationMiddleware

middleware = SummarizationMiddleware(
    model="gpt-4o-mini",
    trigger=("tokens", 12000),
    keep=("messages", 10),
)
```

## 4.5 `langchain.agents.middleware.TodoListMiddleware`

**作用**：
为计划模式提供待办列表能力。

**本项目用途**：

- 项目自定义 `TodoMiddleware` 继承它来增强 plan mode

**最小示例**：

```python
from langchain.agents.middleware import TodoListMiddleware

todo_middleware = TodoListMiddleware(
    system_prompt="Track tasks carefully."
)
```

## 4.6 `langchain.agents.middleware.todo.PlanningState`

**作用**：
计划模式状态类型，通常包含 todo 列表等规划信息。

**本项目用途**：

- `TodoMiddleware` 的状态声明

**最小示例**：

```python
from langchain.agents.middleware.todo import PlanningState

def process_plan(state: PlanningState):
    todos = state.get("todos") or []
```

## 4.7 `langchain.agents.middleware.todo.Todo`

**作用**：
单条待办项的数据结构。

**本项目用途**：

- 待办列表格式化与状态判断

**最小示例**：

```python
from langchain.agents.middleware.todo import Todo

todo: Todo = {"content": "Write docs", "status": "in_progress"}
```

## 4.8 `langchain.agents.middleware.types.ModelRequest`

**作用**：
模型调用前的请求对象。

**本项目用途**：

- `DanglingToolCallMiddleware` 中读取和修补模型输入消息

**最小示例**：

```python
from langchain.agents.middleware.types import ModelRequest

def before_model(request: ModelRequest):
    patched = request.override(messages=request.messages)
    return patched
```

## 4.9 `langchain.agents.middleware.types.ModelResponse`

**作用**：
模型调用后的响应对象。

**本项目用途**：

- `DanglingToolCallMiddleware` 中处理模型输出

**最小示例**：

```python
from langchain.agents.middleware.types import ModelResponse

def after_model(response: ModelResponse):
    return response
```

## 4.10 `langchain.agents.middleware.types.ModelCallResult`

**作用**：
模型中间件调用链中的结果类型。

**本项目用途**：

- `DanglingToolCallMiddleware` 的类型约束

**最小示例**：

```python
from langchain.agents.middleware.types import ModelCallResult

def after_model(...) -> ModelCallResult:
    ...
```

---

## 5. Tool 定义与工具运行时 API

## 5.1 `langchain.tools.tool`

**作用**：
把普通 Python 函数包装成 LangChain 工具。

**本项目用途**：

- 社区工具：搜索、抓取、图片搜索等
- 内置工具：`present_files`、`task`、`view_image`
- 沙箱工具：`read_file`、`write_file`、`str_replace`

**最小示例**：

```python
from langchain.tools import tool

@tool("say_hello", parse_docstring=False)
def say_hello(name: str) -> str:
    return f"Hello, {name}"
```

## 5.2 `langchain_core.tools.tool`

**作用**：
和 `langchain.tools.tool` 类似，也是工具装饰器。项目里用于某些内置工具定义。

**本项目用途**：

- `setup_agent_tool`

**最小示例**：

```python
from langchain_core.tools import tool

@tool
def ping() -> str:
    return "pong"
```

## 5.3 `langchain.tools.BaseTool`

**作用**：
所有工具对象的基类。

**本项目用途**：

- 工具集合拼装
- 子 Agent 工具白名单/黑名单

**最小示例**：

```python
from langchain.tools import BaseTool

def load_tools() -> list[BaseTool]:
    return []
```

## 5.4 `langchain_core.tools.BaseTool`

**作用**：
`langchain_core` 中的工具抽象基类。

**本项目用途**：

- MCP 工具缓存与类型约束

**最小示例**：

```python
from langchain_core.tools import BaseTool

cached_tools: list[BaseTool] = []
```

## 5.5 `langchain.tools.ToolRuntime`

**作用**：
工具执行时的运行上下文，允许访问状态、上下文、配置等。

**本项目用途**：

- 沙箱工具
- `present_files`
- `task`
- `view_image`

**最小示例**：

```python
from langchain.tools import ToolRuntime, tool

@tool
def my_tool(runtime: ToolRuntime, text: str) -> str:
    thread_id = runtime.context.get("thread_id")
    return f"{thread_id}: {text}"
```

## 5.6 `langchain.tools.InjectedToolCallId`

**作用**：
把当前 tool call 的 ID 注入到工具参数里。

**本项目用途**：

- 构造 `ToolMessage`
- 精确回填工具调用结果

**最小示例**：

```python
from typing import Annotated
from langchain.tools import InjectedToolCallId, tool

@tool
def my_tool(tool_call_id: Annotated[str, InjectedToolCallId]) -> str:
    return f"tool_call_id={tool_call_id}"
```

## 5.7 `langgraph.prebuilt.ToolRuntime`

**作用**：
LangGraph 预构建工具节点环境里的运行时类型。

**本项目用途**：

- `setup_agent_tool`

**最小示例**：

```python
from langgraph.prebuilt import ToolRuntime

def setup(runtime: ToolRuntime):
    return runtime.context
```

---

## 6. 消息与 Runnable API

## 6.1 `langchain_core.messages.HumanMessage`

**作用**：
表示用户消息。

**本项目用途**：

- 客户端构造输入消息
- 上传中间件修改用户消息
- todo 提醒注入

**最小示例**：

```python
from langchain_core.messages import HumanMessage

msg = HumanMessage(content="帮我总结这个文档")
```

## 6.2 `langchain_core.messages.AIMessage`

**作用**：
表示模型输出消息，可能包含文本和 tool calls。

**本项目用途**：

- 流式输出序列化
- 子 Agent 执行结果收集
- DeepSeek patch 兼容

**最小示例**：

```python
from langchain_core.messages import AIMessage

msg = AIMessage(content="已完成")
```

## 6.3 `langchain_core.messages.SystemMessage`

**作用**：
表示系统消息。

**本项目用途**：

- 客户端消息序列化

**最小示例**：

```python
from langchain_core.messages import SystemMessage

msg = SystemMessage(content="You are a helpful assistant.")
```

## 6.4 `langchain_core.messages.ToolMessage`

**作用**：
表示工具执行后的消息结果。

**本项目用途**：

- 中间件回填工具结果
- `present_files` / `view_image` / `setup_agent` 返回结果
- 客户端流式消息序列化

**最小示例**：

```python
from langchain_core.messages import ToolMessage

msg = ToolMessage(content="Tool completed", tool_call_id="call_123")
```

## 6.5 `langchain_core.runnables.RunnableConfig`

**作用**：
LangChain Runnable 的运行时配置对象。

**本项目用途**：

- 传递 `thread_id`
- 指定模型名
- 打开/关闭 thinking、subagent、plan mode

**最小示例**：

```python
from langchain_core.runnables import RunnableConfig

config = RunnableConfig(
    configurable={
        "thread_id": "thread-1",
        "model_name": "gpt-4o",
    },
    recursion_limit=100,
)
```

## 6.6 `langchain_core.language_models.LanguageModelInput`

**作用**：
表示模型输入类型。

**本项目用途**：

- `patched_deepseek.py` 中覆写模型调用逻辑

**最小示例**：

```python
from langchain_core.language_models import LanguageModelInput

def invoke(input_: LanguageModelInput):
    ...
```

## 6.7 `langchain.chat_models.BaseChatModel`

**作用**：
聊天模型基类。

**本项目用途**：

- 模型工厂中的类型约束

**最小示例**：

```python
from langchain.chat_models import BaseChatModel

def build_model() -> BaseChatModel:
    ...
```

## 6.8 `langchain_core.tracers.langchain.LangChainTracer`

**作用**：
LangChain 链路追踪器。

**本项目用途**：

- 模型工厂中接入 tracing

**最小示例**：

```python
from langchain_core.tracers.langchain import LangChainTracer

tracer = LangChainTracer(project_name="lumen")
```

---

## 7. LangGraph 运行时与控制流 API

## 7.1 `langgraph.runtime.Runtime`

**作用**：
LangGraph 节点/中间件运行时对象，可读取上下文。

**本项目用途**：

- 所有中间件通过它读取 `thread_id`
- 读取运行时上下文信息

**最小示例**：

```python
from langgraph.runtime import Runtime

def before_agent(state, runtime: Runtime):
    thread_id = runtime.context.get("thread_id")
```

## 7.2 `langgraph.types.Command`

**作用**：
声明式返回状态更新和控制流跳转。

**本项目用途**：

- `present_files`
- `view_image`
- `setup_agent`
- `ClarificationMiddleware`

**最小示例**：

```python
from langgraph.types import Command

return Command(update={"artifacts": ["/mnt/user-data/outputs/report.md"]})
```

## 7.3 `langgraph.graph.END`

**作用**：
表示图执行结束。

**本项目用途**：

- 澄清中间件在需要用户确认时中断本轮执行

**最小示例**：

```python
from langgraph.graph import END
from langgraph.types import Command

return Command(update={"messages": [...]}, goto=END)
```

## 7.4 `langgraph.typing.ContextT`

**作用**：
工具运行时上下文类型参数。

**本项目用途**：

- 工具函数泛型类型标注

**最小示例**：

```python
from langgraph.typing import ContextT
from langchain.tools import ToolRuntime

def tool_fn(runtime: ToolRuntime[ContextT, dict]):
    ...
```

## 7.5 `langgraph.prebuilt.tool_node.ToolCallRequest`

**作用**：
表示工具调用请求对象。

**本项目用途**：

- `ClarificationMiddleware` 中拦截工具调用

**最小示例**：

```python
from langgraph.prebuilt.tool_node import ToolCallRequest

def wrap_tool_call(request: ToolCallRequest, handler):
    return handler(request)
```

## 7.6 `langgraph.config.get_stream_writer`

**作用**：
获取流式写入器，在工具执行期间向前端发送自定义状态消息。

**本项目用途**：

- `task_tool` 中实时推送子任务状态

**最小示例**：

```python
from langgraph.config import get_stream_writer

writer = get_stream_writer()
writer({"message": "Subtask started"})
```

## 7.7 `langgraph.types.Checkpointer`

**作用**：
Checkpointer 抽象类型。

**本项目用途**：

- 同步/异步 checkpointer provider 的类型定义

**最小示例**：

```python
from langgraph.types import Checkpointer

def use_checkpointer(cp: Checkpointer):
    return cp
```

---

## 8. Checkpointer 与持久化 API

## 8.1 `langgraph.checkpoint.memory.InMemorySaver`

**作用**：
内存态 checkpointer。

**本项目用途**：

- 未配置持久化后端时的默认回退方案

**最小示例**：

```python
from langgraph.checkpoint.memory import InMemorySaver

checkpointer = InMemorySaver()
```

## 8.2 `langgraph.checkpoint.sqlite.SqliteSaver`

**作用**：
同步 SQLite checkpointer。

**本项目用途**：

- 同步 provider 中的 SQLite 后端

**最小示例**：

```python
from langgraph.checkpoint.sqlite import SqliteSaver

with SqliteSaver.from_conn_string("store.db") as saver:
    saver.setup()
```

## 8.3 `langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver`

**作用**：
异步 SQLite checkpointer。

**本项目用途**：

- LangGraph Server 异步环境下的 SQLite 持久化

**最小示例**：

```python
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

async with AsyncSqliteSaver.from_conn_string("store.db") as saver:
    await saver.setup()
```

## 8.4 `langgraph.checkpoint.postgres.PostgresSaver`

**作用**：
同步 PostgreSQL checkpointer。

**本项目用途**：

- 同步 provider 中的 Postgres 后端

**最小示例**：

```python
from langgraph.checkpoint.postgres import PostgresSaver

with PostgresSaver.from_conn_string("postgresql://...") as saver:
    saver.setup()
```

## 8.5 `langgraph.checkpoint.postgres.aio.AsyncPostgresSaver`

**作用**：
异步 PostgreSQL checkpointer。

**本项目用途**：

- LangGraph Server 异步环境下的 Postgres 持久化

**最小示例**：

```python
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

async with AsyncPostgresSaver.from_conn_string("postgresql://...") as saver:
    await saver.setup()
```

---

## 9. 生态扩展 API

这一组不属于 LangChain / LangGraph 主包本身，但属于本项目实际使用到的 LangChain / LangGraph 生态扩展。

## 9.1 `langchain_deepseek.ChatDeepSeek`

**作用**：
DeepSeek 聊天模型适配器。

**本项目用途**：

- `patched_deepseek.py` 中对 DeepSeek payload 做兼容修补

**最小示例**：

```python
from langchain_deepseek import ChatDeepSeek

model = ChatDeepSeek(model="deepseek-chat")
```

## 9.2 `langchain_mcp_adapters.client.MultiServerMCPClient`

**作用**：
MCP 多服务端客户端，统一加载多个 MCP server 的工具。

**本项目用途**：

- MCP 工具初始化

**最小示例**：

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({
    "figma": {"command": "npx", "args": ["-y", "some-mcp-server"]}
})
```

## 9.3 `langgraph_sdk.get_client`

**作用**：
获取 LangGraph SDK 客户端。

**本项目用途**：

- 渠道管理器中与 LangGraph 服务交互

**最小示例**：

```python
from langgraph_sdk import get_client

client = get_client(url="http://localhost:2024")
```

---

## 10. 最终去重清单

下面给出按“模块路径 + 符号名”去重后的完整列表：

### 10.1 LangChain / LangChain Core

- `langchain.agents.create_agent`
- `langchain.agents.AgentState`
- `langchain.agents.middleware.AgentMiddleware`
- `langchain.agents.middleware.SummarizationMiddleware`
- `langchain.agents.middleware.TodoListMiddleware`
- `langchain.agents.middleware.todo.PlanningState`
- `langchain.agents.middleware.todo.Todo`
- `langchain.agents.middleware.types.ModelRequest`
- `langchain.agents.middleware.types.ModelResponse`
- `langchain.agents.middleware.types.ModelCallResult`
- `langchain.chat_models.BaseChatModel`
- `langchain.tools.tool`
- `langchain.tools.BaseTool`
- `langchain.tools.ToolRuntime`
- `langchain.tools.InjectedToolCallId`
- `langchain_core.tools.tool`
- `langchain_core.tools.BaseTool`
- `langchain_core.messages.HumanMessage`
- `langchain_core.messages.AIMessage`
- `langchain_core.messages.SystemMessage`
- `langchain_core.messages.ToolMessage`
- `langchain_core.runnables.RunnableConfig`
- `langchain_core.language_models.LanguageModelInput`
- `langchain_core.tracers.langchain.LangChainTracer`

### 10.2 LangGraph

- `langgraph.runtime.Runtime`
- `langgraph.types.Command`
- `langgraph.types.Checkpointer`
- `langgraph.graph.END`
- `langgraph.typing.ContextT`
- `langgraph.prebuilt.ToolRuntime`
- `langgraph.prebuilt.tool_node.ToolCallRequest`
- `langgraph.config.get_stream_writer`
- `langgraph.checkpoint.memory.InMemorySaver`
- `langgraph.checkpoint.sqlite.SqliteSaver`
- `langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver`
- `langgraph.checkpoint.postgres.PostgresSaver`
- `langgraph.checkpoint.postgres.aio.AsyncPostgresSaver`

### 10.3 生态扩展

- `langchain_deepseek.ChatDeepSeek`
- `langchain_mcp_adapters.client.MultiServerMCPClient`
- `langgraph_sdk.get_client`

---

## 11. 如何读这份清单

如果你站在项目架构层面理解，可以把这些 API 简单映射成下面四层：

- **Agent 构建层**
  - `create_agent`
  - `AgentState`
  - `AgentMiddleware`
  - `SummarizationMiddleware`
  - `TodoListMiddleware`

- **工具层**
  - `tool`
  - `ToolRuntime`
  - `InjectedToolCallId`
  - `Command`

- **消息与状态层**
  - `HumanMessage`
  - `AIMessage`
  - `ToolMessage`
  - `RunnableConfig`
  - `Runtime`

- **持久化与服务接入层**
  - `Checkpointer`
  - `InMemorySaver`
  - `SqliteSaver`
  - `AsyncSqliteSaver`
  - `PostgresSaver`
  - `AsyncPostgresSaver`
  - `get_client`

这样在看 lumen 源码时，就比较容易迅速判断：

- 这是在定义 Agent
- 这是在定义 Tool
- 这是在改消息状态
- 这是在处理持久化
- 这是在接外部服务

---

## 12. 一句话总结

如果把整个项目里用到的 LangChain / LangGraph 生态 API 压缩成一句话，可以这样说：

> lumen 主要使用 LangChain 来构建 Agent、消息和工具抽象，使用 LangGraph 来承载运行时、控制流和状态持久化，再通过少量生态扩展包接入特定模型、MCP 服务和 LangGraph SDK。

这也是这个项目技术栈最核心的落点。
