# Lumen Insight Runtime Backend

`runtimes/backend/` 只负责 Insight 运行时后端源码与开发入口。

如果你要看接口定义、整体架构或专项技术设计，请直接看仓库根目录的 `docs/`；这里保留开发者启动、调试和目录说明。

## 运行角色

- `LangGraph Server`：负责 `threads/runs` 等 Agent loop 运行时能力，默认端口 `2024`
- `Gateway API`：负责模型、技能、上传、记忆、建议问题等 REST 接口，默认端口 `8001`
- `Sandbox Provisioner`：唯一持有 Docker socket 的最小容器控制面，默认内网端口 `8002`

生产部署不把这两个控制面直接暴露给浏览器。聊天 run、join、cancel 和产物下载均由业务后端先校验会话所有权，再访问 Runtime 内网地址。

LangGraph Run 还会收到业务后端签发的短期 `usage_context`。`src/usage/` 从最终 `AIMessage.usage_metadata` 或兼容的 `response_metadata` 捕获 lead、subagent、摘要和标题调用；provider 不提供用量时才使用 Runtime 内部估算。事件同步提交到 `LUMEN_USAGE_REPORT_URL`，失败会阻止 Run 的 quota reservation 被正常释放。该 context 只放在运行上下文中，不写入 checkpoint、模型配置或 tracing metadata。

## 关键入口

- 图入口：`langgraph.json`
- Agent 构建入口：`src/agents/`
- Gateway 入口：`src/gateway/app.py`
- Docker Sandbox Provisioner 入口：`src/sandbox_provisioner/app.py`
- 后端测试：`tests/`

## 本地开发

在仓库根目录准备运行时配置：

```bash
cp runtimes/config/config.example.yaml runtimes/config/config.yaml
```

启动整套运行时：

```bash
./runtimes/scripts/start.sh
```

如果只在后端目录单独调试：

```bash
cd runtimes/backend
uv run langgraph dev --no-browser --allow-blocking --no-reload --n-jobs-per-worker 5
uv run python -m src.gateway.run
```

本地启动前需在 `runtimes/config/.env` 设置静态模型、搜索工具、MCP 和渠道所需的
provider secret。Compose 中 Gateway/LangGraph 只继承这个 Runtime 专用环境文件，
不会继承包含 JWT、SMTP、MinerU 等业务秘密的 `backend/.env`。

`runtimes/config/extensions/extensions_config.example.json` 仅说明 MCP/Skill 扩展格式。
真实 `extensions_config.json` 是被 Git 忽略的部署态；未创建时 Runtime 以空扩展配置
启动，首次 Gateway 管理更新会以 `0600` 权限原子创建它。已安装 custom skills 同样
属于部署态，需要随源码发布的技能应移入 `runtimes/skills/public/`。

同时必须设置
`GATEWAY_INTERNAL_API_TOKEN`。Gateway 仅允许 `/health` 匿名访问，其余 `/api`
请求必须携带匹配的 `X-Gateway-Internal-Token`；本地 Backend 使用同一个值。
动态用户模型解析还需要独立的 `MODEL_RESOLVER_INTERNAL_TOKEN`，仅 Backend 与
LangGraph 共享；该接口会返回解密后的 provider 配置，不能复用 RAG、Gateway 或
Provisioner token。
使用 provisioner 模式时还必须独立设置至少 32 位可打印 ASCII
`SANDBOX_PROVISIONER_INTERNAL_TOKEN`。Provisioner 仅允许 `/health` 匿名，其他请求
必须携带 `X-Sandbox-Provisioner-Token`；模板 token 会在启动阶段被拒绝。

说明：
- 当前运行时代码里仍有同步 `requests`、`subprocess.run`、文件读写等路径，因此 `LangGraph Server` 默认保留 `--allow-blocking`。
- Docker Compose 通过 `LUMEN_LANGGRAPH_ALLOW_BLOCKING` 控制这个开关，默认值是 `true`。
- 只有在这些同步路径被逐步改成异步或移到线程池之后，才适合把该开关切回 `false`。

## 测试

```bash
cd runtimes/backend
pytest -q
```

## 源码结构

```text
backend/
├── src/         运行时核心实现
├── tests/       回归测试
├── langgraph.json
├── pyproject.toml
└── uv.lock
```

`src/` 内部按职责拆分为 `agents/`、`gateway/`、`sandbox/`、`skills/`、`tools/`、`subagents/` 等模块，这是运行时内部的必要分层，不再额外复制到仓库级说明里。

## 相关文档

- [后端 HTTP 接口](../../docs/后端HTTP接口文档.md)
- [Gateway 与 LangGraph 架构](../../docs/Gateway与LangGraph架构技术文档.md)
- [智能体架构](../../docs/智能体架构技术文档.md)
