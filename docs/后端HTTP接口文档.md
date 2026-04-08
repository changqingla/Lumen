# lumen 后端 HTTP API 接口文档

## 1. 文档目标

本文用于整理 lumen 项目当前实际对外提供的后端 HTTP API 接口，帮助前端开发、后端联调、第三方系统接入和日常排障。

需要先说明一个关键前提：

- 这个项目的后端不是单一服务，而是由两个 HTTP 服务共同组成：
- `Gateway`：项目自定义业务接口层，默认端口 `8001`
- `LangGraph Server`：Agent 运行时接口层，默认端口 `2024`

因此，本文也分为两部分：

- `Gateway` 自定义 REST API
- 项目当前实际依赖的 `LangGraph` 运行时 API

本文依据的主要实现来源包括：

- `backend/src/gateway/app.py`
- `backend/src/gateway/routers/*.py`
- `backend/langgraph.json`
- `frontend/src/shared/api/client.ts`

## 2. 服务与基础地址

在默认 Docker 开发环境中，两个服务的地址为：

- `Gateway`: `http://localhost:8001`
- `LangGraph`: `http://localhost:2024`

前端通常通过反向代理或同域转发来访问它们，所以在浏览器代码里你会看到这样的路径形式：

- `Gateway` 路径通常以 `/api/...` 开头
- `LangGraph` 路径通常以 `/threads/...` 开头

### 2.1 Gateway 文档入口

Gateway 已启用 FastAPI 自带文档：

- Swagger UI: `GET /docs`
- ReDoc: `GET /redoc`
- OpenAPI JSON: `GET /openapi.json`

### 2.2 认证状态

基于当前代码，Gateway 层没有额外实现统一的鉴权中间件或 Token 校验逻辑。也就是说：

- 当前接口默认处于“未加鉴权”的开发态
- 生产环境若需鉴权，应在网关层、反向代理层或 API Gateway 层补充

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

## 3. Gateway API 总览

Gateway 目前注册的接口模块包括：

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

MCP 接口用于管理 `config/extensions_config.json` 中的 Model Context Protocol 服务配置。

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

### 6.2 更新 MCP 配置

- 方法：`PUT`
- 路径：`/api/mcp/config`
- 说明：将新的 MCP 配置写回 `config/extensions_config.json`，然后重载配置缓存

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
- Gateway 不会直接初始化 MCP 工具，LangGraph 进程会在后续按需重载

失败场景：

- `500`：配置写入失败或配置重载失败

## 7. Memory 接口

Memory 接口用于读取和刷新全局记忆文件。

### 7.1 获取当前记忆数据

- 方法：`GET`
- 路径：`/api/memory`
- 说明：返回当前全局记忆数据

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
- 说明：从记忆存储文件重新读取并刷新进程内缓存

适用场景：

- 手工修改了记忆文件
- 需要强制让 Gateway 读取最新内容

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
- 说明：一次返回“记忆配置 + 当前记忆数据”

响应结构：

```json
{
  "config": {
    "enabled": true,
    "storage_path": "state/memory.json",
    "debounce_seconds": 5,
    "max_facts": 200,
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
- 说明：通过修改 `config/extensions_config.json` 更新技能启用状态

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

Agents 接口用于管理自定义 Agent 及全局 `USER.md`。

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
- 说明：删除整个 Agent 目录，包括配置与相关文件

成功状态码：

- `204 No Content`

失败场景：

- `404`：Agent 不存在
- `422`：名称非法

### 11.8 获取全局用户画像

- 方法：`GET`
- 路径：`/api/user-profile`
- 说明：读取全局 `USER.md` 文件

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

### 11.9 更新全局用户画像

- 方法：`PUT`
- 路径：`/api/user-profile`
- 说明：写入全局 `USER.md`

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
      "content": "Gateway 负责业务接口层，LangGraph 负责运行时执行层。"
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
    "那前端请求分别走哪一边？",
    "它们之间是怎么通信的？",
    "为什么文件上传放在 Gateway？"
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

这一部分不是 Gateway 自定义接口，而是由 `langgraph dev` 启动的 LangGraph Server 提供。

基于当前项目代码与前端调用，项目实际依赖的 LangGraph HTTP API 主要包括以下几组。

需要注意：

- 这里记录的是“当前项目实际使用的接口子集”
- 不是 LangGraph 官方全部通用接口的完整手册

## 15. 线程管理接口

### 15.1 创建线程

- 方法：`POST`
- 路径：`/threads`
- 说明：创建一个新线程

前端请求体示例：

```json
{
  "metadata": {}
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

前端请求体示例：

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

成功时通常返回空响应或标准删除结果，前端当前只检查 HTTP 状态码。

## 16. 运行与流式接口

### 16.1 启动流式运行

- 方法：`POST`
- 路径：`/threads/{thread_id}/runs/stream`
- 请求头：`Accept: text/event-stream`
- 说明：以 SSE 形式启动一次 Agent 运行并持续返回事件流

当前前端发起运行时的请求体示例：

```json
{
  "assistant_id": "lead_agent",
  "on_disconnect": "cancel",
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
- `on_disconnect`：前端断开连接时的处理策略，当前使用 `cancel`
- `context`：运行时上下文，例如线程 ID、模型名、thinking 开关、plan 模式开关
- `config`：图运行配置，当前常用 `recursion_limit`
- `input.messages`：本次输入消息

响应类型：

- SSE 事件流

前端会持续解析事件名和数据块，并据此更新：

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

前端当前只检查状态码，不依赖特定响应体字段。

## 17. 前端当前实际使用的接口集合

如果只从“当前前端会调用哪些接口”这个角度来看，可以简化为以下列表：

### 17.1 Gateway

- `GET /api/models`
- `GET /api/threads/{thread_id}/uploads/list`
- `POST /api/threads/{thread_id}/uploads`
- `DELETE /api/threads/{thread_id}/uploads/{filename}`
- `POST /api/threads/{thread_id}/suggestions`
- `GET /api/threads/{thread_id}/artifacts/{path:path}`

### 17.2 LangGraph

- `POST /threads`
- `POST /threads/search`
- `GET /threads/{thread_id}/state`
- `DELETE /threads/{thread_id}`
- `POST /threads/{thread_id}/runs/stream`
- `POST /threads/{thread_id}/runs/{run_id}/cancel?action=interrupt&wait=0`

## 18. 接口分层建议

如果后续你们还要继续扩展接口，建议维持当前分层原则：

- 与 Agent 执行过程、线程状态、runs、SSE 相关的接口，优先放在 LangGraph 一侧
- 与项目业务管理、配置管理、资源读写、文件访问相关的接口，优先放在 Gateway 一侧

这样做的好处是：

- 职责清晰
- 前后端排障路径明确
- 运行时问题和业务接口问题不容易混在一起

## 19. 与源码对应关系

本文主要对应以下源码位置：

- Gateway 应用入口：`backend/src/gateway/app.py`
- Gateway 启动入口：`backend/src/gateway/run.py`
- Models 路由：`backend/src/gateway/routers/models.py`
- MCP 路由：`backend/src/gateway/routers/mcp.py`
- Memory 路由：`backend/src/gateway/routers/memory.py`
- Skills 路由：`backend/src/gateway/routers/skills.py`
- Artifacts 路由：`backend/src/gateway/routers/artifacts.py`
- Uploads 路由：`backend/src/gateway/routers/uploads.py`
- Agents 路由：`backend/src/gateway/routers/agents.py`
- Suggestions 路由：`backend/src/gateway/routers/suggestions.py`
- Channels 路由：`backend/src/gateway/routers/channels.py`
- LangGraph 配置入口：`backend/langgraph.json`
- 前端 API 封装：`frontend/src/shared/api/client.ts`

## 20. 一句话总结

lumen 的后端 HTTP API 不是一套接口，而是两套接口协作：

- `Gateway` 负责项目自定义的业务与资源接口
- `LangGraph` 负责线程、运行和流式执行接口

理解这条边界，是读懂这个项目后端 API 设计的关键。
