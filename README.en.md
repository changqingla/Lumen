# Lumen

> 中文版: [README.md](./README.md)

An open-source AI workspace for document understanding, knowledge retrieval, and agent-driven workflows.

## Overview

Lumen brings knowledge bases, long-context chat, document parsing, note capture, and runtime orchestration into one deployable system. The goal is to provide an engineering-ready AI workspace, not just a single-feature demo.

Core capabilities:

- Context-aware chat grounded in knowledge bases and documents
- End-to-end RAG pipeline for ingestion, parsing, chunking, indexing, and retrieval
- Agent runtime powered by Gateway and LangGraph
- Team collaboration features plus admin operations
- Repository structure designed for both local deployment and production evolution

## System Overview

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

## Repository Layout

```text
frontend/                Web app (React + Vite)
backend/                 FastAPI business backend
services/rag/            Parsing, chunking, retrieval, indexing services
runtimes/                Runtime layer and operational assets
docker/                  Local deployment entrypoint (Compose + Nginx + init-db)
infra/                   Non-Compose infrastructure assets
shared/                  Cross-deployment shared config and libraries
docs/                    Design docs, migration notes, architecture guides
```

For details, see [docs/repo-structure-guideline.md](./docs/repo-structure-guideline.md).

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Node.js 20+
- Python 3.12+

### 1. Clone the repository

```bash
git clone git@github.com:changqingla/Lumen.git
cd Lumen
```

### 2. Configure environment variables

```bash
cp backend/.env.template backend/.env
```

Then fill required service credentials and keys in `backend/.env`. Runtime configuration is under `runtimes/config/`.

### 3. Build frontend

```bash
npm install
npm run build
```

### 4. Start services

```bash
cd docker
docker compose up -d
```

### 5. Endpoints

- Web: `http://localhost`
- Backend API: `http://localhost:13000`
- API Docs: `http://localhost:13000/api/docs`
- Gateway Docs: `http://localhost:8001/docs`
- LangGraph Docs: `http://localhost:2024`

## Development Commands

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

## Acknowledgements

Lumen has been shaped and accelerated by ideas and practices from the following open-source projects:

- [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli)
- [bytedance/deer-flow](https://github.com/bytedance/deer-flow)
- [infiniflow/ragflow](https://github.com/infiniflow/ragflow)

Thanks to the maintainers and contributors behind these projects.

## Documentation

- [docs/repo-structure-guideline.md](./docs/repo-structure-guideline.md)
- [docs/仓库目录结构说明.md](./docs/%E4%BB%93%E5%BA%93%E7%9B%AE%E5%BD%95%E7%BB%93%E6%9E%84%E8%AF%B4%E6%98%8E.md)
- [backend/README.md](./backend/README.md)
- [docs/model-config-feature-design.md](./docs/model-config-feature-design.md)
- [docs/insight-flow](./docs/insight-flow)

## License

Licensed under Apache License 2.0. See [LICENSE](./LICENSE).

## Live Demo

- https://ireader.online/

## Contact

- `ht20201031@163.com`
