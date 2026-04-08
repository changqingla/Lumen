# LangChain 上下文压缩中间件技术文档

## 1. 文档目标

本文详细介绍 LangChain 中 `SummarizationMiddleware` 的实现机制。重点不是泛泛解释“摘要可以压缩上下文”，而是基于当前项目本地安装的源码，说明它在代码层面到底做了什么、没做什么、以及它为什么能在多轮 Agent 对话中起到“释放上下文窗口”的作用。

阅读本文后，读者应能够回答以下问题：

- `SummarizationMiddleware` 到底是在什么时候触发的
- 它压缩的是哪一部分消息，保留的是哪一部分消息
- 它是如何避免把 AI 工具调用和 Tool 返回拆开的
- 摘要模型拿到的输入是什么样子
- 摘要完成后，消息历史是如何被替换的
- 它和“长期记忆”“消息裁剪”“窗口截断”这些概念有什么区别
- 在 lumen 中，这个中间件是如何被接入主 Agent 循环的

本文基于当前环境中的 LangChain 源码：

- `backend/.venv/lib/python3.12/site-packages/langchain/agents/middleware/summarization.py`

## 2. 核心结论

先给出最重要的结论。

LangChain 的 `SummarizationMiddleware` 并不是简单地“删掉旧消息”，而是采用如下策略：

1. 检查当前消息历史是否达到摘要触发阈值
2. 计算一个安全切分点，把消息分成“待摘要历史”和“保留历史”
3. 使用摘要模型把前半段旧历史总结成一段文本
4. 用一条新的摘要消息替换整段旧历史
5. 把最近一段原始消息继续保留下来

如果把它压缩成一句话，可以这样理解：

`SummarizationMiddleware` 用“一条摘要消息 + 最近若干原始消息”来替换“完整历史消息列表”，从而用抽象上下文替代远端细节，以释放模型输入窗口。

## 3. 代码位置与模块边界

### 3.1 核心实现位置

`SummarizationMiddleware` 的主体实现位于：

- `backend/.venv/lib/python3.12/site-packages/langchain/agents/middleware/summarization.py`

其中最关键的函数包括：

- `__init__`
  负责解析并校验 `trigger`、`keep`、`summary_prompt`、`trim_tokens_to_summarize`

- `before_model`
  同步模式下，在模型调用前决定是否执行摘要

- `abefore_model`
  异步模式下，在模型调用前决定是否执行摘要

- `_should_summarize`
  判断是否达到触发条件

- `_determine_cutoff_index`
  计算切分点，决定摘要哪一部分、保留哪一部分

- `_find_safe_cutoff_point`
  确保不会把 AI 工具调用和 ToolMessage 拆开

- `_create_summary` / `_acreate_summary`
  真正发起摘要模型调用并生成摘要文本

- `_build_new_messages`
  把摘要文本包装成新的消息对象

### 3.2 它不负责什么

`SummarizationMiddleware` 的职责边界其实很清晰。它不负责：

- 线程状态持久化
- 长期记忆提取
- 文件、图片、工作目录等派生上下文注入
- 任务列表补注入
- 业务字段的结构化压缩

它只负责一件事：

对当前消息列表做“历史摘要替换”。

这意味着它是一个“消息历史压缩器”，不是一个“全局上下文管理系统”。

## 4. 初始化参数与配置语义

`SummarizationMiddleware` 的构造函数支持如下关键参数：

- `model`
  用于执行摘要的模型，可以是模型对象，也可以是模型名字符串

- `trigger`
  触发摘要的条件，可以是单个阈值，也可以是阈值列表

- `keep`
  摘要后保留多少原始上下文

- `token_counter`
  用于统计消息 token 数的函数

- `summary_prompt`
  摘要提示词模板

- `trim_tokens_to_summarize`
  摘要前，允许送给摘要模型的最大 token 数

### 4.1 `trigger` 的三种规格

`trigger` 支持三种上下文大小表示方式：

- `("messages", N)`
  按消息条数触发

- `("tokens", N)`
  按 token 数触发

- `("fraction", x)`
  按模型最大输入窗口的比例触发，例如 `0.8`

`trigger` 既可以是单个值，也可以是列表。若传列表，则是 OR 逻辑，只要满足任意一个阈值就触发摘要。

### 4.2 `keep` 的三种规格

`keep` 也支持同样三种规格：

- `("messages", N)`
- `("tokens", N)`
- `("fraction", x)`

不同的是，`keep` 只能是单个值，不能是列表。

它的语义不是“摘要多少”，而是“摘要之后仍然保留多少原始消息不动”。

### 4.3 `fraction` 模式为何依赖模型 profile

如果 `trigger` 或 `keep` 使用了 `fraction`，中间件必须知道模型的 `max_input_tokens`。因此初始化时会从 `model.profile` 里读取这个值。

如果模型 profile 里没有 `max_input_tokens`，则会直接报错，而不是静默降级。这是为了避免“按比例配置，但实际并不知道比例基数”的错误运行状态。

## 5. 摘要触发机制

### 5.1 触发入口

真正的触发判断发生在 `before_model` / `abefore_model` 里：

1. 取出 `state["messages"]`
2. 为每条消息补齐缺失的 `id`
3. 统计总 token 数
4. 调用 `_should_summarize`
5. 若返回 `False`，则不做任何改动，直接放行

因此，这个中间件不会在每轮都执行摘要，而是一个“接近阈值时才执行”的被动压缩器。

### 5.2 `_should_summarize` 的判断逻辑

`_should_summarize` 会遍历所有触发条件：

- 若是 `messages`，则用 `len(messages)` 比较
- 若是 `tokens`，则用当前估算 token 数比较
- 若是 `fraction`，则用 `max_input_tokens * fraction` 作为阈值比较

只要任意一项满足，就立即触发。

### 5.3 真实 token 使用量的辅助判断

除了自己估算 token 数外，它还会查看最近一条 `AIMessage` 的 `usage_metadata["total_tokens"]`。如果模型返回了真实 token 使用统计，并且 provider 与当前摘要模型一致，它也会使用这个值辅助判断。

这说明 `SummarizationMiddleware` 不是完全依赖粗略估算，它也会尽量利用模型真实回传的 token 使用信息。

## 6. 如何决定“摘要哪一段”

### 6.1 总体思路

中间件不会随便从中间截断消息，而是先决定“保留多少最近消息”，再把更早的那部分拿去摘要。

换句话说，它的策略是：

- 后缀保留
- 前缀摘要

### 6.2 `keep = messages`

如果 `keep` 按消息条数配置，例如 `("messages", 10)`，则目标是保留最后 10 条消息。

切分点大致为：

- `len(messages) - 10`

但这个切分点还要经过安全修正，避免落在 `ToolMessage` 中间。

### 6.3 `keep = tokens` / `fraction`

如果按 token 数或窗口比例保留，则逻辑更复杂一些。

中间件会：

1. 先计算目标保留 token 数
2. 然后对消息后缀做二分搜索
3. 找到“最早的一个索引”，使得从这个索引到结尾的消息总 token 数不超过目标

也就是说，在 token 模式下，它不是按“最近 N 条消息”保留，而是按“最近 N token 的消息后缀”保留。

这种做法的好处是：

- 对长消息与短消息都更稳
- 更贴近真实上下文窗口控制

## 7. 为什么它不会把 AI/Tool 配对拆开

这是 `SummarizationMiddleware` 最值得注意的一个工程细节。

### 7.1 问题背景

如果切分点刚好落在 `ToolMessage` 上，会出现一种非常糟糕的状态：

- 当前窗口里只剩工具返回结果
- 但触发这个工具调用的 `AIMessage(tool_calls=...)` 已经被摘要掉了

模型下一轮看到这种历史，会很难判断这些 ToolMessage 是怎么来的，甚至误判执行状态。

### 7.2 它的解决办法

`_find_safe_cutoff_point` 会检查切分点处是不是 `ToolMessage`。

如果是，它会：

1. 收集从当前切分点起连续 `ToolMessage` 的 `tool_call_id`
2. 向前回溯，寻找带有对应 `tool_calls` 的 `AIMessage`
3. 如果找到，就把切分点前移到这个 `AIMessage`

这样做的结果是：

- 工具调用请求和工具响应要么都保留
- 要么都被纳入摘要历史

不会出现一半保留、一半被截掉的断裂状态。

### 7.3 边缘情况

如果回溯时没有找到匹配的 `AIMessage`，它会退而求其次，直接把切分点向后推过这段连续 `ToolMessage`，至少保证当前窗口里不留下孤立工具响应。

## 8. 摘要模型到底看到了什么

### 8.1 不是直接喂 message 对象

在 `_create_summary` 里，`messages_to_summarize` 会先经过 `_trim_messages_for_summary`，再通过 `get_buffer_string()` 转成一段纯文本。

也就是说，摘要模型实际看到的不是 Python 消息对象，而是一段格式化后的对话文本。

### 8.2 `trim_tokens_to_summarize` 的作用

这是一个非常关键但容易被忽略的参数。

它的作用是：

在真正调用摘要模型之前，再对“待摘要消息”做一次额外裁剪，避免摘要输入本身过长。

默认行为是调用 `trim_messages()`，参数大致如下：

- `strategy="last"`
  优先保留这批待摘要消息中的最后一段

- `start_on="human"`
  尽量从 human 消息边界开始

- `allow_partial=True`
  允许部分截断

- `include_system=True`
  包含系统消息

这意味着：

即使被标记为“要摘要”的旧历史很多，最终送给摘要模型的也可能只是其中较新的那一部分，而不是完整旧历史。

### 8.3 失败回退策略

如果 `trim_messages()` 本身出错，中间件会退化为只取最后 15 条消息做摘要。

这说明它对“摘要器本身也可能失败”做了保护，而不是让整个 Agent 调用直接报错。

## 9. 默认摘要提示词长什么样

默认提示词定义在 `DEFAULT_SUMMARY_PROMPT`。

它的核心意图是：

- 当前上下文已经接近输入上限
- 你必须从历史中提取最重要、最相关的信息
- 这些提取出来的内容将替换整段历史
- 因此要特别保留与总体目标相关的高价值上下文
- 只输出提取后的上下文正文，不要附加解释

这个提示词的设计重点不是“写一篇总结”，而是“提取最值得保留的运行上下文”。

因此它更接近：

- 状态压缩
- 运行轨迹提炼
- 高价值历史保真

而不是：

- 面向人类阅读的自然语言摘要

## 10. 摘要完成后，消息历史如何被替换

这是整个中间件最核心的一步。

### 10.1 新消息的构造方式

摘要文本生成后，会被包装成一条新的 `HumanMessage`：

`Here is a summary of the conversation to date:\n\n{summary}`

注意，它不是 `SystemMessage`，而是 `HumanMessage`。

### 10.2 完整替换逻辑

中间件返回的消息更新结果是：

1. `RemoveMessage(id=REMOVE_ALL_MESSAGES)` 清空全部旧消息
2. 插入一条摘要消息
3. 追加保留的最近原始消息

换句话说，最终模型看到的是：

- 一条“历史摘要”
- 一段近期原始消息

而不是：

- 原始完整消息列表

### 10.3 这意味着什么

这意味着摘要结果会真正进入对话历史本身，而不是存放在某个隐藏状态字段里。

后续再发生摘要时，这条摘要消息本身也可能继续成为更大历史的一部分，被再次压缩。

## 11. 同步与异步版本

`SummarizationMiddleware` 同时实现了：

- `before_model`
- `abefore_model`

两者逻辑几乎相同，只是摘要模型调用分别对应：

- `model.invoke(...)`
- `model.ainvoke(...)`

因此它既能跑在同步 Agent 调用链中，也能跑在异步 Agent 调用链中。

## 12. 错误处理策略

这个中间件对错误的处理总体偏保守：

- 若不满足触发条件，直接不处理
- 若切分点无效，直接返回 `None`
- 若待摘要消息为空，返回固定兜底文本
- 若摘要模型调用失败，返回 `"Error generating summary: ..."`
- 若摘要前裁剪失败，则退化为只保留最后 15 条消息来摘要

它的原则不是“摘要必须成功”，而是“摘要失败也不要把主流程搞崩”。

## 13. 这不是长期记忆

很多人第一次看这个中间件时，会把它和 Memory 混在一起。两者其实完全不是一回事。

`SummarizationMiddleware` 的本质是：

- 面向当前线程
- 面向当前消息窗口
- 用摘要消息替换旧历史

长期记忆系统的本质则是：

- 面向跨线程
- 面向结构化用户背景与偏好
- 把提取后的记忆重新注入系统提示词或上下文

前者解决的是：

- 当前消息历史太长怎么办

后者解决的是：

- 跨会话的重要信息怎么长期保留

## 14. 它的优点

从工程角度看，这个实现有几个明显优点。

### 14.1 简单直接

它不需要额外状态存储，也不要求上层框架理解“摘要状态”。只要能改写消息列表，就能工作。

### 14.2 与普通消息流兼容

摘要被包装成普通 `HumanMessage`，因此后续中间件、reducer、消息存储层都不需要特殊适配。

### 14.3 能兼顾近期细节与远期历史

通过 `keep` 保留最近原始消息，模型不会完全失去短期细节。

### 14.4 注意了 ToolMessage 的完整性

它不是粗暴截断，而是显式处理 AI/Tool 配对关系，这对工具型 Agent 很重要。

## 15. 它的局限与注意事项

### 15.1 摘要质量直接受摘要模型和提示词影响

如果摘要模型不够稳定，或者提示词不适合你的任务域，压缩出来的上下文质量会明显下降。

### 15.2 `trim_tokens_to_summarize` 可能让很老的内容连摘要都看不到

这是最常见的隐性问题之一。

如果要摘要的旧历史特别大，而你又把 `trim_tokens_to_summarize` 设得较小，那么摘要模型实际只会看到“待摘要历史中的最后一部分”。更早的消息可能直接从系统里消失，而不是被忠实压进摘要里。

### 15.3 摘要消息本身是非结构化文本

它压进去的是一段自然语言摘要，而不是结构化状态。因此：

- 细节是否保留取决于模型
- 事实是否精确保留取决于模型
- 工具执行细节是否可恢复也取决于模型

### 15.4 它不会自动补回业务状态

`SummarizationMiddleware` 自己只做消息替换，不负责修复“摘要后某些关键状态不可见”的问题。

这也是为什么很多实际项目会像 lumen 一样，在它外围再加专门的补强中间件。

## 16. 在 lumen 中的接入方式

lumen 在：

- `backend/src/agents/lead_agent/agent.py`

中创建并接入 `SummarizationMiddleware`。

接入逻辑大体是：

1. 从 `config.yaml` 读取 `summarization` 配置
2. 解析为 `SummarizationConfig`
3. 构造 `SummarizationMiddleware`
4. 把它尽量靠前地插入主 Agent 中间件链

当前项目里与之直接相关的位置包括：

- `backend/src/config/summarization_config.py`
  摘要配置模型

- `backend/src/config/app_config.py`
  启动时加载摘要配置

- `backend/src/agents/lead_agent/agent.py`
  创建并挂载摘要中间件

### 16.1 为什么 lumen 还要配 TodoMiddleware

因为摘要后，原始 `write_todos` 调用和对应工具消息可能已经不在当前消息窗口里了。

为了解决这个问题，lumen 增加了：

- `backend/src/agents/middlewares/todo_middleware.py`

这个中间件会在 todo 状态仍存在、但原始 todo 消息已经不可见时，再注入一条提醒消息，告诉模型当前待办状态。

这说明一个很重要的实践结论：

`SummarizationMiddleware` 负责压缩历史，但业务上关键的“可见状态修补”往往需要项目自己补。

## 17. 适合它的场景

`SummarizationMiddleware` 比较适合这些场景：

- 长对话、多轮规划型 Agent
- 工具调用很多、消息历史膨胀明显的系统
- 允许用摘要替代远端细节的业务
- 需要和现有消息流无缝兼容的项目

它不太适合这些场景：

- 对事实精确保真要求极高，且不能接受摘要失真
- 每条历史消息都可能在后续被精确引用
- 对“远端历史的原始细节”依赖极强

## 18. 一句话总结

LangChain 的 `SummarizationMiddleware` 本质上是一个“消息历史摘要替换器”：

它在上下文接近阈值时，把较早的一段消息历史压缩成一条新的摘要消息，并保留最近一段原始消息继续参与推理，同时尽量避免拆断 AI 与 Tool 的消息配对关系。

如果要进一步理解它在项目中的真实效果，关键不只是看它“会不会摘要”，而是看：

- 触发阈值设得是否合理
- 保留窗口是否足够
- 摘要模型与提示词是否可靠
- 项目是否对摘要后的业务状态缺口做了额外补强
