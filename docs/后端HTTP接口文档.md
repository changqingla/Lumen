# lumen 后端 HTTP API 接口文档

## 1. 文档目标

本文用于整理 lumen 项目的公网业务 API 与 Runtime 内部 HTTP 协议，帮助前端开发、后端联调和日常排障。两类接口的信任边界不同，不能把 Runtime 路由当作公网 API。

需要先说明一个关键前提：

- `backend/` 业务后端是公网请求的授权点，容器端口为 `13000`。
- Runtime `Gateway` 是配置与线程资源内部服务，端口为 `8001`。
- `LangGraph Server` 是 Agent 执行内部服务，端口为 `2024`。

因此，本文同时保留：

- 浏览器可访问的业务后端合同
- 仅供服务间调用的 Gateway 与 LangGraph 协议说明

本文依据的主要实现来源包括：

- `runtimes/backend/src/gateway/app.py`
- `runtimes/backend/src/gateway/routers/*.py`
- `runtimes/backend/langgraph.json`
- `backend/modules/chat/runtime_controller.py`
- `backend/modules/chat/runtime_run_controller.py`
- `backend/modules/auth/controller.py`
- `docker/nginx/lumen.conf.template`
- `frontend/src/shared/api/client.ts`

## 2. 服务与基础地址

在默认 Docker 部署中：

- 浏览器通过 Nginx 同域访问 `/api/*`，Nginx 将其转发给业务后端。
- Gateway 的 `http://localhost:8001` 与 LangGraph 的 `http://localhost:2024` 只是本机调试或容器内部地址，默认绑定本机地址。
- Nginx 对 `/threads/*` 和 `/api/threads/*` 显式返回 `404`，不会转发到 LangGraph 或 Gateway。

浏览器使用的 Agent Runtime 公网入口是 session-scoped 业务代理：

- `/api/chat-runtime/sessions/{session_id}/...`
- `/api/chat/sessions/{session_id}/artifacts/download`

### 2.1 Gateway 内部文档入口

Gateway 已启用 FastAPI 自带文档，以下地址只用于内部联调：

- Swagger UI: `GET /docs`
- ReDoc: `GET /redoc`
- OpenAPI JSON: `GET /openapi.json`

### 2.2 认证与服务边界

业务后端的聊天与 Runtime 代理接口先验证当前身份，再查询该身份拥有的 `session_id`，并从 session 服务端状态推导 Runtime `thread_id`。客户端声明的任意 `thread_id` 不能作为授权依据。

Runtime Gateway 本身不承担最终用户鉴权，因此必须保持为内部服务，不能因为它有 FastAPI 路由就直接暴露到公网。除匿名 `GET /health` 外，Gateway 的所有 `/api` 路由都要求内部服务请求头：

```http
X-Gateway-Internal-Token: <GATEWAY_INTERNAL_API_TOKEN>
```

Gateway 在 token 缺失时拒绝启动，并使用恒定时间比较校验请求。Compose 从
`docker/.env` 读取一个必填值并注入 Backend、Gateway 与 LangGraph；该 token
不得由浏览器提交、写入 URL、响应或日志。直接在本机联调 Gateway 时，应在
`backend/.env` 与 `runtimes/config/.env` 配置完全相同的随机值。

游客模式使用服务端签发的不可伪造令牌：

- `POST /api/auth/guest-session` 签发或复用游客会话令牌。
- 后续聊天请求通过 `X-Guest-Token` 携带该令牌。
- 不接受客户端自行生成的 `X-Guest-Id` 作为身份。

### 2.3 通用错误格式

大多数接口在失败时使用 FastAPI 默认错误结构：

```json
{
  "detail": "具体错误信息"
}
```

常见状态码包括：

- `400`：请求参数错误、路径错误、文件格式不合法
- `403`：访问被拒绝，通常用于路径安全校验失败
- `404`：资源不存在
- `409`：资源冲突，例如技能或自定义 Agent 已存在
- `422`：参数格式不符合校验规则
- `500`：服务端内部异常
- `503`：依赖服务未启动，例如渠道服务未运行

## 3. Gateway 内部 API 总览

以下 Gateway 接口是 Runtime 内部协议，不经 Nginx 向浏览器暴露。Gateway 目前注册的接口模块包括：

- `models`
- `mcp`
- `memory`
- `skills`
- `artifacts`
- `uploads`
- `agents`
- `suggestions`
- `channels`
- `health`

## 4. Health 接口

### 4.1 获取网关健康状态

- 方法：`GET`
- 路径：`/health`
- 说明：返回 Gateway 自身健康状态

响应示例：

```json
{
  "status": "healthy",
  "service": "lumen-gateway"
}
```

## 5. Models 接口

模型接口用于给前端展示可选模型列表，以及查看某个模型的元信息。

### 5.1 获取全部模型

- 方法：`GET`
- 路径：`/api/models`
- 说明：返回系统中所有可用模型的展示信息，不包含密钥等敏感配置

响应示例：

```json
{
  "models": [
    {
      "name": "gpt-5",
      "display_name": "GPT-5",
      "description": "默认主模型",
      "supports_thinking": true,
      "supports_reasoning_effort": true
    }
  ]
}
```

字段说明：

- `name`：模型唯一标识
- `display_name`：前端展示名称
- `description`：模型描述
- `supports_thinking`：是否支持 thinking 模式
- `supports_reasoning_effort`：是否支持 reasoning effort

### 5.2 获取单个模型详情

- 方法：`GET`
- 路径：`/api/models/{model_name}`
- 说明：根据模型名称读取单个模型详情

路径参数：

- `model_name`：模型名

成功响应结构与 `/api/models` 中单个模型对象一致。

失败场景：

- `404`：模型不存在

## 6. MCP 接口

MCP 接口用于管理 `runtimes/config/extensions/extensions_config.json` 中的 Model Context Protocol 服务配置。Gateway 负责写入该文件，LangGraph 在组装 MCP 工具时读取同一份配置。

### 6.1 获取 MCP 配置

- 方法：`GET`
- 路径：`/api/mcp/config`
- 说明：返回当前全部 MCP 服务配置

响应结构：

```json
{
  "mcp_servers": {
    "server_name": {
      "enabled": true,
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "some-mcp-server"],
      "env": {},
      "url": null,
      "headers": {},
      "oauth": null,
      "description": "服务说明"
    }
  }
}
```

`oauth` 对象支持的主要字段包括：

- `enabled`
- `token_url`
- `grant_type`
- `client_id`
- `client_secret`
- `refresh_token`
- `scope`
- `audience`
- `token_field`
- `token_type_field`
- `expires_in_field`
- `default_token_type`
- `refresh_skew_seconds`
- `extra_token_params`

安全约定：`env`、`headers`、`oauth.client_secret`、
`oauth.refresh_token` 与 `oauth.extra_token_params` 的值不会原样返回，
存在值时统一显示为 `********`。服务名、字段名和非秘密元数据仍可读取。

### 6.2 更新 MCP 配置

- 方法：`PUT`
- 路径：`/api/mcp/config`
- 说明：将新的 MCP 配置写回 `runtimes/config/extensions/extensions_config.json`，然后重载配置缓存

请求体示例：

```json
{
  "mcp_servers": {
    "my-server": {
      "enabled": true,
      "type": "http",
      "command": null,
      "args": [],
      "env": {},
      "url": "https://example.com/mcp",
      "headers": {
        "Authorization": "Bearer xxx"
      },
      "oauth": null,
      "description": "示例 MCP 服务"
    }
  }
}
```

说明：

- 写入时会保留现有 `skills` 配置，不会被 MCP 更新覆盖
- 将 GET 返回的 `********` 原样放入 PUT 时表示保留该字段在配置文件中的原始值；
  新增秘密建议使用 `$ENV_VAR` 引用
- 更新过程读取未解析的 JSON 原文并原子替换文件，不会把环境变量解析后的秘密写回磁盘
- Gateway 不会直接初始化 MCP 工具，LangGraph 进程会在后续按需重载

失败场景：

- `500`：配置写入失败或配置重载失败

## 7. Memory 接口

Memory 接口用于读取和刷新一个明确的长期记忆分区。长期记忆不是全局文件：业务 Backend 会根据已认证用户派生 opaque `memory_scope`，Runtime 再在该 scope 内按可选 `agent_name` 分区。生产环境中的 Gateway `/api` 仅供携带独立内部 token 的服务访问，不应由浏览器直接调用。

所有读取数据的 Memory 接口都不会从缺失或非法 scope 回退：

- `memory_scope`：必填，恰好 64 个小写十六进制字符，由 Backend 签发
- `agent_name`：可选，1 到 64 个字母、数字或连字符；用于 scope 内的 Agent 子分区
- guest 没有 `memory_scope`，不读、不写、不注入持久记忆
- 客户端在 run payload 中伪造的 scope 会被 Backend 删除或覆盖

### 7.1 获取当前记忆数据

- 方法：`GET`
- 路径：`/api/memory`
- Query：`memory_scope`（必填）、`agent_name`（可选）
- 说明：返回指定用户/Agent 分区的当前记忆数据

响应结构：

```json
{
  "version": "1.0",
  "lastUpdated": "2026-03-24T10:00:00Z",
  "user": {
    "workContext": {
      "summary": "",
      "updatedAt": ""
    },
    "personalContext": {
      "summary": "",
      "updatedAt": ""
    },
    "topOfMind": {
      "summary": "",
      "updatedAt": ""
    }
  },
  "history": {
    "recentMonths": {
      "summary": "",
      "updatedAt": ""
    },
    "earlierContext": {
      "summary": "",
      "updatedAt": ""
    },
    "longTermBackground": {
      "summary": "",
      "updatedAt": ""
    }
  },
  "facts": [
    {
      "id": "fact-1",
      "content": "用户偏好使用中文",
      "category": "context",
      "confidence": 0.9,
      "createdAt": "2026-03-24T10:00:00Z",
      "source": "thread-id"
    }
  ]
}
```

### 7.2 强制重载记忆

- 方法：`POST`
- 路径：`/api/memory/reload`
- Query：`memory_scope`（必填）、`agent_name`（可选）
- 说明：从对应 scoped 存储文件重新读取并刷新进程内缓存

该接口主要供受控运维或内部调试使用。正常原子写入会通过文件签名使缓存自动失效；外部工具若修改文件，必须先确认目标 scope 的真实所有者。

响应结构与 `GET /api/memory` 相同。

### 7.3 获取记忆配置

- 方法：`GET`
- 路径：`/api/memory/config`
- 说明：返回记忆系统配置

响应字段：

- `enabled`
- `storage_path`
- `debounce_seconds`
- `max_facts`
- `fact_confidence_threshold`
- `injection_enabled`
- `max_injection_tokens`

### 7.4 获取记忆状态

- 方法：`GET`
- 路径：`/api/memory/status`
- Query：`memory_scope`（必填）、`agent_name`（可选）
- 说明：一次返回“记忆配置 + 指定分区的当前数据”

响应结构：

```json
{
  "config": {
    "enabled": true,
    "storage_path": "",
    "debounce_seconds": 30,
    "max_facts": 100,
    "fact_confidence_threshold": 0.7,
    "injection_enabled": true,
    "max_injection_tokens": 2000
  },
  "data": {
    "version": "1.0",
    "lastUpdated": "",
    "user": {},
    "history": {},
    "facts": []
  }
}
```

默认存储目录是 `{LUMEN_HOME}/memories/{memory_scope}/`。若 `storage_path` 使用历史 JSON 文件名（例如 `memory.json`），实际 scoped 数据写到相邻的 `memory.json.scoped/` 目录。旧 `{LUMEN_HOME}/memory.json` 永不自动读取或迁移，避免跨用户数据泄漏。

## 8. Skills 接口

Skills 接口用于查看技能、启停技能，以及从 `.skill` 压缩包安装技能。

### 8.1 获取全部技能

- 方法：`GET`
- 路径：`/api/skills`
- 说明：返回 public 与 custom 技能目录中的全部技能，包含已禁用项

响应示例：

```json
{
  "skills": [
    {
      "name": "figma",
      "description": "Use the Figma MCP server...",
      "license": null,
      "category": "public",
      "enabled": true
    }
  ]
}
```

### 8.2 获取单个技能详情

- 方法：`GET`
- 路径：`/api/skills/{skill_name}`
- 说明：按技能名称返回技能元信息

失败场景：

- `404`：技能不存在

### 8.3 更新技能启用状态

- 方法：`PUT`
- 路径：`/api/skills/{skill_name}`
- 说明：通过修改 `runtimes/config/extensions/extensions_config.json` 更新技能启用状态

请求体：

```json
{
  "enabled": false
}
```

响应结构与单个技能详情一致。

失败场景：

- `404`：技能不存在
- `500`：配置写入失败或重载失败

### 8.4 安装技能

- 方法：`POST`
- 路径：`/api/skills/install`
- 说明：从线程用户目录中的 `.skill` 文件安装技能

请求体：

```json
{
  "thread_id": "thread-123",
  "path": "mnt/user-data/outputs/my-skill.skill"
}
```

说明：

- `.skill` 文件本质是 ZIP 压缩包
- 压缩包中必须包含合法的 `SKILL.md`
- `SKILL.md` 的 YAML 头部至少需要 `name` 和 `description`
- 技能名称必须使用短横线命名，只能包含小写字母、数字和连字符

成功响应：

```json
{
  "success": true,
  "skill_name": "my-skill",
  "message": "技能 'my-skill' 安装成功"
}
```

失败场景：

- `400`：路径不合法、扩展名不是 `.skill`、不是合法 ZIP、缺少 `SKILL.md` 或元数据非法
- `403`：路径穿越等访问拒绝
- `404`：技能文件不存在
- `409`：同名技能已存在
- `500`：安装过程异常

## 9. Artifacts 接口

Artifacts 接口用于下载或在线查看线程产物文件，也支持读取 `.skill` 压缩包内部文件。

### 9.1 获取产物文件

- 方法：`GET`
- 路径：`/api/threads/{thread_id}/artifacts/{path:path}`
- 说明：返回线程产物内容，自动根据文件类型选择文本、HTML 或二进制响应

路径参数：

- `thread_id`：线程 ID
- `path`：带虚拟前缀的路径，例如 `mnt/user-data/outputs/report.md`

常见示例：

- `/api/threads/thread-1/artifacts/mnt/user-data/outputs/result.md`
- `/api/threads/thread-1/artifacts/mnt/user-data/uploads/demo.pdf`

查询参数：

- `download=true`：强制按附件下载

返回行为：

- HTML 文件：返回 `HTMLResponse`
- 文本文件：返回 `PlainTextResponse`
- 二进制文件：返回原始字节流
- 若是 `.skill/SKILL.md` 这种路径，会先从压缩包中提取内部文件

`.skill` 内部文件读取示例：

- `/api/threads/thread-1/artifacts/mnt/user-data/outputs/demo.skill/SKILL.md`

失败场景：

- `400`：路径不是文件
- `404`：产物不存在，或 `.skill` 压缩包内目标文件不存在

## 10. Uploads 接口

Uploads 接口用于将文件上传到线程私有目录，并在需要时自动转换为 Markdown。

上传目录逻辑：

- 宿主机物理目录：线程专属 uploads 目录
- Agent 运行时虚拟路径：`/mnt/user-data/uploads/...`

### 10.1 上传文件

- 方法：`POST`
- 路径：`/api/threads/{thread_id}/uploads`
- `Content-Type`：`multipart/form-data`
- 表单字段：`files`
- 说明：支持一次上传多个文件

请求示例：

```bash
curl -X POST \
  -F "files=@/path/to/a.pdf" \
  -F "files=@/path/to/b.xlsx" \
  http://localhost:8001/api/threads/thread-1/uploads
```

成功响应示例：

```json
{
  "success": true,
  "files": [
    {
      "filename": "a.pdf",
      "size": 102400,
      "path": ".lumen/threads/thread-1/uploads/a.pdf",
      "virtual_path": "/mnt/user-data/uploads/a.pdf",
      "artifact_url": "/api/threads/thread-1/artifacts/mnt/user-data/uploads/a.pdf",
      "markdown_file": "a.md",
      "markdown_path": ".lumen/threads/thread-1/uploads/a.md",
      "markdown_virtual_path": "/mnt/user-data/uploads/a.md",
      "markdown_artifact_url": "/api/threads/thread-1/artifacts/mnt/user-data/uploads/a.md"
    }
  ],
  "message": "Successfully uploaded 1 file(s)"
}
```

说明：

- 对于 `.pdf`、`.ppt`、`.pptx`、`.xls`、`.xlsx`、`.doc`、`.docx` 等扩展名，系统会尝试额外生成同名 `.md`
- 如果当前线程使用的是非本地沙箱，上传文件会同步写入沙箱虚拟路径

失败场景：

- `400`：未提供文件
- `500`：上传过程异常、沙箱获取失败、文件同步失败

### 10.2 获取已上传文件列表

- 方法：`GET`
- 路径：`/api/threads/{thread_id}/uploads/list`
- 说明：返回线程 uploads 目录中的全部文件

响应示例：

```json
{
  "files": [
    {
      "filename": "a.pdf",
      "size": 102400,
      "path": ".lumen/threads/thread-1/uploads/a.pdf",
      "virtual_path": "/mnt/user-data/uploads/a.pdf",
      "artifact_url": "/api/threads/thread-1/artifacts/mnt/user-data/uploads/a.pdf",
      "extension": ".pdf",
      "modified": 1711267200.0
    }
  ],
  "count": 1
}
```

### 10.3 删除上传文件

- 方法：`DELETE`
- 路径：`/api/threads/{thread_id}/uploads/{filename}`
- 说明：删除线程 uploads 目录中的指定文件

成功响应：

```json
{
  "success": true,
  "message": "Deleted a.pdf"
}
```

失败场景：

- `400`：文件名非法
- `404`：文件不存在
- `403`：路径校验失败
- `500`：删除失败或沙箱同步失败

## 11. Agents 接口

Agents 接口用于管理 Runtime 的全局自定义 Agent 定义，以及 legacy/operator `USER.md`。这些是内部控制面状态，不是按 Backend 用户隔离的公网资源。

### 11.1 命名规则

自定义 Agent 名称必须满足：

- 正则：`^[A-Za-z0-9-]+$`
- 存储时统一转为小写

这意味着：

- 可以传 `Sales-Agent`
- 最终会存为 `sales-agent`

### 11.2 获取全部 Agent

- 方法：`GET`
- 路径：`/api/agents`
- 说明：列出所有自定义 Agent，不包含 `SOUL.md` 正文

响应示例：

```json
{
  "agents": [
    {
      "name": "sales-agent",
      "description": "销售助手",
      "model": "gpt-5",
      "tool_groups": ["default", "browser"],
      "soul": null
    }
  ]
}
```

### 11.3 检查 Agent 名称是否可用

- 方法：`GET`
- 路径：`/api/agents/check`
- 查询参数：`name`
- 说明：校验名称是否合法，并检查是否已存在

请求示例：

- `/api/agents/check?name=sales-agent`

响应示例：

```json
{
  "available": true,
  "name": "sales-agent"
}
```

失败场景：

- `422`：名称不符合命名规则

### 11.4 获取单个 Agent 详情

- 方法：`GET`
- 路径：`/api/agents/{name}`
- 说明：返回 Agent 配置和 `SOUL.md` 内容

响应示例：

```json
{
  "name": "sales-agent",
  "description": "销售助手",
  "model": "gpt-5",
  "tool_groups": ["default"],
  "soul": "# Role\n你是一名销售助手"
}
```

失败场景：

- `404`：Agent 不存在
- `422`：名称非法

### 11.5 创建 Agent

- 方法：`POST`
- 路径：`/api/agents`
- 说明：创建新的自定义 Agent 目录，写入 `config.yaml` 与 `SOUL.md`

请求体示例：

```json
{
  "name": "sales-agent",
  "description": "销售助手",
  "model": "gpt-5",
  "tool_groups": ["default"],
  "soul": "# Role\n你是一名销售助手"
}
```

成功状态码：

- `201 Created`

返回结构与单个 Agent 详情一致。

失败场景：

- `409`：同名 Agent 已存在
- `422`：名称非法
- `500`：写文件失败

### 11.6 更新 Agent

- 方法：`PUT`
- 路径：`/api/agents/{name}`
- 说明：更新 Agent 的配置和/或 `SOUL.md`

请求体字段全部可选：

```json
{
  "description": "新的描述",
  "model": "gpt-5",
  "tool_groups": ["default", "browser"],
  "soul": "# Updated Soul"
}
```

返回结构与单个 Agent 详情一致。

失败场景：

- `404`：Agent 不存在
- `422`：名称非法

### 11.7 删除 Agent

- 方法：`DELETE`
- 路径：`/api/agents/{name}`
- 说明：删除整个 Agent 定义目录，包括 `config.yaml`、`SOUL.md` 与目录内相关文件；不会遍历或删除各用户 scope 下的同名 Agent 记忆

成功状态码：

- `204 No Content`

失败场景：

- `404`：Agent 不存在
- `422`：名称非法

### 11.8 获取 legacy/operator USER.md

- 方法：`GET`
- 路径：`/api/user-profile`
- 说明：读取全局 `USER.md` 文件。当前提示词运行路径不会注入它，它也不是 Backend 认证用户的长期画像

响应示例：

```json
{
  "content": "用户偏好使用中文进行技术讨论"
}
```

若文件不存在，则返回：

```json
{
  "content": null
}
```

### 11.9 更新 legacy/operator USER.md

- 方法：`PUT`
- 路径：`/api/user-profile`
- 说明：写入全局 `USER.md`。该接口仅用于兼容/运维状态，不会影响当前 scoped memory 注入

请求体：

```json
{
  "content": "用户偏好使用中文进行技术讨论"
}
```

响应结构与获取接口相同。

## 12. Suggestions 接口

Suggestions 接口用于根据最近对话生成用户可能继续追问的问题。

### 12.1 生成后续建议问题

- 方法：`POST`
- 路径：`/api/threads/{thread_id}/suggestions`
- 说明：根据最近对话内容生成最多 1 到 5 条建议问题

请求体示例：

```json
{
  "messages": [
    {
      "role": "user",
      "content": "帮我解释一下这个项目的 Gateway 和 LangGraph 区别"
    },
    {
      "role": "assistant",
      "content": "业务后端负责授权与代理，Gateway 负责 Runtime 资源，LangGraph 负责运行时执行。"
    }
  ],
  "n": 3,
  "model_name": "gpt-5"
}
```

字段说明：

- `messages`：最近对话消息数组
- `n`：建议数量，范围 `1` 到 `5`
- `model_name`：可选的模型覆盖

成功响应：

```json
{
  "suggestions": [
    "业务后端如何代理 Runtime 请求？",
    "为什么原始 thread 路由只能内部访问？",
    "文件上传如何绑定业务 session？"
  ]
}
```

说明：

- 若输入消息为空或模型生成失败，接口会返回空数组，而不是抛出 500

## 13. Channels 接口

Channels 接口用于管理外部 IM 渠道集成，目前代码中支持：

- `feishu`
- `slack`
- `telegram`

### 13.1 获取渠道状态

- 方法：`GET`
- 路径：`/api/channels/`
- 说明：返回渠道服务是否运行，以及各通道是否启用、是否运行

响应示例：

```json
{
  "service_running": true,
  "channels": {
    "feishu": {
      "enabled": false,
      "running": false
    },
    "slack": {
      "enabled": true,
      "running": true
    },
    "telegram": {
      "enabled": false,
      "running": false
    }
  }
}
```

### 13.2 重启单个渠道

- 方法：`POST`
- 路径：`/api/channels/{name}/restart`
- 说明：重启指定 IM 通道

路径参数：

- `name`：通常为 `feishu`、`slack` 或 `telegram`

成功响应：

```json
{
  "success": true,
  "message": "Channel slack restarted successfully"
}
```

失败场景：

- `503`：渠道服务未运行
- `200` + `success=false`：通道重启失败，但接口本身可达

## 14. LangGraph 运行时 API

这一部分不是 Gateway 自定义接口，而是由 `langgraph dev` 启动的 LangGraph Server 提供。它们是 Runtime 内部接口，浏览器不能经 Nginx 访问。

业务后端验证当前身份拥有 `session_id` 后，从 session 推导 `thread_id`，再调用其中的 thread/run 子集。其他 Runtime 内部客户端也会使用这些协议。

需要注意：

- 这里记录的是“当前项目内部使用的接口子集”
- 不是 LangGraph 官方全部通用接口的完整手册
- 不应指导前端绕过 `/api/chat-runtime/sessions/...` 代理

## 15. 线程管理接口

### 15.1 创建线程

- 方法：`POST`
- 路径：`/threads`
- 说明：创建一个新线程

业务后端准备 Runtime thread 时的内部请求体示例：

```json
{
  "thread_id": "thread-123",
  "if_exists": "do_nothing"
}
```

响应示例：

```json
{
  "thread_id": "thread-123",
  "created_at": "2026-03-24T10:00:00Z"
}
```

### 15.2 搜索线程列表

- 方法：`POST`
- 路径：`/threads/search`
- 说明：按更新时间等条件搜索线程列表

内部请求体示例：

```json
{
  "limit": 200,
  "offset": 0,
  "sort_by": "updated_at",
  "sort_order": "desc"
}
```

响应通常是数组，元素至少包含：

- `thread_id`
- `created_at`
- `updated_at`
- `metadata`

响应示例：

```json
[
  {
    "thread_id": "thread-123",
    "created_at": "2026-03-24T10:00:00Z",
    "updated_at": "2026-03-24T10:30:00Z",
    "metadata": {
      "title": "解释一下 Gateway 和 LangGraph 的区别"
    }
  }
]
```

### 15.3 获取线程状态

- 方法：`GET`
- 路径：`/threads/{thread_id}/state`
- 说明：获取线程当前状态快照

当前项目主要读取这些字段：

- `values.messages`
- `values.title`
- `values.artifacts`

响应示例：

```json
{
  "values": {
    "title": "New conversation",
    "messages": [],
    "artifacts": []
  }
}
```

### 15.4 删除线程

- 方法：`DELETE`
- 路径：`/threads/{thread_id}`
- 说明：删除指定线程

成功时通常返回空响应或标准删除结果。公网客户端不直接使用该路由。

## 16. 运行与流式接口

### 16.1 启动流式运行

- 方法：`POST`
- 路径：`/threads/{thread_id}/runs/stream`
- 请求头：`Accept: text/event-stream`
- 说明：以 SSE 形式启动一次 Agent 运行并持续返回事件流

业务后端完成 session 所有权、模型选择与配额保留后，发给 LangGraph 的内部请求体示例：

```json
{
  "assistant_id": "lead_agent",
  "on_disconnect": "continue",
  "multitask_strategy": "reject",
  "stream_mode": ["messages-tuple", "values", "custom"],
  "context": {
    "thread_id": "thread-123",
    "model_name": "gpt-5",
    "thinking_enabled": false,
    "is_plan_mode": false
  },
  "config": {
    "recursion_limit": 100
  },
  "input": {
    "messages": [
      {
        "role": "user",
        "content": "解释一下这个项目中的 Gateway 和 LangGraph"
      }
    ]
  }
}
```

字段说明：

- `assistant_id`：当前项目默认使用 `lead_agent`
- `on_disconnect`：业务后端与 Runtime 的连接断开策略，默认为 `continue`，便于客户端断线后 join
- `multitask_strategy`：同一 thread 已有活动 run 时使用 `reject`，避免无意排队
- `context`：运行时上下文，例如线程 ID、模型名、thinking 开关、plan 模式开关
- `config`：图运行配置，当前常用 `recursion_limit`
- `input.messages`：本次输入消息

响应类型：

- SSE 事件流

业务后端不缓冲地代理 SSE，前端持续解析事件名和数据块，并据此更新：

- 流式文本内容
- 工具调用状态
- 运行状态
- 最终消息列表
- 错误信息

### 16.2 取消运行

- 方法：`POST`
- 路径：`/threads/{thread_id}/runs/{run_id}/cancel`
- 当前查询参数：`action=interrupt&wait=0`
- 说明：中断一次正在执行的运行

请求示例：

- `POST /threads/thread-123/runs/run-456/cancel?action=interrupt&wait=0`

公网前端使用 session-scoped cancel 代理，由业务后端验证 run 所属 thread 后调用此内部路由。

## 17. 前端可访问的 Runtime 业务代理

前端不直接调用 Gateway 或 LangGraph，而是调用以下业务后端路由：

### 17.1 身份

- 已登录用户使用 `Authorization: Bearer <access-token>`
- 游客通过 `POST /api/auth/guest-session` 获得令牌，后续使用 `X-Guest-Token`
- 客户端自行生成的 `X-Guest-Id` 不被接受

### 17.2 线程准备与上传

- `POST /api/chat-runtime/sessions/{session_id}/thread/prepare`
- `POST /api/chat-runtime/sessions/{session_id}/thread/uploads`
- `DELETE /api/chat-runtime/sessions/{session_id}/thread/uploads/{filename}`

知识文档由 `thread/prepare` 物化到 Runtime 的保留命名空间，普通上传和删除接口都拒绝 `kb__` 文件名。prepare 只有在当前 KB 权限、稳定 Markdown revision、对象读取和完整性复核全部成功后才提交带 SHA-256 与大小的内部 manifest；这条文件链路不要求分块、embedding 或 Elasticsearch 索引完成，部分失败也不会静默缩小知识范围。

### 17.3 Run 生命周期

- `POST /api/chat-runtime/sessions/{session_id}/runs/stream`
- `GET /api/chat-runtime/sessions/{session_id}/runs?status=running|pending`
- `GET /api/chat-runtime/sessions/{session_id}/runs/{run_id}/stream`
- `POST /api/chat-runtime/sessions/{session_id}/runs/{run_id}/cancel`

启动、查询、断线 join 和 cancel 每次都根据当前身份重新验证 session 所有权。

启动 Run 还会在额度预留之前重新校验当前知识 scope、文档 revision、manifest 与 Runtime 实际文件哈希。客户端提交的 `kb_id/doc_ids` 会被服务端验证结果覆盖；缺少或过期的 prepare 返回冲突，权限撤销返回禁止访问，内部存储或校验服务不可用时返回服务不可用。

### 17.4 产物下载

- `GET /api/chat/sessions/{session_id}/artifacts/download?object_path=...`

业务后端同时校验 session 所有权和产物路径是否属于该会话，然后才向内部 Gateway 获取文件流。

## 18. 接口分层建议

如果后续继续扩展接口，应维持当前分层原则：

- LangGraph 拥有 Agent 执行、thread/run 状态与原始 SSE 协议
- Gateway 拥有 Runtime 配置、线程文件和资源读写
- 业务后端拥有身份、租户、session 所有权与公网代理合同
- 浏览器不能越过业务后端直接使用 Runtime `thread_id`

这样做的好处是：

- 职责清晰
- 前后端排障路径明确
- 运行时问题和业务接口问题不容易混在一起

## 19. 与源码对应关系

本文主要对应以下源码位置：

- Gateway 应用入口：`runtimes/backend/src/gateway/app.py`
- Gateway 启动入口：`runtimes/backend/src/gateway/run.py`
- Models 路由：`runtimes/backend/src/gateway/routers/models.py`
- MCP 路由：`runtimes/backend/src/gateway/routers/mcp.py`
- Memory 路由：`runtimes/backend/src/gateway/routers/memory.py`
- Skills 路由：`runtimes/backend/src/gateway/routers/skills.py`
- Artifacts 路由：`runtimes/backend/src/gateway/routers/artifacts.py`
- Uploads 路由：`runtimes/backend/src/gateway/routers/uploads.py`
- Agents 路由：`runtimes/backend/src/gateway/routers/agents.py`
- Suggestions 路由：`runtimes/backend/src/gateway/routers/suggestions.py`
- Channels 路由：`runtimes/backend/src/gateway/routers/channels.py`
- LangGraph 配置入口：`runtimes/backend/langgraph.json`
- 业务后端 Runtime thread 代理：`backend/modules/chat/runtime_controller.py`
- 业务后端 Runtime run 代理：`backend/modules/chat/runtime_run_controller.py`
- 业务后端游客令牌签发：`backend/modules/auth/controller.py`
- Nginx 公网路由边界：`docker/nginx/lumen.conf.template`
- 前端 API 封装：`frontend/src/shared/api/client.ts`

## 20. 一句话总结

lumen 的公网 HTTP 合同由业务后端提供；Gateway 和 LangGraph 提供的原始路由是 Runtime 内部协议。业务后端在代理任何 thread、run、上传或产物操作前，都必须以当前身份验证 session 所有权。
