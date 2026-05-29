# Docker 启动说明

本仓库只保留一个 Docker 启动入口：`docker/docker-compose.yml`。

## 快速启动

后端默认使用已构建好的 `lumen-backend:paper-translation` 镜像。首次使用或后端 Python 依赖变更后，先构建一次镜像：

```bash
docker build -f docker/backend-paper-translation.Dockerfile -t lumen-backend:paper-translation .
```

日常修改 `backend/` 代码不需要重新构建镜像，Compose 会把代码目录挂载到容器内。

使用默认值直接启动：

```bash
docker compose -f docker/docker-compose.yml up -d
```

如需正式部署参数，先准备一份 Compose 变量文件：

```bash
cp docker/.env.example docker/.env
# 按你的机器路径、域名、证书、密码进行修改

docker compose --env-file docker/.env -f docker/docker-compose.yml up -d
```

停止服务：

```bash
docker compose -f docker/docker-compose.yml down
```

## 当前编排特性

- 只对外公开 Nginx `80/443`
- PostgreSQL、Redis、Elasticsearch、MinIO、API、Gateway、LangGraph、RAG 默认只绑定 `127.0.0.1`
- 各服务数据目录、镜像、端口、域名、证书路径都支持通过 Compose 变量覆盖
- Nginx 配置通过模板渲染，避免把域名和证书路径硬编码进仓库

## 附属文件

- `docker-compose.yml`：唯一启动入口
- `.env.example`：Compose 部署变量示例
- `nginx/`：Nginx 模板配置
- `redis.conf.template`：Redis 配置模板
- `init-db/`：PostgreSQL 初始化脚本
