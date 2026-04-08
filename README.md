# Lumen

> English version: [README.en.md](./README.en.md)

面向文档理解、知识检索与 Agent 工作流的一体化开源 AI 工作空间。

## 项目简介

Lumen 将知识库、长上下文对话、文档解析、笔记沉淀与运行时编排整合为一个可部署系统，目标是提供可持续演进的工程化 AI 工作台，而不是单点功能 Demo。

核心能力包括：

- 围绕知识库与文档上下文的智能对话
- 文档导入、解析、切块、索引与检索的完整 RAG 链路
- Gateway + LangGraph 驱动的 Agent Runtime
- 组织协作、管理后台与运营能力
- 本地部署与线上运行统一的工程结构

## 系统总览

```text
┌───────────────────────────────────────────────────────────────┐
│                           Frontend                            │
│          React + Vite app with feature-oriented UI           │
└──────────────────────────────┬────────────────────────────────┘
                               │
                               ▼
┌───────────────────────────────────────────────────────────────┐
│                            Backend                            │
│   FastAPI domain modules: auth, chat, knowledge, notes,      │
│   favorites, organization, admin, model_config               │
└───────────────┬──────────────────────────────┬────────────────┘
                │                              │
                ▼                              ▼
┌────────────────────────────┐   ┌──────────────────────────────┐
│        RAG Services        │   │        Runtime Layer          │
│  parsing, chunking, ES,    │   │  Gateway + LangGraph +       │
│  retrieval, indexing       │   │  agent workflows             │
└───────────────┬────────────┘   └──────────────┬───────────────┘
                │                               │
                └──────────────┬────────────────┘
                               ▼
┌───────────────────────────────────────────────────────────────┐
│                   Shared Infra and Storage                    │
│      PostgreSQL · Redis · Elasticsearch · MinIO · Nginx      │
└───────────────────────────────────────────────────────────────┘
```

## 仓库结构

```text
frontend/                Web app (React + Vite)
backend/                 FastAPI 业务后端
services/rag/            解析、切块、检索、索引服务
runtimes/                Runtime 运行目录
docker/                  本地部署入口（Compose + Nginx + init-db）
infra/                   非 Compose 基础设施资源
shared/                  跨部署共享配置与库
docs/                    设计文档、迁移说明、架构说明
```

详细说明见 [docs/仓库目录结构说明.md](./docs/%E4%BB%93%E5%BA%93%E7%9B%AE%E5%BD%95%E7%BB%93%E6%9E%84%E8%AF%B4%E6%98%8E.md)。

## 快速开始

### 环境要求

- Docker 与 Docker Compose
- Node.js 20+
- Python 3.12+

### 1. 克隆仓库

```bash
git clone git@github.com:changqingla/Lumen.git
cd Lumen
```

### 2. 配置环境变量

```bash
cp backend/.env.template backend/.env
```

然后补全 `backend/.env` 中所需的密钥与服务配置。运行时配置位于 `runtimes/config/`。

### 3. 构建前端

```bash
npm install
npm run build
```

### 4. 启动服务

```bash
cd docker
docker compose up -d
```

### 5. 访问地址

- Web: `http://localhost`
- Backend API: `http://localhost:13000`
- API Docs: `http://localhost:13000/api/docs`
- Gateway Docs: `http://localhost:8001/docs`
- LangGraph Docs: `http://localhost:2024`

## 开发命令

### Frontend

```bash
npm install
npm run web:dev
npm run web:check
npm run web:build
```

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run_migrations.py
uvicorn app.main:app --host 0.0.0.0 --port 13000
```

## 致谢

Lumen 的设计与演进过程中，受到以下开源项目的启发与帮助：

- [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli)
- [bytedance/deer-flow](https://github.com/bytedance/deer-flow)
- [infiniflow/ragflow](https://github.com/infiniflow/ragflow)

感谢这些优秀项目和背后的开源贡献者。

## 文档入口

- [docs/repo-structure-guideline.md](./docs/repo-structure-guideline.md)
- [docs/仓库目录结构说明.md](./docs/%E4%BB%93%E5%BA%93%E7%9B%AE%E5%BD%95%E7%BB%93%E6%9E%84%E8%AF%B4%E6%98%8E.md)
- [backend/README.md](./backend/README.md)
- [docs/model-config-feature-design.md](./docs/model-config-feature-design.md)
- [docs/insight-flow](./docs/insight-flow)

## 开源协议

本项目采用 Apache License 2.0 开源协议。详见 [LICENSE](./LICENSE)。

## 在线体验

- https://ireader.online/

## 联系方式

- `ht20201031@163.com`
