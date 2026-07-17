# Docker 启动说明

本仓库只保留一个 Docker 启动入口：`docker/docker-compose.yml`。

## 快速启动

后端默认使用已构建好的 `lumen-backend:paper-translation` 镜像。首次使用或后端 Python 依赖变更后，先构建一次镜像：

```bash
docker build -f docker/backend-paper-translation.Dockerfile -t lumen-backend:paper-translation .
```

日常修改 `backend/` 代码不需要重新构建镜像，Compose 会把代码目录挂载到容器内。
镜像会从 `backend/requirements.txt` 构建 `/opt/lumen-backend-venv`；API 与迁移
命令即使通过 `sh -lc` 启动也会使用该隔离环境，不继承基础镜像的全局 Python 包。
Runtime 镜像在 `/opt/insight-flow-venv` 提供预构建依赖。Gateway、LangGraph 和
Sandbox Provisioner 首次启动时会把它复制到各自的持久卷，再用当前 `uv.lock` 做
frozen 增量同步；同步过程不会继承业务数据库、provider 或内部 token。脚本以锁文件、
Python ABI 和自身内容指纹判断失效，并在复用前校验环境；下载失败会在同一持久 cache
上有限重试，成功后后续启动直接复用。

使用默认值直接启动：

```bash
cp backend/.env.template backend/.env
./docker/init-env.sh
cp runtimes/config/.env.example runtimes/config/.env
cp runtimes/config/config.example.yaml runtimes/config/config.yaml
# 补全 JWT、模型凭据、有效的 TLS 证书路径；仓库不在 /root/Lumen 时还要修改 LUMEN_ROOT_DIR。
docker compose --env-file docker/.env -f docker/docker-compose.yml up -d
```

如需正式部署参数，先准备一份 Compose 变量文件：

```bash
./docker/init-env.sh
# 按你的机器路径、域名、证书、密码进行修改

docker compose --env-file docker/.env -f docker/docker-compose.yml up -d
```

已有部署在拉取新增内部 token 的版本后也应执行一次 `./docker/init-env.sh`。脚本使用
Docker Compose 自己的 dotenv 解析规则，能正确识别带引号的空值、占位值和变量展开；
它会补齐或修复内部 token 与 Redis 凭据，并将 `docker/.env` 权限设为 `0600`。
PostgreSQL/MinIO 属于持久服务，已有 `.env` 中的对应凭据即使仍是模板值也只会告警、
不会自动轮换，以免环境文件与持久数据中的账户密码失配。直接在 `docker/` 目录操作时
使用 `./init-env.sh`。

Compose 变量文件 `docker/.env` 管理端口、镜像、基础设施密码以及跨容器共享的
`GATEWAY_INTERNAL_API_TOKEN`、`RAG_INTERNAL_API_TOKEN`、
`MODEL_RESOLVER_INTERNAL_TOKEN` 与
`SANDBOX_PROVISIONER_INTERNAL_TOKEN`。Gateway token 只在
Backend/Gateway/LangGraph 间共享，RAG token 只在 Backend/RAG 间共享，模型解析
token 只在 Backend/LangGraph 间共享，Provisioner token 只在
LangGraph/Provisioner 间共享；它们都应使用 `openssl rand -hex 32` 独立生成，不能
复用。Backend 业务配置来自 `backend/.env`；Gateway/LangGraph 只继承
`runtimes/config/.env`，静态模型、搜索工具、MCP 和渠道所需的 provider secret 应
放在该 Runtime 专用文件中，避免 Runtime 无意获得 JWT、SMTP、MinerU、模型配置
加密密钥等 Backend secret。RAG 也不整体继承 `backend/.env`：Compose 只从
`docker/.env` 向 RAG 注入独立的 `RAG_INTERNAL_API_TOKEN`、embedding/CV provider
配置和 ES 凭据，避免文档解析器接触数据库、JWT、MinIO root、SMTP、Gateway
等业务秘密。异步 RAG 任务本身不会携带 API key；Compose 部署时应在
`docker/.env` 配置 worker 所需的 `EMBEDDING_*` / `CV_*`，并按需与
`backend/.env` 的模型配置保持一致。

`REDIS_PASSWORD` 与 `RAG_REDIS_PASSWORD` 必须彼此独立、至少 32 字符，且只能包含
ASCII 字母、数字、下划线和连字符。推荐分别使用 `openssl rand -hex 32` 生成；
Redis 启动时会在不回显秘密的情况下校验该规则并 fail closed。密码通过容器环境传入，
渲染命令和 healthcheck 都只引用运行时变量，不把 Compose 中的原值嵌入 shell 程序。

Nginx 的 `LUMEN_HTTP_MODE` 默认是 `redirect`：80 端口仅直接提供 ACME HTTP-01
challenge，其余请求返回 `308` 到相同 host/URI 的 HTTPS 地址。仅在明确需要保留
HTTP 内容服务的本地开发环境中将其设置为 `serve`，才会通过 HTTP 提供 Web/API；除
`redirect|serve` 外的值会使 Nginx 启动失败。所有 `/api/` 请求都关闭请求体缓冲，
因此文档上传与普通 API 使用同一条可达的代理规则。`serve` 不会禁用 443 server，
Nginx 仍会校验 `LUMEN_SSL_CERT_PATH` 与 `LUMEN_SSL_CERT_KEY_PATH`；完全无证书的
前端本地开发应使用 `npm run web:dev`，而不是 Compose Nginx。

停止服务：

```bash
docker compose --env-file docker/.env -f docker/docker-compose.yml down
```

## 当前编排特性

- 只对外公开 Nginx `80/443`；80 默认仅保留 ACME 并将其他请求重定向到 HTTPS
- PostgreSQL、Redis、Elasticsearch、MinIO、API、Gateway、LangGraph、RAG 默认只绑定 `127.0.0.1`
- Gateway 仅允许 `/health` 匿名访问；所有 `/api` 路由还要求
  `X-Gateway-Internal-Token`，网络隔离不再是唯一防线
- 各服务数据目录、镜像、端口、域名、证书路径都支持通过 Compose 变量覆盖
- Nginx 配置通过模板渲染，避免把域名和证书路径硬编码进仓库
- Runtime 主配置和内置 Skills 以只读方式挂载。Gateway 仅可写
  `runtimes/config/extensions/` 与 `runtimes/skills/custom/`；
  LangGraph 以只读方式消费这两处共享状态，因此 MCP/Skill 管理接口的变更可立即生效。
  真实 `extensions_config.json` 与已安装 custom skills 是被 Git 忽略的部署态；仓库只提供
  `extensions_config.example.json` 和目录说明，避免把 MCP secret 或用户技能误提交
- Backend 与 RAG 都从只读挂载的 `shared/python/recall_lib` 加载检索与
  Elasticsearch 适配逻辑，RAG 服务内不再维护第二份算法源码
- RAG 源码只读挂载，task payload、tokenizer/tiktoken cache 与临时文件写入
  `RAG_TASK_STATE_DIR` 独立持久目录。Redis processing lease 使用唯一 token、
  heartbeat 和 stale recovery；旧 worker 不能确认新 worker 的 claim
- RAG 使用独立 Redis ACL 用户，只能访问 `document_parse_queue:*` 并执行队列
  状态机所需命令；它不能读取 Backend quota、usage stream 或其他持久队列
- LangGraph 不挂载 `/var/run/docker.sock`。只有不发布宿主端口、不加载业务密钥的
  `lumen_sandbox_provisioner` 持有 socket；其 API 仅接受严格校验且相互绑定的
  `thread_id` / `sandbox_id`，镜像、命令、环境、端口、权限和挂载不能由调用方提交
- Provisioner 将 `workspace`、`uploads`、`outputs` 分别可写挂载，将 Backend 管理的
  `knowledge` 与 skills 分别只读挂载；不会把整个 `/mnt/user-data` 作为可写卷暴露
- 沙箱控制端口只绑定宿主机网关私有地址。容器固定使用默认 seccomp、
  `no-new-privileges`、非 privileged、`cap-drop ALL`、进程数限制和受限 tmpfs；
  当前上游 AIO 镜像启动时必须创建用户并写 `/etc`，所以只回加
  `CHOWN/DAC_OVERRIDE/FOWNER/SETGID/SETUID` 五项兼容 capability，且不能启用
  read-only rootfs。Chromium 通过固定 `--no-sandbox` 运行，以避免加入高风险
  `SYS_ADMIN`；更换为预初始化、无启动期系统写入的镜像后才能进一步移除这些例外
- Provisioner 默认镜像固定到已验证的 sha256 digest；`SANDBOX_IMAGE` 覆盖值也必须
  使用 `name@sha256:<64 hex>`，mutable tag 会在启动阶段被拒绝

`GET /api/mcp/config` 不会返回 MCP 环境变量、请求头、OAuth secret 或
refresh token 的真实值，而是统一返回 `********`。使用该响应执行 PUT 时，
掩码表示保留磁盘中的原值；建议实际秘密始终通过 `$ENV_VAR` 引用提供。

## 附属文件

- `docker-compose.yml`：唯一启动入口
- `init-env.sh`：幂等创建或升级 Compose 部署秘密
- `.env.example`：Compose 部署变量示例
- `nginx/`：Nginx 模板配置
- `redis.conf.template`：Redis 配置模板
- `init-db/`：PostgreSQL 初始化脚本
