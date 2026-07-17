# lumen 自定义 Agent 模块技术说明

## 1. 文档目标

本文详细介绍 lumen 中的自定义 Agent 模块。重点不是说明 API 如何调用，而是解释这个项目如何把“自定义 Agent”实现成一组可被创建、读取、更新、删除并实际参与运行时组装的文件系统对象。

阅读本文后，读者应能够回答以下问题：

- 这个项目里的自定义 Agent 和默认 Agent 有什么关系
- 一个自定义 Agent 在磁盘上由哪些文件构成
- `config.yaml`、`SOUL.md`、独立记忆是如何一起参与运行时的
- 自定义 Agent 如何影响模型选择、工具白名单和系统提示词
- 为什么删除一个 Agent 要连配置、SOUL 和记忆一起删除
- `USER.md` 在当前实现里处于什么状态

### 1.1 先给出六个直接答案

- 自定义 Agent 和默认 Agent 的关系是什么：它不是另一套独立后端，而是在同一套 lead agent 运行时上叠加一层按名称切换的角色配置。
- 一个自定义 Agent 在磁盘上由哪些文件构成：全局定义目录包含 `config.yaml` 和 `SOUL.md`；用户长期记忆不在该目录，而在每个 Backend 签发的 scope 下分区。
- `config.yaml`、`SOUL.md`、独立记忆如何一起参与运行时：结构化配置负责模型和工具组，SOUL 负责身份与行为边界，scope/agent 记忆负责当前用户的长期上下文，三者在运行时共同影响主 Agent 的装配结果。
- 自定义 Agent 如何影响模型、工具白名单和系统提示词：`model` 会覆盖模型选择，`tool_groups` 会裁剪工具暴露范围，而 `SOUL` 与 Agent 级记忆会进入提示词编排。
- 删除 Agent 会删除什么：当前只删除全局 Agent 定义目录，也就是配置、SOUL 及该目录内的其他定义文件；各用户 scope 下的记忆会保留，重建同名 Agent 后仍可能重新使用。
- `USER.md` 当前处于什么状态：它是 legacy/operator 管理状态，当前不参与提示词注入，不能当作多租户用户画像。

## 2. 模块定位

lumen 的自定义 Agent 模块，不是“多开几个主图实例”，而是在同一套 lead agent 架构之上，引入一层按名称切换的角色配置系统。

它解决的是这样一类需求：

- 同一用户希望拥有多个不同人格或用途的 Agent
- 某个 Agent 需要使用不同的模型
- 某个 Agent 只允许使用部分工具组
- 某个 Agent 要有独立的长期记忆
- 某个 Agent 要注入独立的身份文本或行为边界

也就是说，这个模块的本质不是“复制一个新智能体后端”，而是“给同一个主 Agent 运行时加上一层角色化参数和文件布局”。

## 3. 核心设计：Agent 是文件系统对象

这个模块最关键的设计是：

自定义 Agent 不是存储在数据库里，也不是写在主配置文件里，而是存在于应用数据目录下的独立文件夹中。

一个 Agent 定义目录通常包含：

- `config.yaml`
- `SOUL.md`

其路径结构由 `Paths` 统一定义，大致位于：

- `{base_dir}/agents/{agent_name}/`

用户长期记忆位于另一棵目录树：

- `{base_dir}/memories/{memory_scope}/agents/{agent_name}/memory.json`

这说明自定义 Agent 的全局定义是一组文件，而用户画像是按认证 scope 隔离的派生状态。这样设计有几个直接好处：

- 调试直观，Agent 内容可以直接查看和手工修改
- 部署迁移简单，复制目录即可迁移 Agent
- 配置和人格便于部署；长期记忆不会因多个用户使用同一个 Agent 名称而共享

## 4. 代码分层与关键入口

与自定义 Agent 最相关的实现主要分布在以下位置：

- `runtimes/backend/src/config/agents_config.py`
  负责 Agent 配置、SOUL 读取与 Agent 列表扫描。

- `runtimes/backend/src/gateway/routers/agents.py`
  负责对外提供 CRUD 接口，以及 USER.md 的读写接口。

- `runtimes/backend/src/config/paths.py`
  定义 Agent 目录、scoped 记忆根和 legacy `USER.md` 的物理路径。

- `runtimes/backend/src/agents/lead_agent/agent.py`
  负责在运行时读取 `agent_name`，并用其影响模型解析、中间件构建与工具组筛选。

- `runtimes/backend/src/agents/lead_agent/prompt.py`
  负责把 SOUL 和 Agent 级记忆注入系统提示词。

这说明自定义 Agent 不是靠某一个模块单独完成，而是：

- 配置层负责发现和装载
- 网关层负责管理
- 运行时层负责消费

## 5. Agent 配置模型很轻量，但作用很大

`AgentConfig` 的字段非常少，只有：

- `name`
- `description`
- `model`
- `tool_groups`

这说明作者刻意把 Agent 配置保持在一个较小集合里。它没有把“所有行为差异”都塞进 config，而是把真正影响运行时的关键开关留下：

- `model` 影响模型选择
- `tool_groups` 影响工具暴露范围
- `description` 更多面向管理和展示
- `name` 则是 Agent 的稳定身份键

个性和行为边界被放进了 `SOUL.md`，而不是和结构化配置混在一起。这个分层很合理。

## 6. `config.yaml` 和 `SOUL.md` 为什么分开

这是自定义 Agent 模块里很重要的一个设计。

### 6.1 `config.yaml` 负责结构化控制

它承载的是：

- 模型覆盖
- 工具组白名单
- 展示描述

这些都属于程序可直接消费的结构化配置。

### 6.2 `SOUL.md` 负责人格与行为边界

SOUL 不是简单介绍文案，而是运行时会真正注入系统提示词的身份文本。它定义的是：

- Agent 的人格
- 价值观
- 行为边界
- 沟通风格

这类内容用 Markdown 文本来承载，比写进 YAML 更自然，也更适合被提示词层直接消费。

因此，自定义 Agent 的角色建模其实是“两层结构”：

- 配置层控制能力
- SOUL 层控制身份

## 7. Agent 是如何被加载出来的

### 7.1 名称校验与标准化

无论是配置加载还是网关 API，Agent 名称都必须符合固定模式，只允许字母、数字和短横线。并且最终会标准化为小写。

这样做的好处是：

- 避免文件系统路径混乱
- 保证跨平台兼容性
- 降低大小写导致的重复 Agent 风险

### 7.2 目录就是存在性判断

一个 Agent 是否存在，首先是通过目录和 `config.yaml` 是否存在来判断，而不是查某个中心注册表。

这说明作者采用的是“约定式文件布局即注册表”的策略。

### 7.3 配置解析中的兼容性处理

加载 `config.yaml` 时，系统不仅会解析 YAML，还会：

- 若未提供 `name`，则使用目录名作为默认值
- 在交给 Pydantic 之前剔除未知字段

这一点说明模块具备一定的向后兼容意识。旧版遗留字段不会立刻让 Agent 整体失效，而是先被过滤掉。

## 8. 运行时是如何消费自定义 Agent 的

### 8.1 `agent_name` 是运行时入口

在主 Agent 构建时，`make_lead_agent()` 会从 `RunnableConfig.configurable` 读取 `agent_name`。如果当前不是 bootstrap 模式，就会尝试加载对应的 Agent 配置。

这意味着：

- 自定义 Agent 不是通过不同 graph 名称区分
- 而是通过同一个主图在运行时切换角色配置

这个实现方式很经济，也更容易维护。

### 8.2 模型选择优先级会被 Agent 覆盖

主 Agent 的模型解析顺序是：

- 请求显式覆盖
- Agent 自身配置
- 全局默认模型

这说明自定义 Agent 可以拥有自己的默认模型，但调用方依然有能力在请求级覆盖它。

这种优先级设计很实用，因为它兼顾了：

- Agent 角色级默认行为
- 请求级临时灵活性

### 8.3 工具组白名单会影响最终工具空间

若 Agent 配置里指定了 `tool_groups`，主 Agent 在装配工具时会把这些组传给 `get_available_tools()`，从而只暴露这些组对应的工具。

这意味着自定义 Agent 的能力差异不仅体现在提示词上，还体现在真正的工具面上。

这是一个非常重要的设计，因为它让“角色差异”不只是文案差异，而是运行时能力边界差异。

## 9. 自定义 Agent 和记忆模块是怎么连起来的

### 9.1 中间件层传入 `agent_name`

`_build_middlewares()` 在构造记忆中间件时，会把 `agent_name` 传给 `MemoryMiddleware`。Runtime context 还必须包含 Backend 签发的 `memory_scope`。后续队列和文件路径以 `(memory_scope, agent_name)` 共同分区。

### 9.2 提示词注入也会按 Agent 隔离

静态 `apply_prompt_template()` 只装配不含用户画像的基础 prompt。`ScopedMemoryPromptMiddleware` 会在每次 model call 时从可信 Runtime context 读取 scope，并调用：

- `get_agent_soul(agent_name)`
- `_get_memory_context(memory_scope, agent_name)`

前者负责读 Agent 专属 SOUL，后者负责读 Agent 专属长期记忆。

这说明自定义 Agent 的个性化不只是静态 SOUL，还包括持续演化的 Agent 级长期记忆。

### 9.3 删除 Agent 的数据生命周期

网关删除接口直接删除 `{base_dir}/agents/{agent_name}/`，因此会删除 `config.yaml`、`SOUL.md` 和该定义目录内的其他文件。scoped 记忆不在这棵目录树中，不会被该接口清理。

这是当前合同中需要明确的保留语义：删除定义不会等价于删除所有用户派生数据。若产品需要隐私删除，应新增显式的、可审计的 scoped purge 协议，并处理正在运行的记忆任务；不能用未加锁的全目录扫描替代。

## 10. `SOUL.md` 是如何参与提示词编排的

当前实现里，`SOUL.md` 会被读取后包裹进 `<soul>` 片段，然后插入主系统提示词模板中。

这说明 SOUL 不是单独给前端展示的元数据，而是模型运行时真实可见的系统级上下文。

因此，SOUL 对 Agent 的作用并不是“介绍页”，而是：

- 行为约束
- 风格塑造
- 角色持久化

从技术视角看，它是一种文本型控制层。

## 11. `USER.md` 当前处于什么状态

这是一个值得单独指出的实现细节。

项目仍保留一个全局 `USER.md`：

- `Paths.user_md_file` 明确给出了这个文件路径
- 网关也提供了 `/user-profile` 的读写接口
- 当前 Runtime 提示词编排没有读取或注入它

这意味着：

- 它只是 Gateway 内部的 legacy/operator 管理状态
- 它不是 Backend 认证用户的画像，也不能在多租户运行路径中直接注入
- 真正参与注入的是 Backend 派生 scope 下的结构化长期记忆

如果写技术文档，这个差异必须说清楚。否则读者会误以为它已经和 SOUL、记忆一样进入运行时。

## 12. Gateway 如何管理自定义 Agent

### 12.1 列表与详情

网关可以列出所有 Agent，并按名称获取详情。详情接口会额外返回 `SOUL.md` 内容。

这说明系统对 Agent 的展示对象是“配置 + SOUL 的合体”，而不是只返回结构化配置。

### 12.2 创建流程

创建 Agent 时，网关会：

- 校验名称
- 创建 Agent 目录
- 写入 `config.yaml`
- 写入 `SOUL.md`

若过程失败，还会删除已经创建的目录，避免留下半成品。

这个失败回滚很重要，因为它保持了“目录存在即 Agent 存在”的约定完整性。

### 12.3 更新流程

更新时，系统会分别处理两类变更：

- 结构化配置字段更新 `config.yaml`
- 人格文本更新 `SOUL.md`

这再一次体现了配置和身份文本分层管理的设计。

### 12.4 删除流程

删除时直接清理整个 Agent 定义目录，而不是逐文件删除。它不会遍历或删除各用户 scope 下的 Agent 记忆。

## 13. 这个模块里的几个关键 tricks

如果把自定义 Agent 模块最有价值的工程技巧提炼出来，主要有以下几点。

### 13.1 用文件系统目录承载 Agent 实体

这样调试友好、迁移简单、结构自然分层。

### 13.2 用 `config.yaml` 和 `SOUL.md` 分层建模

能力控制归结构化配置，角色塑造归自由文本。

### 13.3 通过 `agent_name` 在同一主图上切换角色

避免复制整套 graph 或分裂成多个主运行时。

### 13.4 用户 scope 与 Agent 双层隔离

这让不同用户、不同自定义 Agent 都不会互相污染长期画像。

### 13.5 创建失败时回滚整个目录

保证“目录即对象”的约定始终成立。

## 14. 当前实现的能力边界

从现有代码看，这个模块也有一些明确边界。

- 自定义 Agent 并不是新的 graph 类型，而是同一 lead agent 的变体
- 配置字段比较克制，主要只支持模型和工具组差异
- `USER.md` 是未注入的 legacy/operator 状态，不是多租户画像接口
- 删除 Agent 定义不会清除各 scope 下的同名 Agent 记忆；隐私删除需要显式 purge 设计
- Agent 的运行隔离主要体现在提示词、工具组和长期记忆上，而不是独立代码执行栈

这些边界不是缺陷，而是说明该模块更像“角色层”而不是“独立平台”。

## 15. 这个模块的本质是什么

如果只看表面，lumen 的自定义 Agent 模块像是“多存几份配置文件”。但从实现上看，它真正完成的是：

- 让同一套主 Agent 运行时可以拥有多个角色实例
- 让每个角色拥有独立的能力边界、人格文本和长期记忆
- 让这些角色可以通过简单文件布局被创建、管理和删除

所以，这个模块不是单纯的管理接口，而是一层把“默认 Agent 后端”扩展成“多角色 Agent 系统”的运行时角色化机制。
