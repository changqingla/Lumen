# lumen 状态机与 Graph 编排模块技术说明

## 1. 文档目标

这份文档要回答的不是“项目用了 LangGraph 吗”这种表面问题，而是更具体的实现问题：

- 图入口在哪里暴露。
- 主 Agent 到底是手写 StateGraph，还是用预制 agent loop。
- 状态是怎么定义并在循环里演化的。
- 中间件和工具为什么承担了大量原本可以做成 graph node 的职责。
- 子 Agent 与 bootstrap Agent 又是如何复用这套图编排模型的。

换句话说，这份文档关注的是 lumen 的“执行编排内核”。

## 2. 核心结论

这个项目的编排模式不是前端 React 风格状态流，也不是纯手写节点式 DAG。

它的核心是一种“预制 Agent Loop + 自定义状态模式 + 中间件切面 + Command 控制流”的编排方式。

更准确地说：

- 对外暴露的是 LangGraph 图。
- 图的主体由 `create_agent()` 生成，而不是项目自己手写大量 node/router。
- 项目真正定制的部分，主要集中在状态模式、工具集、提示词和中间件链。

所以如果一定要归类，它更接近“基于 LangGraph 预构建 Agent Graph 的定制化 agent looper”。

## 3. 图入口在什么地方

整个系统对 LangGraph Server 暴露的图入口，定义在 `runtimes/backend/langgraph.json`。

这里做了两件关键事情：

- 把图名 `lead_agent` 绑定到 `src.agents:make_lead_agent`。
- 把 checkpointer 绑定到异步工厂 `src/agents/checkpointer/async_provider.py:make_checkpointer`。

这意味着在 LangGraph Server 看来，系统只公开了一个主图入口。至于主图内部是几个节点、什么循环、带哪些工具，都是由 `make_lead_agent()` 在运行时构造出来的。

## 4. 为什么这个项目没有手写大型 `StateGraph`

从代码结构看，项目没有自己显式去写一个由十几个 node 和 router 组成的大型 StateGraph。它选择的是 `langchain.agents.create_agent()`。

这样做不是偷懒，而是刻意把“模型调用-工具调用-状态回写”的标准循环外包给框架，把项目精力集中在真正有业务差异的部分：

- 用什么提示词。
- 用什么模型。
- 用什么工具集合。
- 用什么中间件切面。
- 线程状态要扩展哪些字段。

这背后的工程判断是：

- 通用 agent loop 由框架生成，稳定且少 bug。
- 业务差异主要不在 loop 骨架，而在 loop 的周边约束和状态语义。

因此这个项目的图编排不是“自己画一张复杂 graph”，而是“用框架的标准 graph，当作一个可注入状态和切面的宿主”。

## 5. 主图的真正装配点

主图装配的核心文件是 `runtimes/backend/src/agents/lead_agent/agent.py`。

`make_lead_agent()` 在这里完成几项关键工作：

- 解析运行时 configurable 参数。
- 解析最终模型名。
- 判断是否启用 thinking、plan mode、subagent。
- 判断是否处于 bootstrap 模式。
- 加载自定义 Agent 配置。
- 构造工具列表。
- 构造中间件列表。
- 构造系统提示词。
- 指定状态模式为 `ThreadState`。
- 最后调用 `create_agent()`。

也就是说，这个函数本质上就是主图的“编排工厂”。

## 6. `ThreadState` 为什么是图编排的基础

图是否稳定运行，首先取决于状态模式是不是足够清晰。

lumen 没有把状态拆成很多散落的小表，而是用 `ThreadState` 作为统一状态面。

它在标准 AgentState 之上增加了：

- `sandbox`
- `thread_data`
- `title`
- `artifacts`
- `todos`
- `uploaded_files`
- `viewed_images`

这样设计以后，图中的每一轮循环都围绕同一份状态演化：

- 模型读 `messages` 和其他状态。
- 工具通过 `Command(update=...)` 写状态。
- 中间件在各 hook 时机读写状态。
- checkpointer 把整个状态快照持久化。

所以在这个系统里，Graph 编排的核心不是“节点之间传什么参数”，而是“所有节点和切面共同操作哪一份状态对象”。

## 7. 主循环本质上长什么样

虽然代码没有手写 node 图，但从运行语义上看，它仍然是一个标准的 agent loop：

第一步，读取当前线程状态。

第二步，把提示词、消息历史和中间件注入内容送给模型。

第三步，模型输出：

- 如果是普通回答，则本轮趋向结束。
- 如果带有 tool_calls，则进入工具执行阶段。

第四步，工具返回字符串或 `Command(update=...)`。

第五步，状态被更新，新的 ToolMessage 或其他副作用写入状态。

第六步，再次进入模型，直到没有新的工具调用，或被控制命令提前结束。

所以它依然是 loop，只是这个 loop 由框架生成，而不是手写 `while` 或手写 graph edge。

## 8. `Command` 在这个图里承担什么角色

如果说 `ThreadState` 是静态状态面，那么 `Command` 就是动态控制面。

当前项目里最常见的两种 Command 用法是：

- `Command(update=...)`
- `Command(update=..., goto=END)`

前者用于声明式地修改线程状态。
后者用于在写状态的同时终止当前执行分支。

这让系统不必再额外手写一个“控制节点”去表达跳转。工具或中间件本身就可以成为控制流发起点。

## 9. 为什么 `ask_clarification` 能中断整张图

`ask_clarification` 这个工具看上去像普通工具，实际不是。

它真正的控制逻辑在 `ClarificationMiddleware` 里：

- 先拦截工具调用。
- 从参数里提取 question、clarification_type、options、context。
- 格式化成一条 ToolMessage。
- 返回 `Command(update={"messages": [...]}, goto=END)`。

这意味着图不会继续进入后续的普通工具处理和模型回合，而是直接在当前 run 收束。

从编排角度看，这是一条“中断边”。

但项目没有把它画成独立 graph edge，而是借助中间件和 Command 在运行时动态生成。

## 10. 为什么很多横切能力没有被做成 graph node

如果用传统 LangGraph 思路，很多功能都可以拆成单独节点：

- 上传文件上下文整理。
- 悬空工具修补。
- 图片注入。
- todo 提醒。
- 标题生成。
- 记忆异步更新。

lumen 没这么做，而是优先做成中间件。

原因很现实：

- 这些能力大多是横切关注点，不是业务主路径节点。
- 它们通常作用于“模型前”“模型后”“工具前”“Agent 前后”，和主循环时机强绑定。
- 若全部变成 graph node，主图会被很多辅助节点淹没，难以维护。

所以这个项目的 graph 编排哲学不是“把所有事都节点化”，而是“主 loop 保持紧凑，把横切逻辑交给 middleware hook”。

## 11. 中间件顺序为什么等同于编排顺序

在 `_build_middlewares()` 里，中间件不是随便 append 的，它们的顺序本身就是调度逻辑。

例如：

- ThreadDataMiddleware 先计算线程路径。
- UploadsMiddleware 才能基于线程路径构造上传文件上下文。
- SandboxMiddleware 负责准备执行环境。
- DanglingToolCallMiddleware 在模型读取历史前修补协议缺口。
- SummarizationMiddleware 尽早压缩上下文。
- TodoMiddleware 在 plan mode 下插入任务管理提示。
- TitleMiddleware 与 MemoryMiddleware 在较后阶段补全元信息和记忆投递。
- ViewImageMiddleware 只在支持视觉模型上启用，并且必须在模型调用前注入图像内容。
- ClarificationMiddleware 总是最后，用来拦截澄清请求。

也就是说，这里的“图编排”有一部分其实不是写在 graph edges 上，而是写在 middleware ordering 里。

## 12. 图装配时有哪些运行时分支

`make_lead_agent()` 不是构造一张完全固定的图，它会根据运行参数拼出不同变体。

主要分支包括：

- `thinking_enabled` 是否开启。
- `reasoning_effort` 是否传递给模型。
- `is_plan_mode` 是否开启 TodoMiddleware。
- `subagent_enabled` 是否暴露 task 工具以及添加 SubagentLimitMiddleware。
- `agent_name` 是否启用自定义 Agent 的模型覆盖和工具白名单。
- `is_bootstrap` 是否进入引导模式。

所以这里更像“按配置生成图实例”，而不是“项目里只存在一张永远不变的静态图”。

## 13. bootstrap 模式说明了什么

bootstrap 模式很能体现这个项目的图编排方式。

它并没有再定义一张完全不同的图，而是在同一个 `make_lead_agent()` 工厂里，切换为一组更小的装配参数：

- 使用最小化 prompt。
- 在普通工具集基础上额外加入 `setup_agent`。
- 不加载普通自定义 Agent 配置。

这说明在本项目中，“不同图”很多时候并不是不同 graph 文件，而是同一个 agent graph 工厂在不同参数下生成的不同运行实例。

## 14. 子 Agent 为什么也属于同一套编排哲学

子 Agent 的构造在 `runtimes/backend/src/subagents/executor.py`。

它同样使用 `create_agent()`，但会切掉很多主图能力，只保留最小执行骨架：

- 模型。
- 过滤后的工具。
- 最小中间件集合。
- 子 Agent 专属 system prompt。
- 同样的 `ThreadState`。

这说明子 Agent 不是主图外的另一个异构系统，而是同一编排框架下的轻量图实例。

区别只是：

- 主图负责面向用户、多能力协调。
- 子图负责隔离上下文、完成被委派任务。

## 15. 子 Agent 图为什么只保留最小中间件

子 Agent 只装了两个关键中间件：

- ThreadDataMiddleware
- SandboxMiddleware

而且都启用了 `lazy_init=True`，倾向复用父级资源。

这种做法非常说明问题：

- 子 Agent 不需要再承担标题生成、记忆更新、澄清中断这些面向主会话的职责。
- 它只需要能访问正确的线程路径和沙箱。
- 这样图更轻，执行成本也更低。

所以这个项目的 Graph 编排不是“一张图打天下”，而是按职责裁剪不同层级 agent 的运行骨架。

## 16. 为什么说它是 agent looper，而不是 router graph

从 `智能体架构技术文档` 也能看出来，整个系统更像一个持续反复执行的 agent loop，而不是一个按业务分叉路由的大型 router graph。

它的主要特征是：

- 同一模型节点会被反复调用。
- 工具节点也会反复进入。
- 是否继续运行，由模型是否继续发起 tool call 决定。
- 某些特殊工具或中间件会提前终止 loop。

这和那种“用户请求来了，根据意图路由到 A/B/C 节点”的图不一样。

它更像 ReAct 风格的循环式状态机，只不过运行在 LangGraph 的状态持久化框架上。

## 17. `stream_mode="values"` 说明图是以状态快照为中心的

无论是嵌入式客户端还是子 Agent 执行器，流式消费时都大量使用 `stream_mode="values"`。

这说明当前项目在图执行时更关心：

- 每一步之后完整状态长什么样。

而不是只关心：

- 某个 token 或某个 edge 触发了什么最细粒度事件。

这是一种非常典型的状态机视角：

- 图的运行结果首先表现为状态演化。
- 事件只是状态演化对外暴露的不同观察角度。

## 18. checkpointer 为什么也是图编排的一部分

很多人会把 checkpointer 当成“存储层问题”，但在这个项目里它其实是编排层的一部分。

原因是没有 checkpointer，就没有真正的跨轮状态机。

有了 checkpointer 之后：

- 同一个 `thread_id` 可以在多次调用间复用完整状态。
- graph 不是每次从空白初始状态重启。
- 中间件、摘要、记忆、产物等都能建立长期语义。

所以在 `langgraph.json` 里把 checkpointer 直接挂在图定义旁边，本身就是一种编排声明：这张图被设计成支持会话延续，而不是一次性函数调用。

## 19. 为什么当前编排结构很适合“能力叠加”

这个项目的一大优点是：新增能力时，通常不需要重写主图。

常见扩展方式有三种：

- 新增工具。
- 新增中间件。
- 扩展状态字段。

只要不破坏主 loop，很多新能力都能通过这三种方式叠加进去。

这就是预制 Agent Graph 的优势：

- 主循环稳定。
- 扩展点足够多。
- 新能力与老能力的耦合点相对集中。

## 20. 这个模块里的几个关键 tricks

### 20.1 用 `create_agent()` 承担通用循环，把定制点留给业务层

项目避免自己手写复杂 StateGraph，从而把维护成本集中在真正有业务差异的地方。

### 20.2 用 `ThreadState` 统一状态面

图里的所有行为最终都围绕一份状态对象展开，这让 checkpointer、工具、副作用和中间件能够自然协同。

### 20.3 用中间件顺序表达隐式编排

很多控制逻辑不是体现在显式 graph node 上，而是体现在 hook 类型和 middleware 顺序里。

### 20.4 用 `Command` 替代额外控制节点

状态更新、提前结束、特殊分支都可以由工具或中间件直接发起，避免图结构膨胀。

### 20.5 用同一套工厂生成主图、bootstrap 图和子图变体

不同角色的 agent 并没有分裂成完全不同的执行框架，而是在一个统一编排哲学下做轻重裁剪。

## 21. 当前实现的能力边界

这个模块当前也有边界：

- 项目没有自己显式维护一份可视化的大型 graph 定义，理解执行路径更多需要结合 `create_agent()` 语义和中间件链。
- 由于大量控制逻辑位于中间件和工具返回的 Command 中，执行流是“动态生成”的，静态读图不如传统节点图直观。
- 它更擅长 agent loop 型问题，不是那种复杂业务审批流或多分支工作流编排引擎。
- 一些流程约束仍然由 prompt 协议承担，而不是强形式化图约束。

## 22. 这个模块的本质是什么

lumen 的状态机与 Graph 编排模块，本质上是在用 LangGraph 提供的持久化状态机壳子，承载一个经过深度定制的 agent loop。

它没有把复杂度放在“画很多节点”上，而是把复杂度放在“怎样稳定地驱动一轮轮状态演化”上：

- 状态怎么建模。
- 循环怎么控制。
- 工具怎么回写。
- 中间件怎么切面化介入。
- 特殊情况怎么用 Command 改写执行方向。

理解了这一点，就能看清这个项目的编排内核并不是一张炫技 graph，而是一台可持续运转、可插拔增强、可持久化恢复的 Agent 状态机。
