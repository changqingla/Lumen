# lumen MCP 模块技术说明

## 1. 文档目标

本文详细说明 lumen 中 MCP 模块的实现方式。重点不是介绍 MCP 协议本身，而是解释这个项目如何把 MCP 服务器接入到 Agent 运行时中，并保证它在多进程、异步、认证和热更新场景下仍然稳定可用。

阅读本文后，读者应能够回答以下问题：

- 这个项目里的 MCP 模块到底扮演什么角色
- MCP 配置为什么不放在普通 `config.yaml` 工具段里，而是单独建一套扩展配置
- 启用一个 MCP server 后，它是如何变成 Agent 可见工具的
- Gateway 与 LangGraph Server 分进程部署时，配置变更为什么还能生效
- OAuth 是如何接入 MCP HTTP/SSE 服务的
- 为什么 MCP 相关实现要同时处理缓存、事件循环和懒初始化

### 1.1 先给出六个直接答案

- 这个项目里的 MCP 模块扮演什么角色：它不是 MCP server，而是 MCP client 集成层，负责把外部 MCP server 暴露的能力桥接进本地 Agent 的工具空间。
- 为什么 MCP 配置不放进普通 `config.yaml` 工具段：因为 MCP server 不是简单的本地工具对象，它有连接、握手、认证、热更新和远程发现等生命周期需求，更适合放进独立的扩展配置模型中。
- 启用一个 MCP server 后，它如何变成 Agent 可见工具：系统会先把配置转成统一连接参数，再创建多服务 MCP 客户端，拉取远程工具列表，并把结果包装成 LangChain 兼容工具对象返回给 Agent。
- Gateway 和 LangGraph 分进程部署时，配置变更为什么还能生效：因为 MCP 关键装配点不会只依赖进程内缓存，而会结合配置文件读取和修改时间失效机制，让独立进程都能感知磁盘上的最新配置。
- OAuth 如何接入 MCP HTTP/SSE 服务：OAuth 配置是 server 级配置，运行时会先获取或刷新 token，再把认证头注入 HTTP/SSE 请求，供对应 MCP server 使用。
- 为什么 MCP 实现要同时处理缓存、事件循环和懒初始化：因为远程工具装配成本高、部分链路是异步的、而且不是每轮 Agent 调用都需要 MCP，所以系统必须在“首次需要时才加载”和“后续尽量复用”之间做平衡。

## 2. 模块定位

先给出一个很重要的判断。

lumen 中的 MCP 模块，不是一个 MCP server 实现，而是一个 MCP client 集成层。它的职责不是“对外暴露 MCP 能力”，而是“把外部 MCP server 暴露出来的能力拉进本地 Agent 工具空间”。

因此，它在整个系统里的位置更接近一个桥接层，连接的是三件事：

- 外部 MCP server
- 本地 Agent 的工具调用系统
- Gateway 提供的配置管理接口

从工程角度看，MCP 模块解决的是以下几个问题：

- 如何描述和持久化多个 MCP server 的配置
- 如何把不同传输方式统一成同一种内部连接参数
- 如何在 Agent 首次真正需要时才加载 MCP 工具
- 如何在配置文件被 Gateway 修改后，让独立进程里的 Agent 感知到变化
- 如何在 HTTP/SSE 场景下注入 OAuth 认证头
- 如何让子 Agent 也能安全使用异步 MCP 工具

## 3. 模块边界与代码分层

从代码结构上看，MCP 模块被拆成几层，每层职责都比较清晰。

- `runtimes/backend/src/config/extensions_config.py`
  负责扩展配置模型，MCP server 与技能状态都放在这里统一管理。

- `runtimes/backend/src/mcp/client.py`
  负责把配置模型转换成 `langchain-mcp-adapters` 需要的服务连接参数。

- `runtimes/backend/src/mcp/oauth.py`
  负责 OAuth token 获取、缓存、刷新和请求头注入。

- `runtimes/backend/src/mcp/tools.py`
  负责真正创建多服务 MCP 客户端并拉取工具对象。

- `runtimes/backend/src/mcp/cache.py`
  负责 MCP 工具缓存、懒初始化和基于配置文件修改时间的失效控制。

- `runtimes/backend/src/gateway/routers/mcp.py`
  负责对外暴露 MCP 配置读写接口，让前端或外部系统能修改配置。

如果只看 `get_mcp_tools()`，会觉得 MCP 很简单；但实际上真正让它可用的是这几层协同，而不是单点实现。

## 4. 为什么 MCP 配置单独建模

lumen 没有把 MCP server 配置塞进普通的 `config.yaml` 工具列表里，而是单独使用 `ExtensionsConfig` 管理。这个设计背后有几个很现实的原因。

第一，MCP server 和普通工具的生命周期不一样。普通工具通常只是一个本地对象路径，而 MCP server 是一个需要连接、握手、认证甚至远程发现工具元数据的外部能力源。

第二，MCP 配置需要被 Gateway 动态读写，而 `config.yaml` 更偏向静态启动配置。把两者分开后，系统可以在不重启主服务的前提下调整 MCP server 列表。

第三，MCP 配置和技能状态都属于“扩展层能力”，因此项目用 `extensions_config.json` 这一独立文件统一管理扩展态，而不是把所有配置都堆到一份主配置里。

第四，MCP 配置结构比普通工具复杂得多。它不仅需要 `enabled`、传输类型、地址或命令，还可能包含 OAuth 参数、请求头和描述信息，因此更适合独立模型。

## 5. 配置模型的内部结构

### 5.1 `ExtensionsConfig` 的双职责

`ExtensionsConfig` 同时管理两类内容：

- `mcp_servers`
- `skills`

这意味着它不是一个“纯 MCP 配置类”，而是扩展域的总配置模型。这样做的好处是 Gateway 在写回配置时，只需要维护一份扩展配置文件，不必拆成多个独立文件。

但这也带来一个实现上的约束：当 Gateway 更新 MCP 配置时，必须保留 `skills` 部分，避免覆盖掉无关扩展状态。后面配置写回流程里，正是按这个原则实现的。

### 5.2 单个 MCP server 的字段语义

单个 MCP server 配置由 `McpServerConfig` 表示，核心字段包括：

- `enabled`
- `type`
- `command`
- `args`
- `env`
- `url`
- `headers`
- `oauth`
- `description`

这里有一个明显的设计特点：它同时覆盖了本地启动型 server 和远程连接型 server。

也就是说，这个配置模型并没有假设 MCP 一定是 HTTP 服务，而是把 stdio、SSE、HTTP 统一放在同一层抽象下。这样上层装配逻辑就可以在一次遍历中处理所有 server，而不用按协议类型拆成不同系统。

### 5.3 OAuth 配置的粒度

OAuth 不是在全局层配置，而是挂在单个 MCP server 下。这说明项目认为认证是 server 级责任，而不是 MCP 全局责任。这是合理的，因为不同 MCP server 可能：

- 有的完全不需要认证
- 有的使用 `client_credentials`
- 有的使用 `refresh_token`
- 有的即使都走 OAuth，token 字段名和过期字段也未必一致

因此 `McpOAuthConfig` 的设计是比较“宽容”的，不仅允许自定义 token 字段名、token 类型字段名和 expires 字段名，还支持额外表单参数、audience 和 scope。这类设计明显是为了兼容不同 OAuth 提供方的差异。

## 6. 配置文件发现与环境变量解析

### 6.1 配置文件定位策略

`ExtensionsConfig.resolve_config_path()` 的查找顺序体现了 MCP 模块对多部署形态的适配。

优先级依次是：

- 显式传入的配置路径
- 环境变量 `LUMEN_EXTENSIONS_CONFIG_PATH`
- 当前目录下的 `extensions_config.json`
- 父目录下的 `extensions_config.json`
- 默认运行态路径 `runtimes/config/extensions/extensions_config.json`（仓库只跟踪同目录的 `extensions_config.example.json`）
- 为兼容旧版本而保留的历史位置与文件名

这个顺序的意义在于：

- 嵌入式调用场景可以直接指定路径
- 容器部署场景可以通过环境变量控制
- 本地开发场景可以使用默认运行态路径；文件不存在时以空配置启动
- 老版本用户可以继续沿用旧文件名，不会直接失效

### 6.2 环境变量替换的一个小技巧

扩展配置加载后会递归解析环境变量。凡是以 `$` 开头的字符串，都会尝试从环境中取值。

这里有一个很实用的 trick：如果环境变量不存在，系统不会保留字面量 `$VAR`，而是写成空字符串。这样做是为了避免下游 MCP client 把占位符文本误当成真实 header 或 secret 发出去。

这个细节看起来小，但在认证场景里很关键。否则很多“为什么请求头里出现了 `$TOKEN` 字符串”的问题会很难排查。

## 7. 传输参数是如何标准化的

`runtimes/backend/src/mcp/client.py` 的职责很集中，就是把项目内部的配置模型转换成 `langchain-mcp-adapters` 需要的 server 参数字典。

### 7.1 三类传输统一归一

系统当前支持三类传输：

- `stdio`
- `sse`
- `http`

标准化时采取的是分支映射策略：

- `stdio` 必须提供 `command`，可以附带 `args` 和 `env`
- `sse` 与 `http` 必须提供 `url`，可以附带 `headers`

这一步的意义不是简单改字段名，而是把配置模型里的语义约束前置到构建阶段。也就是说，MCP server 配置是否成立，不必等到真正发起连接时才暴露。

### 7.2 失败隔离而不是整体失败

`build_servers_config()` 有一个很好的工程取向：它会逐个尝试构建启用的 server 配置，遇到无效项只记录错误并跳过，而不会让整个 MCP 系统失效。

这意味着在多个 MCP server 并存时，某一个配置写坏，不会拖垮其他正常 server。对于面向运营配置的系统，这是非常重要的稳定性策略。

换句话说，lumen 对 MCP 配置的处理是“局部容错”，而不是“全有或全无”。

## 8. MCP 工具是如何被发现并注入 Agent 的

### 8.1 真实入口在工具装配阶段

MCP 工具并不是独立挂到 Agent 上的，而是在 `get_available_tools()` 里和普通工具、内建工具一起合并。

这个过程大致是：

- 读取当前扩展配置文件
- 判断是否存在启用的 MCP server
- 若存在，则从缓存层获取 MCP 工具列表
- 将这些工具拼接到普通工具和内建工具之后

因此，从 Agent 视角看，MCP 工具和本地工具没有本质差别。模型并不会知道某个工具来自远端 MCP server，还是来自本地 Python 模块。

这就是项目采用 MCP 的关键目标之一：把远程能力以“本地工具”的形态透明注入给 Agent。

### 8.2 为什么在这里读磁盘而不是只读内存配置

这里有一个非常关键的实现选择：装配工具时，不是只使用进程内缓存的扩展配置，而是重新调用 `ExtensionsConfig.from_file()` 读磁盘文件。

原因很直接：

- Gateway 负责更新扩展配置
- Agent 运行在 LangGraph Server 进程
- 两者是独立进程

如果只依赖内存配置，Gateway 改了配置以后，LangGraph 进程感知不到。通过在关键装配点重读磁盘，MCP 模块就获得了跨进程一致性。

这不是最复杂的同步方案，但对于这种低频变更、高价值一致性的场景来说，非常实用。

## 9. MCP 客户端创建逻辑

`runtimes/backend/src/mcp/tools.py` 是真正把配置变成可用工具的核心层。

它的主要流程可以概括为：

第一步，检查 `langchain-mcp-adapters` 是否安装。如果依赖不存在，直接返回空列表，并打印安装提示。

第二步，从磁盘读取最新扩展配置，并构建启用 server 的参数映射。

第三步，如果没有启用的 server，直接返回空列表。

第四步，为 HTTP/SSE 类型 server 预先准备 OAuth 授权头。

第五步，构造 OAuth 工具拦截器。

第六步，创建 `MultiServerMCPClient`。

第七步，从所有 server 拉取工具对象并返回。

这说明 MCP 模块不是在服务启动时一次性完成所有工作，而是在真正需要工具时，按“连接配置 -> 认证准备 -> 客户端初始化 -> 工具发现”的顺序完成装配。

## 10. 为什么 OAuth 要做成两段式注入

OAuth 这一段是 MCP 模块里最有技巧的实现之一。

很多系统在接入远程服务时，只会在“工具调用时”加认证头。但在 MCP 这里，这样做是不够的，因为 MCP client 在真正拿到工具之前，就可能已经需要建立连接或执行发现流程。

lumen 为此做了两段式处理。

### 10.1 第一段：连接建立前的初始授权头

`get_initial_oauth_headers()` 会在 MCP client 初始化之前，为所有启用了 OAuth 的 server 先取一次 token，并把 `Authorization` 写进 server 配置的请求头里。

这一步的目标是保证：

- 工具发现阶段能通过认证
- 初始连接建立时就带上有效 token

如果没有这一步，很多需要认证的 HTTP/SSE MCP server 在“还没来得及进入工具拦截器逻辑”之前就已经连接失败了。

### 10.2 第二段：工具调用时的动态授权头

仅仅有初始头还不够，因为 token 会过期。于是项目又通过 `build_oauth_tool_interceptor()` 构造了工具级拦截器，在每次请求经过时按 server_name 动态注入最新 token。

这样两段配合后，系统就同时解决了两个问题：

- 首次连接怎么认证
- 长时间运行后 token 过期怎么续期

这是一个很典型的工程 trick：把“启动时鉴权”和“运行时鉴权”拆开处理，而不是希望某一层逻辑兼顾全部场景。

## 11. OAuth token 管理器的实现思路

`OAuthTokenManager` 的实现相当克制，但关键点都做到了。

### 11.1 缓存策略

它以 server 为粒度缓存 token，每个 server 对应一个 `_OAuthToken`，其中记录：

- access token
- token type
- expires_at

这意味着 token 生命周期完全在内存里管理，不需要引入外部缓存或数据库。对 MCP 这种进程内客户端场景来说，这样已经足够。

### 11.2 提前刷新而不是等到过期

判断 token 是否需要刷新时，系统不是看“现在是否已经超过过期时间”，而是看“是否进入了刷新窗口”。这个窗口由 `refresh_skew_seconds` 控制。

这个细节很重要，因为真实网络环境下如果等 token 刚过期再刷新，就会把部分请求暴露在过期边界上，导致偶发 401。提前刷新是一种很常见、也很实用的稳定性策略。

### 11.3 每个 server 一把锁

Token manager 为每个 server 建立了一把 `asyncio.Lock`。这解决的是并发刷新风暴问题。

假设多个协程同时发现 token 即将过期，如果没有锁，就会并发向 token endpoint 发起多次刷新请求。当前实现会让第一个协程进入刷新，其他协程等待，刷新完成后直接复用结果。

这个技巧虽然简单，但在高并发工具调用场景里非常关键。

### 11.4 对不同 OAuth 提供方保持宽容

Token manager 并没有写死响应格式，而是允许配置：

- access token 字段名
- token type 字段名
- expires_in 字段名
- 默认 token type

同时，若 `expires_in` 无法解析，会回退到一个保守默认值。这说明实现者非常清楚不同 OAuth 服务端的响应格式经常不完全一致，因此刻意把兼容性设计进了配置层。

## 12. MCP 工具缓存为什么单独存在

如果每次 Agent 调用都重新初始化 MCP client 并重新拉取工具，理论上功能也成立，但工程上会很差。原因包括：

- 建立远程连接有开销
- 工具发现本身可能是一个网络过程
- OAuth 初始化也有额外请求成本

因此项目用了 `runtimes/backend/src/mcp/cache.py` 做一层专门缓存。

### 12.1 缓存的三种状态

这层缓存本质上维护三项状态：

- 当前是否已经初始化
- 当前缓存的工具列表
- 上次初始化时配置文件的修改时间

这说明它不是“永不失效的单例缓存”，而是带版本感知的运行时缓存。

### 12.2 为什么用配置文件 mtime 作为失效信号

缓存是否过期，不是通过复杂的事件广播来判断，而是直接比较扩展配置文件的修改时间。

这种设计有几个优点：

- 简单直接，不依赖跨进程消息系统
- 对 Gateway 与 LangGraph Server 分进程部署天然友好
- 对本地开发和嵌入式使用都兼容

当 Gateway 更新 `extensions_config.json` 后，LangGraph 进程不需要被主动通知。下次访问缓存时，只要发现 mtime 变了，就会自动重建工具缓存。

这个策略非常适合“配置更新不高频，但必须最终一致”的场景。

### 12.3 为什么 Gateway 更新配置时不直接清缓存

`gateway/routers/mcp.py` 在更新配置后，特意写了一个注释：这里不手动重置 MCP 工具缓存。

原因就在于缓存真正存在于 LangGraph Server 进程，而不是 Gateway 进程。Gateway 就算本地清掉自己进程里的状态，也影响不了真正执行 Agent 的那边。

因此这个项目选择了更合理的责任边界：

- Gateway 只负责把配置写对
- LangGraph 进程自己通过 mtime 检测决定何时失效缓存

这体现了一个很成熟的设计观念：不要在错误的进程里做无效的缓存控制。

## 13. 懒初始化与事件循环兼容

### 13.1 为什么不能在 Gateway 启动时初始化 MCP 工具

`gateway/app.py` 明确说明了一个决定：Gateway 启动时不初始化 MCP 工具。

原因有两点：

- Gateway 本身不直接执行 MCP 工具
- 真正使用工具的是 LangGraph Server，而且两者是独立进程

如果在 Gateway 启动时初始化，不仅没有收益，还会制造“配置已加载但真正执行端缓存未同步”的假象。因此系统选择按需初始化，这更符合 MCP 工具的真实使用路径。

### 13.2 为什么要兼容已有事件循环

MCP 工具初始化是异步行为，但 `get_cached_mcp_tools()` 对外提供的是同步入口。为了兼容不同宿主环境，它必须处理三种情况：

- 当前没有事件循环
- 当前有事件循环但未运行
- 当前已有正在运行的事件循环

最麻烦的是第三种。如果直接在当前线程里再运行一次异步初始化，往往会报错。项目的处理方式是：如果当前循环已在运行，就开一个新线程，在那个线程里启动新的事件循环完成初始化。

这个实现不是为了“优雅”，而是为了让 MCP 工具在 LangGraph Studio、嵌入式客户端和不同运行上下文里都能稳定工作。

## 14. Gateway 如何管理 MCP 配置

### 14.1 读接口很简单，但作用很重要

Gateway 暴露了 Runtime 内部 `/api/mcp/config` 读取接口，返回当前所有 MCP server 配置。这个接口本身不复杂，但它的意义在于让受限管理流程可以查询“系统当前认为自己有哪些 MCP server”。环境变量、请求头、OAuth secret、refresh token 和额外 token 参数只返回统一掩码，不会把运行时解析后的值暴露给管理面。

### 14.2 写接口的关键点不在于写文件，而在于保留其他扩展状态

更新接口在写配置时，并不是简单把请求体原样落盘，而是：

- 找到当前扩展配置文件路径
- 读取当前配置
- 保留 `skills` 配置
- 只替换 `mcpServers` 部分
- 将结果写回 JSON 文件
- 重载当前进程内的扩展配置缓存

更新时必须读取未解析环境变量的 JSON 原文。GET 响应中的掩码表示保留原值，
写入使用同目录临时文件和原子替换。这样切换 MCP 或 Skill 状态时不会把
`$ENV_VAR` 对应的明文秘密写回磁盘，也不会让另一个进程读到半个 JSON 文件。
整个读改写还由进程锁和文件锁串行化，MCP 更新与 Skill 启停并发发生时不会
用各自的旧快照覆盖另一方；替换后会 `fsync` 文件与目录再返回成功。

这里最重要的点是“保留 skills 部分”。因为 `extensions_config.json` 不是只服务 MCP，如果更新 MCP 时把技能状态一起覆盖掉，就会造成无关模块的配置损坏。

### 14.3 为什么扩展配置使用独立目录

仓库只提供 `runtimes/config/extensions/extensions_config.example.json` 作为格式参考，真实
部署文件位于同目录的 `extensions_config.json` 并由 Git 忽略。Docker 部署只把
`runtimes/config/extensions/` 对应的容器目录作为可写目录挂给 Gateway，主配置目录仍保持只读；
LangGraph 以只读方式挂载同一目录。目录级挂载也允许 Gateway 通过临时文件
加原子替换更新配置，避免单文件 bind mount 无法被 `replace()` 的问题，也避免将 MCP
header、OAuth token 或 stdio 环境变量误提交到源码仓库。

显式部署路径尚不存在时，Gateway 与 LangGraph 先以空配置启动；首次管理更新会以
`0600` 权限在该路径原子创建配置，LangGraph 的 mtime 缓存会检测文件创建并重载。

## 15. 子 Agent 为什么也能使用 MCP 工具

这部分是很多人第一次读代码时容易忽略的点。

子 Agent 执行器 `SubagentExecutor` 在同步执行包装里，会显式使用 `asyncio.run()` 启动新的事件循环。注释里明确提到，这样做是为了允许在线程池环境中正常使用仅支持异步的工具，例如 MCP 工具。

这说明实现者已经预先考虑到一个现实问题：

- 子 Agent 常常跑在线程池里
- 线程池里的线程默认没有事件循环
- 而 MCP 工具链路本身依赖异步初始化和异步请求

因此，如果没有这一层处理，主 Agent 也许能用 MCP，子 Agent 却会在调用时出问题。

换句话说，MCP 模块虽然代码量不大，但它的可用性依赖于子 Agent 执行框架在事件循环层面的兼容设计。

## 16. 这个模块里的几个关键 tricks

如果把 MCP 模块里最有价值的工程技巧提炼出来，大致有下面这些。

### 16.1 把“扩展配置”与“主配置”分开

这样既方便 Gateway 动态修改，也降低了主配置的耦合度。

### 16.2 用磁盘文件做跨进程一致性桥梁

Gateway 与 LangGraph Server 不共享内存，就不要假设彼此能同步缓存。通过扩展配置文件和 mtime 做桥梁，是非常务实的方案。

### 16.3 初始请求头和运行时拦截器双管齐下

这让 OAuth 既覆盖了连接建立，也覆盖了后续请求续期。

### 16.4 逐个 server 容错

单个错误 server 不拖垮整个 MCP 模块，提升了整体可用性。

### 16.5 用每个 server 一把锁避免 token 刷新风暴

这是并发认证场景里非常常见、也非常值得保留的模式。

### 16.6 把异步初始化包装成同步可调用接口

这样主工具装配逻辑不需要全链路异步化，但仍然能兼容异步 MCP client。

## 17. 当前实现的能力边界

从现有代码看，MCP 模块当前有一些明确边界。

- 只支持 `stdio`、`sse` 和 `http` 三类传输，不支持例如 `websocket`
- OAuth 只针对 HTTP/SSE 场景设计，stdio server 不涉及这套认证链路
- 工具发现与缓存都在进程内完成，没有做分布式共享缓存
- 配置热更新是“下次访问时生效”，不是强实时广播

这些都不是缺陷，而是当前架构下很合理的取舍。它说明这个模块优先追求的是：清晰、稳定、可维护，而不是在所有维度都做成最复杂的版本。

## 18. 这个模块的本质是什么

如果只从文件列表看，lumen 的 MCP 模块像是“读个配置、连个客户端、拿一组工具”。但从实现上看，它真正做的是把一个原本很松散的外部协议能力，封装成了 Agent 运行时里一等公民的工具来源。

它完成了以下几件关键事情：

- 为 MCP server 建立独立、可持久化、可动态更新的配置模型
- 把不同传输方式统一抽象成同一种内部连接参数
- 通过 `langchain-mcp-adapters` 把远程能力翻译成 LangChain 工具对象
- 用 OAuth token 管理器处理远程认证与续期
- 用缓存与 mtime 机制平衡性能和跨进程一致性
- 用事件循环兼容与子 Agent 适配保证异步工具真正可用

所以，MCP 模块在这个项目里不是一个附属插件，而是一套完整的“远程工具接入运行时”。它让 lumen 的工具系统从“本地配置的若干工具”扩展成了“可动态接入外部能力网络”的架构。
