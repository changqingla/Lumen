# Lumen

<p align="center">
  <strong>面向文档理解、知识检索与 Agent 工作流的一体化开源 AI 工作空间</strong>
  <br />
  <strong>An open-source AI workspace for document understanding, knowledge retrieval, and agent-driven workflows.</strong>
</p>

<p align="center">
  Lumen 将知识库、长上下文对话、文档解析、笔记沉淀与运行时编排整合为一个可部署系统。
  <br />
  It brings knowledge bases, long-context chat, document parsing, note capture, and runtime orchestration into one deployable system.
</p>

<p align="center">
  <a href="https://ireader.online/">Live Demo</a>
  ·
  <a href="./docs/%E4%BB%93%E5%BA%93%E7%9B%AE%E5%BD%95%E7%BB%93%E6%9E%84%E8%AF%B4%E6%98%8E.md">Repository Guide</a>
  ·
  <a href="./docs/repo-structure-guideline.md">Structure Guideline</a>
  ·
  <a href="./backend/README.md">Backend Guide</a>
</p>

<p align="center">
  <img alt="license" src="https://img.shields.io/badge/license-Apache--2.0-111827?style=for-the-badge">
  <img alt="frontend" src="https://img.shields.io/badge/frontend-React%20%2B%20Vite-0f172a?style=for-the-badge">
  <img alt="backend" src="https://img.shields.io/badge/backend-FastAPI-0f172a?style=for-the-badge">
  <img alt="runtime" src="https://img.shields.io/badge/runtime-LangGraph%20%2B%20Gateway-0f172a?style=for-the-badge">
  <img alt="rag" src="https://img.shields.io/badge/RAG-Elasticsearch%20%2B%20MinIO-0f172a?style=for-the-badge">
</p>

## Product Vision | 产品愿景

大多数 AI 工具只解决一个局部问题：

- 只有聊天，没有知识沉淀
- 只有文档管理，没有执行能力
- 只有 RAG demo，没有团队协作与真实运维能力

Most AI products solve only one slice of the workflow:

- chat without durable knowledge
- document storage without execution
- a RAG demo without collaboration or production operations

Lumen 希望提供的是一个完整工作空间，而不是一个单点功能页。

Lumen is built as a complete workspace rather than a single-feature prototype.

它把以下能力放进同一个系统里：

- 面向真实使用场景的 Web 产品界面
- 面向业务域组织的 FastAPI 后端
- 知识库导入、切块、检索、问答的 RAG 链路
- Gateway + LangGraph 驱动的 Agent Runtime
- 可本地部署、可线上运行、可持续扩展的工程结构

It combines:

- a production-style web experience
- a domain-oriented FastAPI backend
- an end-to-end RAG ingestion and retrieval pipeline
- an agent runtime powered by Gateway and LangGraph
- a deployable repository layout built for long-term evolution

## Why It Feels Different | 为什么它不像普通 Demo

- `Knowledge-first`
  对话不是脱离语境的聊天，而是围绕知识库、文档、组织共享内容展开。
  Chat is grounded in knowledge bases, documents, and shared organizational context.

- `Agent-ready`
  不止是回答问题，还支持工具调用、产物生成、工作流执行与运行时编排。
  It goes beyond Q&A into tool execution, artifact generation, and runtime orchestration.

- `Built for accumulation`
  知识不会在一次会话后消失，可以沉淀为笔记、收藏、知识库内容与组织资产。
  Knowledge survives beyond a single conversation through notes, favorites, KBs, and team assets.

- `Structured as a real project`
  仓库已经按 `features/` 与 `modules/` 收口，代码查找路径更接近产品边界而不是历史堆叠。
  The repository is organized around product domains instead of historical technical sprawl.

## Core Experience | 核心体验

### 1. Chat with context | 带上下文的智能对话

- 支持基于知识库、单篇文档或自由输入上下文发起对话。
- 支持流式回复、中断生成、重新生成和多模型切换。
- 支持展示思考轨迹、工具轨迹与产物输出。

- Start conversations against selected KBs, documents, or free-form context.
- Stream responses, interrupt generations, regenerate answers, and switch models.
- Inspect reasoning traces, tool traces, and generated artifacts.

### 2. Knowledge operations | 知识库运营能力

- 支持个人、组织、公开知识库。
- 支持共享、订阅、收藏、迁移、失败重试与内容管理。
- 支持知识广场与高价值资料复用场景。

- Manage personal, organization, and public knowledge bases.
- Control sharing, subscriptions, favorites, retries, migrations, and lifecycle operations.
- Enable discovery and reuse through public and curated knowledge workflows.

### 3. Parsing and retrieval | 文档解析与检索

- 支持 PDF、DOCX、Markdown、TXT 等多格式导入。
- 支持切块、索引、检索与文档预览。
- 支持围绕 Chunk 进行质量治理与搜索运营。

- Ingest PDFs, DOCX, Markdown, TXT, and more.
- Parse, chunk, index, retrieve, and preview content in the web UI.
- Maintain chunk-level operations for retrieval quality.

### 4. Notes and memory | 笔记与知识沉淀

- 支持文件夹化笔记系统与 Markdown 预览。
- 支持把高价值对话沉淀为长期知识资产。
- 支持收藏与回顾高频资料。

- Use a folder-based notes system with Markdown preview.
- Turn valuable conversations into durable knowledge assets.
- Favorite and revisit high-value content.

### 5. Team and admin workflows | 团队协作与运营后台

- 支持组织创建、加入、成员管理与组织共享。
- 支持激活码、用户管理与管理统计。
- 支持更贴近平台化产品的运营能力。

- Support organizations, shared access, member management, and admin operations.
- Manage activation codes, users, and operational dashboards.
- Operate the system like a real product, not just a local experiment.

## System Overview | 系统总览

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

## Repository Layout | 仓库结构

```text
frontend/                Web app (React + Vite)
  src/
    app/                 App shell, routing, providers
    features/            Product domains on the frontend
    shared/              Cross-feature UI, contracts, hooks, utilities
    styles/              Global styles

backend/                 FastAPI business backend
  app/                   Application entry and assembly
  modules/               Backend business domains
  infrastructure/        External service adapters
  shared/                Shared business capabilities
  config/                Runtime settings and integration config
  migrations/            DB initialization and migration scripts
  tests/                 Backend tests

services/
  rag/                   Parsing, chunking, retrieval, indexing services

runtimes/
  backend/               Runtime backend
  config/                Runtime configuration
  skills/                Runtime skills
  scripts/               Runtime helpers

docker/                  Canonical local deployment entrypoint
  docker-compose.yml
  nginx/
  init-db/

infra/                   Non-Compose infrastructure assets
shared/                  Cross-deployment shared config and libraries
docs/                    Design docs, migration notes, architecture guides
```

详细说明见 [docs/仓库目录结构说明.md](./docs/%E4%BB%93%E5%BA%93%E7%9B%AE%E5%BD%95%E7%BB%93%E6%9E%84%E8%AF%B4%E6%98%8E.md)。

For more detail, see [docs/仓库目录结构说明.md](./docs/%E4%BB%93%E5%BA%93%E7%9B%AE%E5%BD%95%E7%BB%93%E6%9E%84%E8%AF%B4%E6%98%8E.md).

## Quick Start | 快速开始

### Prerequisites | 环境要求

- Docker and Docker Compose
- Node.js 20+
- Python 3.12+

### 1. Clone | 克隆仓库

```bash
git clone git@github.com:changqingla/Reader.git
cd Reader
git checkout relase2.3.0
```

### 2. Configure environment | 配置环境变量

```bash
cp backend/.env.template backend/.env
```

然后补全 `backend/.env` 中所需的密钥与服务配置。

Then fill in the required service credentials and keys in `backend/.env`.

运行时配置位于 `runtimes/config/`。

Runtime-related configuration lives in `runtimes/config/`.

### 3. Build frontend | 构建前端

```bash
npm install
npm run build
```

### 4. Start the stack | 启动整套服务

```bash
cd docker
docker compose up -d
```

### 5. Visit endpoints | 访问地址

- Web app: `http://localhost`
- Backend API: `http://localhost:13000`
- API docs: `http://localhost:13000/api/docs`
- Gateway docs: `http://localhost:8001/docs`
- LangGraph docs: `http://localhost:2024`

## Development | 开发说明

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

### Useful validation commands | 常用校验命令

```bash
npm run build
pytest
docker compose -f docker/docker-compose.yml up -d
```

## Acknowledgements | 致谢

Lumen 的设计与演进过程中，受到以下开源项目的启发与帮助：

Lumen has been shaped and accelerated by ideas and practices from the following open-source projects:

- [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli)
  在 CLI agent 交互、任务执行体验和开发者工作流层面带来了很多启发。
  Inspiration for CLI agent interaction patterns, execution ergonomics, and developer workflow design.

- [bytedance/deer-flow](https://github.com/bytedance/deer-flow)
  在 Agent 工作流编排、任务流设计和系统化执行体验上提供了重要参考。
  A strong reference for agent workflow orchestration and system-level task execution design.

- [infiniflow/ragflow](https://github.com/infiniflow/ragflow)
  在知识库产品形态、文档解析链路与 RAG 工程实践上带来了很多启发。
  A major source of inspiration for KB product design, document ingestion pipelines, and RAG engineering practices.

感谢这些优秀项目和背后的开源贡献者。

Deep thanks to the maintainers and contributors behind these projects.

## Documentation | 文档入口

- [docs/repo-structure-guideline.md](./docs/repo-structure-guideline.md)
- [docs/仓库目录结构说明.md](./docs/%E4%BB%93%E5%BA%93%E7%9B%AE%E5%BD%95%E7%BB%93%E6%9E%84%E8%AF%B4%E6%98%8E.md)
- [backend/README.md](./backend/README.md)
- [docs/model-config-feature-design.md](./docs/model-config-feature-design.md)
- [docs/insight-flow](./docs/insight-flow)

## License | 开源协议

This project is licensed under the Apache License 2.0.

本项目采用 Apache License 2.0 开源协议。

See [LICENSE](./LICENSE) for details.

## Live Project | 在线体验

- Production site: [https://ireader.online/](https://ireader.online/)

## Contact | 联系方式

- Email: `ht20201031@163.com`
