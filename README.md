# Lumen

<div align="center">

### 面向文档理解、知识检索与 Agent 工作流的一体化开源 AI 工作空间

把知识库、长上下文对话、文档解析、笔记沉淀与运行时编排放进同一个可部署系统里。  
它不是一个只会“聊天”的 Demo，而是一个为真实产品形态、真实团队协作和真实运行环境设计的 AI Workspace。

[English](./README.en.md) · [在线体验](https://ireader.online/) · [后端说明](./backend/README.md) · [仓库结构](./docs/%E4%BB%93%E5%BA%93%E7%9B%AE%E5%BD%95%E7%BB%93%E6%9E%84%E8%AF%B4%E6%98%8E.md)

<p>
  <img alt="license" src="https://img.shields.io/badge/License-Apache_2.0-111827?style=for-the-badge">
  <img alt="frontend" src="https://img.shields.io/badge/Frontend-React_%2B_Vite-0f172a?style=for-the-badge">
  <img alt="backend" src="https://img.shields.io/badge/Backend-FastAPI-0f172a?style=for-the-badge">
  <img alt="runtime" src="https://img.shields.io/badge/Runtime-Gateway_%2B_LangGraph-0f172a?style=for-the-badge">
  <img alt="rag" src="https://img.shields.io/badge/RAG-Elasticsearch_%2B_MinIO-0f172a?style=for-the-badge">
</p>

<p>
  <img alt="Lumen showcase carousel" src="./docs/assets/readme-showcase-carousel.gif" width="100%">
</p>

</div>

## 为什么是 Lumen

很多 AI 项目只覆盖工作流中的一个局部：

- 有聊天，但没有知识沉淀
- 有文档管理，但没有执行能力
- 有 RAG 演示，但没有组织协作和运行体系

Lumen 关注的是完整工作空间，而不是单个功能点。它希望把“知识进入系统、被理解、被检索、被调用、被沉淀、被团队复用”这条链路真正串起来。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| Contextual Chat | 围绕知识库、文档和业务上下文发起对话，而不是孤立聊天 |
| RAG Pipeline | 支持导入、解析、切块、索引、检索与回答的完整链路 |
| Agent Runtime | 通过 Gateway + LangGraph 承载工具调用、任务执行和工作流编排 |
| Knowledge Operations | 支持个人、组织、公开知识库，以及共享、订阅、迁移等运营动作 |
| Notes & Memory | 将对话结果沉淀为长期知识资产，支持回顾、收藏和组织复用 |
| Team & Admin | 提供组织协作、成员管理、激活码与后台运营能力 |

## 你会在这里看到什么

### 1. 一个像产品而不是像实验页的前端

Lumen 的前端不是把模型接口包一层 UI，而是按真实产品域拆分。聊天、知识库、笔记、组织、管理后台都有明确的功能边界和交互承担。

### 2. 一个按业务域组织的后端

后端以 FastAPI 为核心，围绕 `auth`、`chat`、`knowledge`、`notes`、`favorites`、`organization`、`admin`、`model_config` 等模块收口，便于持续演进。

### 3. 一条完整的文档理解与检索链路

项目支持多种文档格式导入，完成解析、切块、索引、检索、预览与问答闭环，并为后续质量治理和运营扩展留好了空间。

### 4. 一个真正可接任务的 Runtime 层

Lumen 并不把 Agent 当作一个展示按钮，而是把 Gateway 与 LangGraph 放进运行时层，承接更复杂的工具执行、状态流转和工作流组织。

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
frontend/                React + Vite Web 应用
backend/                 FastAPI 业务后端
services/rag/            文档解析、切块、检索、索引服务
runtimes/                Runtime 运行目录与辅助能力
docker/                  本地部署入口（Compose / Nginx / init-db）
infra/                   非 Compose 基础设施资源
shared/                  跨部署共享配置与基础库
docs/                    架构说明、迁移文档与设计资料
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

补全 `backend/.env` 中的密钥和服务配置。运行时相关配置位于 `runtimes/config/`。

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

## 适合什么场景

- 想做真正能落地的知识库产品，而不是一次性问答页面
- 想把文档理解、检索和 Agent 工作流放到同一套系统中
- 想基于清晰的工程结构继续扩展团队协作和平台能力

## 致谢

Lumen 的设计与演进过程中，受到以下开源项目的启发与帮助：

- [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli)
- [bytedance/deer-flow](https://github.com/bytedance/deer-flow)
- [infiniflow/ragflow](https://github.com/infiniflow/ragflow)

感谢这些优秀项目和背后的开源贡献者。

## 开源协议

本项目采用 Apache License 2.0 开源协议。详见 [LICENSE](./LICENSE)。

## 联系方式

- 体验地址: [https://ireader.online/](https://ireader.online/)
- 邮箱: `ht20201031@163.com`
