# Lumen Insight Runtime Backend

`runtimes/backend/` 只负责 Insight 运行时后端源码与开发入口。

如果你要看接口定义、整体架构或专项技术设计，请直接看 `docs/lumen/`；这里保留的是开发者真正需要的启动、调试和目录说明。

## 运行角色

- `LangGraph Server`：负责 `threads/runs` 等 Agent loop 运行时能力，默认端口 `2024`
- `Gateway API`：负责模型、技能、上传、记忆、建议问题等 REST 接口，默认端口 `8001`

## 关键入口

- 图入口：`langgraph.json`
- Agent 构建入口：`src/agents/`
- Gateway 入口：`src/gateway/app.py`
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

- `docs/lumen/后端HTTP接口文档.md`
- `docs/lumen/Gateway与LangGraph架构技术文档.md`
- `docs/lumen/智能体架构技术文档.md`
