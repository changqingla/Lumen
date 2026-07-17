# LangChain 内置中间件与计划状态技术文档

## 1. 文档目标

本文聚焦 lumen 中几类直接依赖 LangChain 内置实现的能力：

- `AgentMiddleware`
- `SummarizationMiddleware`
- `TodoListMiddleware`
- `PlanningState`
- `Todo`
- `ModelRequest`
- `ModelResponse`
- `ModelCallResult`

重点不是做 API 手册式罗列，而是把它们的实现算法、执行顺序、状态流转和在本项目中的接入方式讲清楚。读完后，应该能回答下面这些问题：

- LangChain 的 middleware 在 agent loop 中到底是怎么跑起来的
- `SummarizationMiddleware` 是如何判断“该压缩了”，又是如何裁切消息的
- `TodoListMiddleware` 为什么不是普通工具，而是中间件自带能力
- `PlanningState` 中的 `todos` 为什么能跨多轮保留，又为什么不依赖消息文本本身
- `Todo` 为什么是 todo 系统里的最小原子单元
- `ModelRequest`、`ModelResponse`、`ModelCallResult` 在 `wrap_model_call` 协议里分别扮演什么角色
- lumen 为什么还要在 LangChain 原生 todo 中间件外面再包一层自定义扩展

## 2. 相关源码位置

先把最关键的源码入口列出来，便于后续对照。

- LangChain 基类与状态定义
  - `backend/.venv/lib/python3.12/site-packages/langchain/agents/middleware/types.py`
- LangChain 摘要中间件
  - `backend/.venv/lib/python3.12/site-packages/langchain/agents/middleware/summarization.py`
- LangChain Todo 中间件与计划状态
  - `backend/.venv/lib/python3.12/site-packages/langchain/agents/middleware/todo.py`
- LangChain Agent 工厂与 middleware 编排
  - `backend/.venv/lib/python3.12/site-packages/langchain/agents/factory.py`
- lumen 对摘要中间件的配置适配
  - `runtimes/backend/src/config/summarization_config.py`
  - `runtimes/backend/src/config/app_config.py`
  - `runtimes/backend/src/agents/lead_agent/agent.py`
- lumen 对 Todo 中间件的扩展
  - `runtimes/backend/src/agents/middlewares/todo_middleware.py`
  - `runtimes/backend/src/agents/thread_state.py`

## 3. 先说结论

这几个类之间的关系可以先压缩成一句话：

`AgentMiddleware` 是通用执行协议，`SummarizationMiddleware` 和 `TodoListMiddleware` 是两种具体策略，`PlanningState` 是 Todo 策略扩展出来的一块状态面。

如果再翻译成运行时语言，就是：

- `AgentMiddleware` 定义“可以在哪些时机介入 agent loop”
- `SummarizationMiddleware` 在模型调用前改写 `messages`
- `TodoListMiddleware` 在模型调用前后同时工作，既给模型注入任务管理规则，又给 agent 动态注入 `write_todos` 工具
- `PlanningState` 让 `todos` 不只是消息文本，而是线程状态的一部分

## 4. AgentMiddleware 的底层执行模型

### 4.1 它本质上是一个 hook 协议，不是一个独立节点类型

`AgentMiddleware` 定义在 `types.py` 中，它是所有 agent middleware 的基类。它自己几乎不做业务逻辑，真正重要的是它暴露了一组 hook：

- `before_agent`
- `before_model`
- `after_model`
- `after_agent`
- `wrap_model_call`
- `wrap_tool_call`

其中最重要的区别是：

- `before_*` / `after_*` 是“状态更新型 hook”，返回 `dict[str, Any] | None`
- `wrap_*` 是“包裹执行型 hook”，它可以修改请求、重试、短路、替换响应，甚至完全不调用底层 handler

也就是说，LangChain 把中间件分成了两种作用方式：

- 改状态
- 改执行过程

### 4.2 AgentState 是所有 middleware 的最小公共状态

`AgentState` 在 `types.py` 中定义，基础字段有三个：

- `messages`
- `jump_to`
- `structured_response`

其中：

- `messages` 是主消息历史，带 `add_messages` reducer
- `jump_to` 是内部控制流字段，用于让 middleware 改写接下来的跳转目标
- `structured_response` 用于结构化输出，不在输入 schema 中暴露

lumen 自己的 `ThreadState` 就是在这个基础上继续加字段，比如：

- `sandbox`
- `thread_data`
- `title`
- `artifacts`
- `todos`
- `uploaded_files`
- `viewed_images`

所以从架构上讲，middleware 操作的不是“散落的局部变量”，而是一份统一的线程状态对象。

### 4.3 middleware 是如何被挂到图上的

LangChain 的 `create_agent()` 在 `factory.py` 里做了几件关键事：

1. 扫描所有 middleware，按它们实现了哪些 hook 进行分类。
2. 把各 middleware 的 `state_schema` 合并成一份总状态 schema。
3. 把各 middleware 的 `tools` 收集起来，和普通工具一起交给 `ToolNode`。
4. 把 `before_agent`、`before_model`、`after_model`、`after_agent` 变成图节点。
5. 把 `wrap_model_call` / `wrap_tool_call` 组合成一层层 wrapper。

这意味着 middleware 不是“外面包一个装饰器”那么简单，而是被正式接进 LangGraph 状态机里。

### 4.4 执行顺序是什么

对单个 agent loop 来说，可以把执行顺序抽象成下面这样：

```text
启动一次请求
-> before_agent 按声明顺序执行一次
-> 进入循环
   -> before_model 按声明顺序执行
   -> wrap_model_call 按声明顺序从外到内包裹
   -> 调用模型
   -> after_model 按声明逆序执行
   -> 如果 AIMessage 有 tool_calls
      -> 调工具
      -> 回到 before_model
   -> 否则结束循环
-> after_agent 按声明逆序执行一次
-> END
```

这里有两个非常关键的性质：

- `before_model` 是顺序正向执行的
- `after_model` 是逆序执行的

这和很多 Web 框架里的 middleware 链很像，原因也一样：前置阶段逐层准备，后置阶段逐层回收。

### 4.5 wrap_model_call 的算法意义

`wrap_model_call` 和 `before_model` 最大的不同，不是时机，而是权力。

`before_model` 只能返回一个状态增量，让后续节点去消费。

`wrap_model_call` 则拿到了：

- `ModelRequest`
- 下一个 handler

因此它可以做这些事：

- 改写系统提示词
- 动态增删工具
- 替换模型实例
- 做重试
- 做缓存
- 短路返回

LangChain 在 `factory.py` 里把多个 `wrap_model_call` 组合成一条链，并明确规定：

- 声明顺序靠前的 middleware 是外层 wrapper
- 声明顺序靠后的 middleware 是内层 wrapper

这意味着越靠前的 middleware，越像“最外层控制器”。

## 5. SummarizationMiddleware 的实现算法

### 5.1 它的职责不是“生成摘要”，而是“在上下文将满时重写消息历史”

`SummarizationMiddleware` 的核心目标不是做总结本身，而是做消息替换。

它的工作结果不是多加一条摘要消息这么简单，而是：

- 把旧消息整体删掉
- 插入一条新的摘要消息
- 保留一段最近的原始消息尾部

所以它的真正职责是：

在保持最近上下文可见的前提下，把较早历史压缩成一条新的“摘要型消息”。

### 5.2 初始化参数的含义

它最关键的几个参数是：

- `model`
  用哪个模型来生成摘要
- `trigger`
  何时触发摘要
- `keep`
  摘要后保留多少最近上下文
- `token_counter`
  如何估算 token
- `summary_prompt`
  用什么提示词生成摘要
- `trim_tokens_to_summarize`
  在调用摘要模型之前，最多给它喂多少 token 的待摘要消息

lumen 在 `agent.py` 里做了一层适配，把这些参数改造成了配置项。

### 5.3 触发判断算法

触发逻辑在 `_should_summarize()` 中，采用的是“任一条件满足即触发”的策略。

`trigger` 支持三种规格：

- `("messages", N)`
  当消息条数达到 `N`
- `("tokens", N)`
  当估算 token 数达到 `N`
- `("fraction", x)`
  当估算 token 数达到模型最大输入 token 的 `x`

若传入的是列表，则是 OR 关系，不是 AND。

算法可以写成：

```text
for 每个 trigger 条件:
  如果是 messages 且 len(messages) >= N:
    触发
  如果是 tokens 且 total_tokens >= N:
    触发
  如果是 fraction 且 total_tokens >= max_input_tokens * x:
    触发
  如果模型返回了 usage_metadata.total_tokens，且它超过阈值:
    也触发
否则不触发
```

这里还有一个细节很重要：它不只依赖本地近似 token 计数，还会尝试读取最后一条 `AIMessage` 中的 `usage_metadata.total_tokens`。这是一种“双通道判定”：

- 能靠近似计数就靠近似计数
- 如果模型供应商真的回传了使用量，也会拿来参与判定

### 5.4 cutoff 是怎么计算的

一旦决定要摘要，就要回答另一个问题：

应该把哪一段历史压缩掉，哪一段留下？

这个分界点就是 `cutoff_index`。

`keep` 也支持三种规格：

- `("messages", N)`
- `("tokens", N)`
- `("fraction", x)`

算法分两类。

#### 第一类：按消息条数保留

如果 `keep=("messages", N)`，逻辑比较直接：

1. 目标是保留最后 `N` 条消息。
2. 先算出理论分界点 `len(messages) - N`。
3. 然后调用 `_find_safe_cutoff_point()` 做安全修正。

#### 第二类：按 token 数保留

如果 `keep=("tokens", N)` 或 `keep=("fraction", x)`，它不会逐条线性试探，而是使用二分查找：

1. 先换算出目标保留 token 数。
2. 判断整段消息是否已经低于该值。
3. 若没有，就在消息数组中二分查找最早的一个索引，使得 `messages[mid:]` 的 token 数不超过预算。
4. 找到候选点后，再调用 `_find_safe_cutoff_point()` 做安全修正。

这个设计的关键点是：

- 它保留的是“后缀”
- 它要找的是“最早能留下来的后缀起点”
- 它用二分查找避免在长消息历史上做高成本线性扫描

### 5.5 为什么要做 safe cutoff

直接在某个 index 把消息切开有个风险：

可能把一条 `AIMessage(tool_calls=...)` 和后面的 `ToolMessage` 切成两半。

如果发生这种情况，后续消息语义就会被破坏：

- 要么只剩工具响应，没有工具请求
- 要么只剩工具请求，没有工具响应

所以 `_find_safe_cutoff_point()` 会额外检查：

1. 如果分界点落在 `ToolMessage` 上，就先收集这一串 `ToolMessage` 的 `tool_call_id`。
2. 向前回溯，寻找发起这些 `tool_call_id` 的 `AIMessage`。
3. 如果找到，就把 cutoff 往前挪到那条 `AIMessage`。
4. 如果找不到，至少把 cutoff 往后挪过整串 `ToolMessage`，避免留下孤儿工具响应。

这一步非常关键。它说明 `SummarizationMiddleware` 不是简单地“留最后 N 条”，而是在努力维护 tool call 语义完整性。

### 5.6 摘要生成算法

确定好要摘要的片段后，真正的摘要流程在 `_create_summary()` / `_acreate_summary()` 里。

步骤如下：

1. 如果待摘要消息为空，直接返回固定文本。
2. 调用 `_trim_messages_for_summary()`，把要喂给摘要模型的消息进一步裁切到 `trim_tokens_to_summarize` 以内。
3. 用 `get_buffer_string()` 把消息转成纯文本串，避免消息对象的元数据膨胀 token。
4. 用 `summary_prompt.format(messages=formatted_messages)` 组装提示词。
5. 调用摘要模型。
6. 取响应文本作为摘要结果。

其中第 2 步也有细节：

- 默认用 `trim_messages(...)`
- `strategy="last"`，表示尽量保留尾部
- `start_on="human"`，尽量从 human 消息开始
- `allow_partial=True`
- `include_system=True`

如果裁切本身抛错，则退化为保留最后 15 条消息。

如果模型摘要调用抛错，也不会中断整个 agent，而是返回一段 `"Error generating summary: ..."` 文本作为摘要内容。这是一种明显偏可用性的设计。

### 5.7 它如何真正改写状态

`before_model()` 的返回值不是一条普通追加消息，而是：

```python
{
    "messages": [
        RemoveMessage(id=REMOVE_ALL_MESSAGES),
        *new_messages,
        *preserved_messages,
    ]
}
```

这表示：

1. 先清空整个 `messages`
2. 插入一条新的摘要 `HumanMessage`
3. 再把保留的最近消息尾部拼回去

所以摘要后的消息形态通常会变成：

```text
HumanMessage("Here is a summary of the conversation to date: ...")
+ 最近保留的若干原始消息
```

也正因为是“全量替换”，很多依赖旧消息显式存在的功能都会受影响，这也是 lumen 后面要对 todo 做额外补偿的原因。

### 5.8 lumen 中如何接入它

lumen 没直接手写 `SummarizationMiddleware(...)`，而是经过了一层配置转换：

- `config.yaml` / `config.example.yaml` 里的 `summarization` 段负责声明参数
- `app_config.py` 在启动时读取配置并调用 `load_summarization_config_from_dict()`
- `agent.py` 的 `_create_summarization_middleware()` 再把配置转换为 LangChain 所需参数

因此，项目里摘要能力的工程模型是：

- 框架负责算法
- 项目负责参数化与装配顺序

## 6. PlanningState 的实现逻辑

### 6.1 它是 AgentState 的一个扩展型 schema

`PlanningState` 定义在 `todo.py` 中，继承自 `AgentState`，只额外增加了一个字段：

- `todos`

定义形式是：

```python
todos: Annotated[NotRequired[list[Todo]], OmitFromInput]
```

这里的含义拆开看是：

- `NotRequired`
  说明不是每轮状态里都必须存在
- `list[Todo]`
  说明它是结构化待办数组，而不是一段自然语言
- `OmitFromInput`
  说明它不会出现在 agent 的输入 schema 中

### 6.2 OmitFromInput 的真实含义

`OmitFromInput = OmitFromSchema(input=True, output=False)`。

这意味着：

- `todos` 不作为外部调用 agent 时必须传入的输入字段
- 但它仍然会保留在内部状态和输出状态中

这很重要，因为它表达的是一种设计意图：

待办列表不应该靠调用方直接灌入，而应该由 agent 自己在运行中通过 `write_todos` 工具维护。

也就是说，`PlanningState` 不是“给用户传计划”的接口，而是“给 agent 存计划”的状态面。

### 6.3 它为什么适合计划模式

如果没有 `PlanningState`，todo 只能存在于消息文本中，那么一旦消息被压缩、截断或被窗口淘汰，模型就可能失去计划。

而 `PlanningState` 让 todo 进入线程状态后，就有了几个性质：

- 可以跨多轮保存
- 可以被 checkpointer 持久化
- 可以不依赖消息文本是否仍然可见
- 可以被 middleware 在模型调用前重新读取并做补偿注入

这就是计划模式能够在长任务中相对稳定的根本原因。

## 7. Todo、ModelRequest、ModelResponse、ModelCallResult

这一节补的是前面几节里一直反复出现、但还没单独拆开的 4 个关键类型。

### 7.1 Todo 是 todo 系统里的最小原子单元

`Todo` 定义在 `langchain.agents.middleware.todo` 中，本质上是一个 `TypedDict`：

```python
class Todo(TypedDict):
    content: str
    status: Literal["pending", "in_progress", "completed"]
```

它表达的是“一条待办项”的最小结构，而不是整个计划。

这里最重要的是两个字段：

- `content`
  待办内容本身
- `status`
  当前状态，只允许是 `pending`、`in_progress`、`completed`

这意味着 LangChain 的 todo 机制不是拿自然语言段落来表示计划，而是要求模型维护一个结构化数组：

```python
list[Todo]
```

这有几个直接后果：

- todo 可以被精确覆盖，而不是靠文本解析
- middleware 可以按结构读取和重写 todo
- checkpointer 存的是结构化状态，不是“模型说过的一段计划文本”

所以 `Todo` 其实是整个计划模式里最底层的“数据原子”。

### 7.2 ModelRequest 是 middleware 包裹模型调用时看到的“请求快照”

`ModelRequest` 定义在 `langchain.agents.middleware.types` 中，是 `wrap_model_call()` 里最核心的输入对象。

它包含这些关键字段：

- `model`
  当前实际要调用的聊天模型
- `messages`
  本次送给模型的消息列表，不含 system message
- `system_message`
  当前系统消息
- `tool_choice`
  工具选择策略
- `tools`
  本轮可用工具
- `response_format`
  结构化输出策略
- `state`
  当前 agent 状态
- `runtime`
  当前 LangGraph 运行时
- `model_settings`
  模型调用时附带的额外设置

这里最容易误解的一点是：

`ModelRequest` 不是线程状态本身，它更像“从当前状态和配置中整理出来的一次模型调用请求对象”。

也就是说，它是运行时快照，而不是持久状态。

### 7.3 为什么 ModelRequest 设计成 override，而不是原地改

`ModelRequest` 虽然是 dataclass，但 LangChain 明确不鼓励你直接改属性。

它专门提供了：

```python
request.override(...)
```

来生成一个新的请求对象。

这背后的算法思想其实是“近似不可变请求”：

- middleware 读到当前请求
- 不直接把原对象改乱
- 而是基于原请求复制出一个带局部修改的新请求
- 然后把新请求交给下一个 handler

因此，一个典型的 `wrap_model_call()` 实际上像这样工作：

```text
收到 request
-> 读 request.system_message / request.tools / request.model
-> 生成新的 request.override(...)
-> 调 handler(new_request)
-> 拿到模型响应
-> 再决定是否继续包装响应
```

这也是为什么 `TodoListMiddleware` 会用它来追加系统提示词，而不是直接改全局 prompt。

### 7.4 ModelRequest 在 lumen 中最典型的作用点

在这个项目里，虽然你自己写的很多中间件主要使用 `before_model()`，但 LangChain 内置的 `TodoListMiddleware` 是通过 `wrap_model_call()` 起作用的。

它的算法就是：

1. 读取 `request.system_message`
2. 把 todo 规则拼接进去
3. 用 `request.override(system_message=new_system_message)` 构造新请求
4. 调用下一个 handler

这说明 `ModelRequest` 最适合干的事情是：

- 改模型请求
- 不改线程状态

也就是说，它偏“调用层”，不是“状态层”。

### 7.5 ModelResponse 是模型调用之后的标准返回封装

`ModelResponse` 也是定义在 `types.py` 里，它只有两个核心字段：

- `result`
  一个消息列表，通常至少包含一条 `AIMessage`
- `structured_response`
  如果用了结构化输出，则这里是解析后的结构化对象

它表示的是：

模型执行完之后，中间件链和后续 graph 节点应该接收什么。

注意这里的 `result` 是消息列表，而不是纯字符串。原因是 LangChain 的模型层并不只可能返回自然语言答复，还可能返回：

- 带 tool calls 的 `AIMessage`
- 结构化输出相关的 `ToolMessage`
- 其他标准消息对象

所以 `ModelResponse` 是一个“消息级返回容器”，而不是“文本级返回容器”。

### 7.6 ModelCallResult 为什么是联合类型

`ModelCallResult` 是：

```python
ModelResponse | AIMessage
```

这表示 `wrap_model_call()` 的返回值可以有两种写法：

第一种，完整返回 `ModelResponse`。

第二种，直接返回一个 `AIMessage`，由框架自动当成简写形式处理。

这么设计的目的是平衡两种 middleware 场景：

- 简单场景
  只想快速改一下模型回复，直接回一条 `AIMessage` 就够了
- 复杂场景
  需要同时返回消息列表和结构化结果，就必须回 `ModelResponse`

从算法角度看，它其实是在降低 middleware 的编写门槛。

### 7.7 这三个类型在 wrap_model_call 中是如何串起来的

可以把 `wrap_model_call` 的数据流画成下面这样：

```text
ModelRequest
-> middleware 读取 / override 请求
-> handler(新请求)
-> 得到 ModelResponse 或 AIMessage
-> middleware 决定直接返回、替换返回、或包装后返回
-> 框架继续把结果写回消息状态
```

如果更具体一点：

- `ModelRequest`
  是“送进去什么”
- `ModelResponse`
  是“标准格式拿回来什么”
- `ModelCallResult`
  是“middleware 被允许返回什么”

这个区分非常关键，因为它解释了为什么 `wrap_model_call` 比 `before_model` 更强大：

- `before_model` 只能交状态增量
- `wrap_model_call` 可以直接拿到完整请求对象，并且直接控制返回对象

### 7.8 它们和线程状态是什么关系

这几个类型很容易和 `AgentState` / `PlanningState` 混在一起，但实际上它们分属不同层。

- `Todo`
  是计划状态里的最小数据项
- `PlanningState`
  是线程状态 schema 的一部分
- `ModelRequest`
  是一次模型调用的请求封装
- `ModelResponse`
  是一次模型调用的响应封装
- `ModelCallResult`
  是 middleware 在模型包装阶段允许返回的结果类型

也就是说：

- `Todo`、`PlanningState` 更偏状态层
- `ModelRequest`、`ModelResponse`、`ModelCallResult` 更偏调用协议层

## 8. TodoListMiddleware 的实现算法

### 7.1 它的核心不是“帮模型列计划”，而是“给 agent 注入计划管理协议”

`TodoListMiddleware` 做了三件核心事情：

1. 定义 todo 状态 schema
2. 动态注入 `write_todos` 工具
3. 通过系统提示词约束模型如何使用这个工具

所以它不是一个简单的 prompt 模板，而是一套“状态 + 工具 + 约束”的完整机制。

### 7.2 state_schema = PlanningState

`TodoListMiddleware` 显式声明：

```python
state_schema = PlanningState
```

这意味着一旦这个 middleware 被接进 agent，整张图的状态 schema 就会自动扩展出 `todos` 字段。

这也是为什么 `write_todos` 返回 `Command(update={"todos": ...})` 时，状态系统能接得住。

### 7.3 它是如何注入 write_todos 工具的

在 `__init__()` 中，`TodoListMiddleware` 动态定义了一个 `write_todos` 工具函数，并把它放进：

```python
self.tools = [write_todos]
```

而 `create_agent()` 会统一收集中间件的 `tools`，与普通工具一起交给 `ToolNode`。

这意味着：

- `write_todos` 不需要在项目工具注册表里声明
- 它只在 middleware 被启用时才存在
- 它天然和 `PlanningState` 绑定

这正是它和普通业务工具的本质区别。

### 7.4 write_todos 工具的更新算法

`write_todos` 工具不是增量 patch todo 列表，而是整表替换。

它返回的是：

```python
Command(
    update={
        "todos": todos,
        "messages": [ToolMessage(f"Updated todo list to {todos}", tool_call_id=tool_call_id)],
    }
)
```

这表示一次 `write_todos` 调用会同时做两件事：

1. 用新的 todo 列表覆盖状态中的 `todos`
2. 追加一条 `ToolMessage` 到消息历史，作为工具执行结果

所以 todo 的真实来源是结构化状态，不是消息里的那段文字说明。消息里的 `ToolMessage` 更多是为了让模型下一轮还能看见“刚刚已经更新过 todo”。

### 7.5 系统提示词注入算法

`TodoListMiddleware` 在 `wrap_model_call()` / `awrap_model_call()` 里工作。

逻辑是：

1. 看当前请求是否已经有 `system_message`
2. 如果有，就把 todo 系统提示词追加到原 system message 后面
3. 如果没有，就创建一个新的 `SystemMessage`
4. 调用 `handler(request.override(system_message=new_system_message))`

这说明它不是直接改状态，而是改“送给模型的请求”。

这个设计比把提示词写死在全局 prompt 里更灵活，因为：

- 只有启用 Todo middleware 时才会注入
- 不影响其他模式
- 可以通过参数覆盖默认系统提示词和工具描述

### 7.6 为什么它要禁止并行多次 write_todos

Todo 中间件在 `after_model()` 里还做了一件约束校验：

1. 取最后一条 `AIMessage`
2. 统计其中名为 `write_todos` 的 tool call 数量
3. 如果大于 1，就不给它们正常执行，而是为每个调用返回一条错误 `ToolMessage`

原因很直接：

`write_todos` 是整表替换，不是 append 或 patch。

如果一次模型输出里同时并行发出两次 `write_todos`，就会出现语义歧义：

- 到底以哪一份列表为准
- 是否要合并
- 合并规则是什么

LangChain 直接选择了最保守也最稳定的策略：

一次模型调用里，最多允许一次 `write_todos`。

### 7.7 TodoListMiddleware 的算法总结

可以把它压缩成下面的伪代码：

```text
初始化:
  扩展 state_schema 为 PlanningState
  创建 write_todos 工具
  把工具挂到 self.tools

每次模型调用前:
  把 todo system prompt 拼到 system_message

当模型调用 write_todos 时:
  用 Command(update=...) 覆盖 todos
  同时追加 ToolMessage

每次模型调用后:
  检查最后一条 AIMessage
  如果其中 write_todos 出现多次:
    为每个调用生成 error ToolMessage
    阻止歧义更新
```

## 9. lumen 为什么还要自定义 TodoMiddleware

### 8.1 原生 TodoListMiddleware 的局限

原生 `TodoListMiddleware` 已经能：

- 注入 `write_todos`
- 维护 `todos` 状态
- 给模型补任务管理系统提示词

但它默认假设一个前提：

只要 `todos` 还存在，模型就能从消息历史里理解当前计划。

这个前提在短上下文下通常成立，但在 lumen 这种叠加了 `SummarizationMiddleware` 的系统里就不一定了。

因为摘要会直接重写 `messages`，导致原来的：

- `write_todos` 工具调用
- 对应的 `ToolMessage`

都可能被替换掉。

这时就会出现一种典型状态不一致：

- 状态层还有 `todos`
- 消息层已经看不到 todo 的来源和最新状态

### 8.2 项目自定义 TodoMiddleware 做了什么

lumen 的 `runtimes/backend/src/agents/middlewares/todo_middleware.py` 继承自 LangChain 的 `TodoListMiddleware`，额外重写了 `before_model()` / `abefore_model()`。

它的补偿算法是：

1. 读取状态中的 `todos`
2. 若没有 todo，直接返回 `None`
3. 检查当前消息窗口里是否仍存在带 `write_todos` 调用的 `AIMessage`
4. 检查当前消息窗口里是否已经存在 `name="todo_reminder"` 的提醒消息
5. 如果“状态里有 todo，但消息里已经看不到 write_todos，且提醒还没注入过”，就构造一条新的 `HumanMessage`
6. 这条消息把当前 todo 列表重新格式化后注入到消息历史中

这实际上是在做“状态到消息的补偿投影”。

它不是恢复旧消息，也不是重新执行工具，而是：

在模型调用前，重新把状态里的 todo 以提醒消息形式投射回当前上下文窗口。

### 8.3 为什么这个补偿有效

因为在 LangChain 的 agent loop 中，模型真正能看见的是：

- 当前 `messages`
- 当前 `system_message`
- 当前可用工具

它不会直接“看见 Python 里的 state dict”。

所以只把 todo 留在状态里还不够，必要时还需要把它重新显化到消息窗口中。

lumen 这层扩展解决的就是这个问题。

## 10. 这几个组件在 lumen 里的协同关系

把这四个对象放回 lumen，可以得到一个比较完整的链路：

1. `create_agent()` 根据 middleware 列表构建图。
2. `AgentMiddleware` 约定了各类 hook 的行为和状态 schema 扩展方式。
3. `SummarizationMiddleware` 在 `before_model` 阶段可能重写 `messages`。
4. `TodoListMiddleware` 通过 `PlanningState` 提供 `todos` 状态与 `write_todos` 工具。
5. lumen 自定义的 `TodoMiddleware` 在摘要造成消息缺口时，把 `todos` 重新投影回消息窗口。

所以这几部分不是并列关系，而是层层叠加关系：

- `AgentMiddleware` 是协议层
- `PlanningState` 是 Todo 的状态层
- `TodoListMiddleware` 是 Todo 的基础策略层
- `SummarizationMiddleware` 是上下文管理层
- lumen `TodoMiddleware` 是面向长上下文的补偿层

## 11. 一个完整例子

假设用户发起一个复杂任务，计划模式开启，同时摘要功能也开启。

系统可能经历这样的过程：

1. `TodoListMiddleware` 把 `write_todos` 工具和 todo 系统提示词注入给模型。
2. 模型先调用 `write_todos`，生成三项待办。
3. `write_todos` 把 todo 列表写入 `todos` 状态，并在消息里留下 `ToolMessage`。
4. 随着任务变长，`SummarizationMiddleware` 在某一轮触发，把早期消息替换为摘要消息。
5. 原始 `write_todos` 调用和对应 `ToolMessage` 可能从当前消息窗口消失。
6. 但 `PlanningState.todos` 仍然保留在状态里。
7. lumen 的 `TodoMiddleware.before_model()` 发现“状态里有 todo，但消息里已看不到它”，于是注入 `todo_reminder`。
8. 模型继续工作，并在后续步骤再次调用 `write_todos` 更新进度。

这个例子说明：

真正保证计划连续性的，不是单独某一个类，而是“状态持久化 + 消息补偿 + 工具调用协议”三者联动。

## 12. 工程上的优点与代价

### 11.1 优点

- 中间件职责清晰，摘要和 todo 都是可插拔能力
- todo 不是纯文本，而是结构化状态，适合持久化与恢复
- 摘要算法考虑了 tool call 配对，不是粗暴切消息
- 本项目对 todo 做了额外补偿，适合长任务

### 11.2 代价

- 摘要会重写消息历史，因此任何依赖“旧消息必须可见”的能力都要额外设计补偿
- `write_todos` 是整表替换，约束模型必须一次只发一个更新调用
- `fraction` 类型的摘要阈值依赖模型 profile 中存在 `max_input_tokens`
- 摘要质量本质上仍依赖摘要模型本身的理解能力

## 13. 最后的判断标准

如果要快速判断这几个对象各自解决什么问题，可以记下面这组对应关系：

- `AgentMiddleware`
  定义中间件如何介入 agent loop
- `SummarizationMiddleware`
  在上下文将满时，安全地把旧消息压缩成摘要并重写消息历史
- `PlanningState`
  把 todo 从消息文本提升为结构化线程状态
- `TodoListMiddleware`
  给 agent 注入 todo 工具、todo 状态和 todo 使用规则

而在 lumen 里，真正让这套机制能扛住长任务和摘要压缩的最后一层，是项目自定义的 `TodoMiddleware`。

## 14. 附：一句话版记忆法

可以把它记成一句话：

LangChain 用 `AgentMiddleware` 定义了“怎么插手 agent loop”，`SummarizationMiddleware` 负责压缩消息，`TodoListMiddleware + PlanningState` 负责把计划变成结构化状态，而 lumen 再补了一层“摘要后别把 todo 弄丢”的上下文补偿。
