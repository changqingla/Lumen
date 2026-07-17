# Lumen Business API

`backend/` 是 Lumen 的业务与租户边界。它负责身份、组织、知识库元数据、聊天会话、笔记、收藏、模型凭据和创意工坊；模型工具循环、文档分块检索和对象存储分别由 Runtime、RAG 与 MinIO 承担。

## 代码边界

```text
controller -> service -> repository -> SQLAlchemy entity
                       -> internal Runtime/RAG/MinIO clients
```

- `app/main.py`：应用生命周期、路由注册、liveness/readiness。
- `modules/`：按业务域组织 controller、service、repository 与 entity。
- `services/`：被多个业务域共同使用、但仍由业务 API 拥有的应用服务。
- `middlewares/`：登录用户与游客身份解析。
- `config/`：数据库、Redis 和经过校验的环境配置。
- `migrations/`：带版本连续性和 checksum 的自定义迁移。
- `tests/`：业务与安全回归测试。

### 数据库迁移安全边界

`run_migrations.py` 在同一条 PostgreSQL 连接上持有 session advisory lock，串行执行
迁移发现、历史验证和应用。默认等待 60 秒；可通过正数
`MIGRATION_LOCK_TIMEOUT_SECONDS` 调整，超时会直接失败。数据库中的
`schema_migrations` 必须是当前镜像迁移列表的精确前缀；未知版本、缺洞、文件名、
类型或 checksum 不一致都会拒绝启动。因此部署时必须先完成迁移，且不能用旧镜像
对已经前进的数据库执行 runner，也不能修改已经发布的迁移文件。正常结束会显式
解锁；runner 异常退出或连接断开时，PostgreSQL 会随 session 关闭自动释放锁。

SQL migration 的 SQL 与 ledger 写入位于同一数据库事务。Python migration 由独立
子进程执行，其数据库提交、对象存储操作或其他外部副作用无法与父进程随后写入
ledger 的事务形成原子操作；进程在两者之间中断时，该 migration 会在下次运行时
重试。Python migration 必须设计为可重入、可核验，并提供人工恢复步骤；advisory
lock 只提供并发串行化，不提供外部副作用回滚。

### MinerU 外部边界

MinerU 官方 API base URL、API 返回的预签名上传 URL 和结果 ZIP URL 都必须使用
HTTPS，并且只能解析到公网地址。每次 TCP connect 前会重新解析、校验并固定到已
批准的数值 IP；请求不读取环境代理且不跟随重定向。预签名 URL 可以携带 query，
但禁止 userinfo 和 fragment，日志不会记录签名 URL、API token 或远端响应正文。

结果 ZIP 以流式方式下载到 bounded spool。`MINERU_MAX_ZIP_DOWNLOAD_BYTES` 限制
压缩响应，`MINERU_MAX_ZIP_MEMBER_COUNT`、
`MINERU_MAX_ZIP_MEMBER_UNCOMPRESSED_BYTES` 和
`MINERU_MAX_ZIP_TOTAL_UNCOMPRESSED_BYTES` 分别限制成员数、单成员与声明总解压
大小；读取成员时还会再次执行实际字节上限。超限或 HTTP 内容编码响应会直接失败。

创意工坊图片 provider 同样不读取环境代理且不跟随重定向。响应 JSON（包括 base64
图片）按 `CREATIVE_WORKSHOP_IMAGE_MAX_RESPONSE_BYTES` 流式限长；远端错误正文、
URL query、用户 ID 和异常 traceback 不进入普通日志或公网错误响应。

论文译文导出 PDF 时，WeasyPrint 的 fetcher 绑定到当前任务目录。它只允许目录内
现有、大小受限的 PNG/JPEG/WebP/GIF/BMP 位图和有界 raster data URL；远程 URL、
非图片文件、目录穿越和解析到目录外的 symlink 都会被拒绝。不能把默认
`url_fetcher` 直接恢复到用户可影响的 Markdown 上。

### 论文翻译任务边界

论文翻译以磁盘上的 source PDF 与 `task.json` manifest 为 durable authority，Redis
只负责投递协调。v2 协议使用 `pending`、`processing`、`scheduled` ZSET 以及独立的
payload、attempt、lease token、generation hash；enqueue、claim、heartbeat、ACK、
retry、promotion、stale recovery、cancel 和 shutdown requeue 都由 Lua 原子转移。

每个 claim 使用随机 token 和 Redis `TIME` 派生的单调 generation。worker 必须先把
claim 激活到 manifest，所有文件/状态提交前先续租，再在跨进程 `flock` 内确认当前
active token。产物先写到 `.leases/<token>/`，过期 worker 即使在新 worker claim 后
恢复，也无法覆盖或发布新结果。只有当前 lease 写出的 terminal manifest 可以 ACK；
foreign terminal 会清除归属、更换 Runtime thread 并重新执行。

manifest、Markdown、图片、PDF 和上传源文件都使用唯一同目录临时文件、文件
`fsync`、`os.replace` 与目录 `fsync`。maintenance 定期提升到期 retry、回收过期
lease，并用持久游标扫描 `queued/converting/translating` manifest，补偿“文件已落盘
但 Redis 尚未入队”的崩溃窗口。heartbeat 必须短于 visibility timeout；配置位于
`.env.template` 的 `CREATIVE_WORKSHOP_PAPER_TRANSLATION_QUEUE_*`。

浏览器不得直接操作 LangGraph thread 或 Gateway 文件目录。`modules/chat/runtime_run_controller.py` 先验证业务 session 所有权，再从服务端推导 thread、assistant 和模型绑定，代理 run stream、恢复与取消操作。

### Runtime 知识物化边界

`thread/prepare` 根据 session 中的 `kbIds/docIds` 重新校验当前知识库权限，只读取已经提交稳定 Markdown revision 的文档；整库模式同样枚举全部已物化文档。Runtime 可用性由非空 `markdown_path`、`materialization_revision >= 1`、对象可下载、UTF-8、非空正文和可选持久 SHA-256 决定，不依赖文档的分块、embedding 或 Elasticsearch 状态。正文按最多 20 篇一批流式物化；任一文档缺失、Markdown 不可用、对象存储失败、解码失败或 revision 变化都会中止整次 prepare，不得把失败文档误判为退出 scope，也不得删除上一版文件。

每个受管文件使用 `kb__<kb-id>__<doc-id>__<content-hash-prefix>__...md` 保留命名，session manifest 保存文档 revision、完整 SHA-256、字节数和 Runtime 文件名。新文件上传并复核 revision 后先提交 manifest，再清理旧文件；提交或清理中断会留下可由下一次 prepare 收敛的孤儿文件，而不会静默授权缩减后的 scope。

Run 准入发生在模型解析和 token 预留之前。业务后端会重新校验当前 KB 权限、已物化文档精确集合、revision、manifest schema、Runtime 受管文件集合及每个文件的实际 `size + SHA-256`。任一项不一致都 fail closed；浏览器提交的 `kb_id/doc_ids` 会被丢弃，Runtime context 只从服务端验证后的 session scope 重建。

`thread/prepare` 与 Run 准入对同一个 Runtime thread 共用 PostgreSQL session-level
advisory lock。锁由独立 `NullPool` engine 的物理连接持有，不依赖会在 `commit`
后归还连接池的请求 `AsyncSession`；同 thread 跨 API worker/主机串行，不同 thread
仍可并行。获取超时或锁连接异常统一返回可重试的
`503 THREAD_GUARD_UNAVAILABLE`。仅 `DEBUG=true` 的单机开发允许显式使用
`process`/`flock` 回退，生产环境不会静默退化为进程内互斥。

### Runtime token 计费边界

业务后端是用户额度与账单记录的唯一权威。Run 启动前，`TokenQuotaService` 按 UTC 自然月把数据库已提交用量与 Redis 中未结算预留合并，并通过 Lua 原子预留本次 Run 的额度。只有预留成功后，后端才向 Runtime 注入短期签名 `usage_context`；浏览器提交的同名字段会被丢弃。

Runtime 为 lead、subagent、上下文摘要和标题模型调用读取 provider usage，按唯一 `event_id` 上报。后端先将事件通过带 consumer group 的 Redis Stream 持久接收，再按以下顺序处理：

```text
DB INSERT ... ON CONFLICT(event_id) DO NOTHING
  -> DB COMMIT
  -> Redis 原子校准 committed 并扣减 reservation pending
  -> XACK / XDEL
```

终止事件携带本 Run 已接受的全部 usage event ID，只有这些 ID 全部提交后才释放未消费预留。客户端断连不会释放仍在继续的 Run；取消、Runtime 硬崩溃或 provider 未返回终态时，由预留 TTL 回收。Redis 使用 AOF 与 `noeviction`，不能以 LRU 静默淘汰账本或队列。

### Python 依赖安全

JWT 统一使用 `PyJWT`，所有解码调用都必须显式传入允许的算法列表；当前 HS256
token 与旧版 python-jose 生成的标准 JWT 保持兼容。模型配置使用的 Fernet 来自
显式固定的 `cryptography` 依赖，不得依赖 JWT 库的可选 extra 间接安装。

FastAPI、Starlette、multipart 与 Pydantic 必须作为一组升级并执行 Backend 全量
回归。`docker/backend-paper-translation.Dockerfile` 从
`backend/requirements.txt` 安装独立 venv，不得绕过该清单复用基础镜像的全局
site-packages。论文 PDF 栈升级除 Python 测试外，还必须在部署基础镜像的
Pango/Cairo 环境完成真实 PDF 烟测。

## 本地开发

```bash
cp backend/.env.template backend/.env
python3.12 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt -r backend/requirements-dev.txt

cd backend
python run_migrations.py
uvicorn app.main:app --host 0.0.0.0 --port 13000 --reload
```

业务 API 需要 PostgreSQL、Redis 与 MinIO。文档处理还需要 RAG/Elasticsearch，聊天执行还需要 Gateway/LangGraph。

## 验证

从仓库根目录运行：

```bash
DEBUG=false PYTHONPATH="$PWD/backend" python3.12 -m pytest -q backend/tests
python3.12 -m compileall -q backend
docker compose --env-file docker/.env -f docker/docker-compose.yml config -q
```

提交新行为时，测试应覆盖 service 规则、repository 查询条件和 controller 契约。涉及权限的查询必须在数据库分页与计数之前应用访问条件，不能先取数据再在 Python 中过滤。

## 工程约定

- Controller 只处理 HTTP 契约、身份依赖和响应映射。
- Service 拥有业务规则与跨资源编排。
- Repository 只封装持久化查询；新代码应尽量由 service 控制事务边界。
- 外部 URL 必须经过统一 outbound policy，Runtime/RAG 使用内网地址和服务间 token。
- 长任务必须进入可恢复队列，不能依赖请求进程内的 `BackgroundTasks`。
- API Key、token、完整 prompt 和内部异常不得写入普通日志、响应或 tracing metadata。
- 计费身份只能来自服务端签名 context；provider 不返回 usage 时必须明确标记估算值，不能信任客户端 token 数。
