# Repository Structure and Ownership

本文档描述当前代码所有权，不是按文件名生成的目录清单。跨服务修改应先确定数据与授权由哪一层负责。

| Directory | Owner / Responsibility | Must not own |
| --- | --- | --- |
| `frontend/` | React UI、交互状态、SSE 消费与展示 | 租户授权、知识库访问判定、模型密钥 |
| `backend/` | 身份、租户、业务数据、会话所有权、运行编排 | Agent 工具循环、向量索引实现 |
| `services/rag/` | 文档解析、分块、embedding、ES 写入和检索 | 用户登录、业务会话 |
| `runtimes/backend/` | LangGraph、Gateway、Tools、Skills、Memory、Sandbox、Subagents | 最终用户/租户授权 |
| `runtimes/skills/` | 运行时渐进加载的领域工作说明 | 业务 API 与持久化逻辑 |
| `shared/python/` | 多部署单元必须共享的 Python 协议代码 | 独立业务实现副本 |
| `docker/` | 单机生产拓扑、Nginx 与初始化资源 | 应用业务规则 |
| `docs/` | 当前架构、协议和运维决策 | 失效路径与历史实现承诺 |

## Request Boundaries

```text
Browser -> Nginx -> Business API -> PostgreSQL / Redis / MinIO
                              -> RAG -> Elasticsearch
                              -> Gateway / LangGraph -> Sandbox
```

业务后端是公网请求的授权点。Nginx 明确拒绝外部 `/threads/*` 与 `/api/threads/*`；后端按当前用户和 session 推导 Runtime thread，不能接受浏览器声明的任意 thread 所有权。

## Knowledge Document Jobs

知识库文档在文件落库后先持久化为内部 `queued` 状态，再进入 Redis 可恢复队列。Worker 使用带 token 的可见性 lease、heartbeat 和有界重试；定期 reconciler 会扫描数据库中仍为 `queued` 的记录，补偿数据库提交成功但 Redis 入队失败的窗口。为保持现有客户端合同，对外序列化时将内部 `queued` 映射为 `processing`；对外状态中没有新增 `queued` 枚举。

## Durable Work Protocols

项目当前有三套后台任务协议，不能退回请求进程内的 `BackgroundTasks`：

| Workflow | Durable authority | Delivery / recovery | Commit fence |
| --- | --- | --- | --- |
| Knowledge document processing | PostgreSQL `kb_documents` + MinIO | Backend Redis lease、heartbeat、retry、DB reconcile | document lease token；ready 前更新 revision/hash |
| RAG parse/embed/store | durable task payload dir + Redis metadata | idempotency key、visibility lease、startup/periodic stale recovery | 当前 RAG lease token 才能 complete/fail/requeue |
| Paper translation | source PDF + `task.json` manifest | Redis pending/processing/scheduled、attempt、reconcile cursor | manifest active/terminal token + generation，产物位于 `.leases/<token>/` |

Redis 配置必须保持 AOF、`appendfsync everysec` 与 `noeviction`。内存压力应显式拒绝写入，不能用 LRU 静默淘汰 quota reservation 或任务状态。

## Database Migrations

`backend/run_migrations.py` 在单条 PostgreSQL session 上持有固定域的 advisory lock；锁内完成迁移发现、ledger 前缀验证和应用。`schema_migrations` 必须是镜像中迁移序列的精确前缀，未知未来版本、缺洞、filename/kind/checksum 不一致和重复 ledger 行都会拒绝启动。

已发布迁移不可修改。SQL migration 与 ledger 写入处于同一事务；Python migration 的外部副作用不能和父进程 ledger 原子提交，因此必须可重入并有人工恢复说明。Compose 让业务 API 等待 migration job 成功完成。

## Internal Credentials

内部 token 不可复用；名称相似不表示同一信任域：

| Credential | Producers / consumers | Must not be inherited by |
| --- | --- | --- |
| `GATEWAY_INTERNAL_API_TOKEN` | Backend、Gateway、LangGraph | Browser、RAG、Provisioner、sandbox |
| `RAG_INTERNAL_API_TOKEN` | Backend、RAG | Gateway、LangGraph、Provisioner、browser |
| `MODEL_RESOLVER_INTERNAL_TOKEN` | Backend、LangGraph | Gateway、RAG、Provisioner、sandbox |
| `SANDBOX_PROVISIONER_INTERNAL_TOKEN` | LangGraph、Provisioner | Gateway、Backend、RAG、sandbox workload |
| `RAG_REDIS_PASSWORD` | Redis ACL、RAG | Backend/Runtime 环境与任务 payload |

Backend 业务秘密来自 `backend/.env`；Gateway 与 LangGraph 只继承 `runtimes/config/.env`，Compose 还会在 Gateway 环境中显式清空它不拥有的 resolver/provisioner token。RAG 不继承任一整份应用 env，只接收显式 allowlist；它使用独立 Redis ACL 用户，只能访问 `document_parse_queue:*`。

## Outbound Boundaries

用户或远端响应能影响的 URL 必须经过结构化 outbound policy。模型 provider 默认只允许公网地址；只有运维显式设置 `MODEL_PROVIDER_ALLOW_PRIVATE_ENDPOINTS=true` 时才允许自托管私网端点，Compose 会把同一策略同时注入 Backend 与 Runtime。无论是否放开私网，模型端点都会拒绝非法 scheme、userinfo、fragment 和 query，并在请求及 connect 前重新解析、校验和 pin 地址；客户端不读取环境代理、不跟随重定向。MinerU 始终只允许公网 HTTPS，并限制 API JSON、ZIP 下载、成员数及单项/总解压字节。

InfoQuest 与 Jina 等固定厂商 API 不接受可配置目标端点，但仍统一使用 Runtime 的有界 provider transport：每个 worker thread 使用独立连接池，忽略环境代理，禁止重定向，并限制连接/读取时间和解压后的响应体。新增固定端点工具不得直接调用全局 `requests` API。

创意工坊图片响应使用有界流式 JSON 读取。论文 PDF 只允许访问当前任务根内的受支持位图，不能从译文 Markdown 读取任意本地文件或远端资源。日志不能包含 provider body、预签名 query、完整 prompt、文档正文或 API key 片段。

## Runtime State And Memory

认证用户的长期记忆由 Backend 用域分隔 HMAC 从用户 UUID 派生 opaque `memory_scope`；客户端 scope 会被覆盖，guest 不读、不写、不注入。Runtime 在每次 model call 时按 `(memory_scope, agent_name)` 动态读取，而不是在建图时缓存某个用户画像。旧全局 `memory.json` 永不自动读取或迁移。

scoped JSON 使用进程内锁 + Linux `flock`、唯一临时文件、文件/目录 `fsync` 和原子替换。防抖更新队列仍是进程内 best-effort 状态，硬崩溃可能丢失尚未处理的派生更新；它不能承载必须一次不丢的业务事实。轮换 Backend `SECRET_KEY` 会改变 scope，需要显式迁移或双钥过渡。

## Sandbox Ownership

LangGraph 不持有 Docker socket。只有最小 Sandbox Provisioner 控制面拥有该能力，且它只接受固定 schema 的 `thread_id` / `sandbox_id`，镜像、命令、端口、环境和挂载不能由调用方提交。workspace/uploads/outputs 可写，Backend 管理的 knowledge 与 skills 只读；Provisioner API 使用独立 token。

Docker socket 仍等价于宿主高权限能力，隔离控制面只能缩小暴露面，不能消除该风险。生产部署还应把 Provisioner 放在独立主机或受约束的 Docker API 代理之后，并限制其出站网络。

## Dependency Direction

允许的主要依赖方向：

```text
frontend -> backend HTTP contracts
backend controllers -> services -> repositories/entities
backend services -> internal RAG/Runtime/MinIO clients
LangGraph graph -> middleware/tools -> sandbox and internal model resolver
RAG API + backend knowledge services -> shared/python/recall_lib -> RAG core / Elasticsearch
```

禁止从底层 repository 反向导入 controller，禁止在前端复制服务端权限规则，禁止用两个长期分叉的实现维护同一检索协议。

## Generated State

以下目录不是源码，已由 `.gitignore` 排除：`node_modules/`、`frontend/dist/`、`logs/`、`services/rag/tmp/`、`runtimes/state/`、`runtimes/config/extensions/extensions_config.json`、`runtimes/skills/custom/` 中的安装产物、各类 cache 与本地 `.env`。仓库只保留扩展配置示例和 custom skill 目录说明。清理这些路径前应确认其中没有仍需保留的运行产物或用户数据。
