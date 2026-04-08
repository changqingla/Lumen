# Lumen

<div align="center">

### An open-source AI workspace for document understanding, knowledge retrieval, and agent-driven workflows

Lumen brings knowledge bases, long-context chat, document parsing, note capture, and runtime orchestration into one deployable system.  
It is not a chat wrapper demo. It is built as a real workspace for product-grade AI experiences, team collaboration, and long-term system evolution.

[中文](./README.md) · [Live Demo](https://ireader.online/) · [Backend Guide](./backend/README.md) · [Repository Guide](./docs/repo-structure-guideline.md)

<p>
  <img alt="license" src="https://img.shields.io/badge/License-Apache_2.0-111827?style=for-the-badge">
  <img alt="frontend" src="https://img.shields.io/badge/Frontend-React_%2B_Vite-0f172a?style=for-the-badge">
  <img alt="backend" src="https://img.shields.io/badge/Backend-FastAPI-0f172a?style=for-the-badge">
  <img alt="runtime" src="https://img.shields.io/badge/Runtime-Gateway_%2B_LangGraph-0f172a?style=for-the-badge">
  <img alt="rag" src="https://img.shields.io/badge/RAG-Elasticsearch_%2B_MinIO-0f172a?style=for-the-badge">
</p>

</div>

## Why Lumen

Many AI projects only solve one slice of the workflow:

- chat without durable knowledge
- document storage without execution
- a RAG demo without collaboration or operational structure

Lumen is designed around the whole workspace. It connects the full path from document ingestion and understanding to retrieval, execution, memory, and team reuse.

## Core Capabilities

| Capability | Description |
| --- | --- |
| Contextual Chat | Start conversations around knowledge bases, documents, and domain context instead of isolated prompts |
| RAG Pipeline | End-to-end ingestion, parsing, chunking, indexing, retrieval, and answer generation |
| Agent Runtime | Gateway + LangGraph runtime for tool use, task execution, and workflow orchestration |
| Knowledge Operations | Personal, organizational, and public knowledge bases with sharing and subscription flows |
| Notes & Memory | Turn useful outputs into reusable long-term knowledge assets |
| Team & Admin | Collaboration, member management, activation codes, and operational tooling |

## What You Will Find Here

### 1. A frontend that feels like a product, not an experiment

The frontend is not just a thin UI over model APIs. It is organized around real product domains such as chat, knowledge, notes, organization, and admin workflows.

### 2. A backend structured by business boundaries

The FastAPI backend is organized around domains like `auth`, `chat`, `knowledge`, `notes`, `favorites`, `organization`, `admin`, and `model_config`, making the system easier to extend over time.

### 3. A full document understanding and retrieval pipeline

Lumen supports multi-format document ingestion and completes the loop through parsing, chunking, indexing, retrieval, preview, and grounded question answering.

### 4. A runtime layer built for real execution

Agents are treated as runtime infrastructure, not just a UI gimmick. Gateway and LangGraph sit in the execution layer to support richer workflows, tool calls, and stateful task handling.

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
frontend/                React + Vite web application
backend/                 FastAPI business backend
services/rag/            Document parsing, chunking, retrieval, indexing
runtimes/                Runtime layer and operational assets
docker/                  Local deployment entrypoint
infra/                   Infrastructure assets outside Compose
shared/                  Shared config and libraries
docs/                    Architecture notes, migration docs, design material
```

For more detail, see [docs/repo-structure-guideline.md](./docs/repo-structure-guideline.md).

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

Then fill in the required credentials and service configuration in `backend/.env`. Runtime-related configuration lives under `runtimes/config/`.

### 3. Build the frontend

```bash
npm install
npm run build
```

### 4. Start the services

```bash
cd docker
docker compose up -d
```

### 5. Open the endpoints

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

## Good Fit For

- teams building knowledge-centric AI products beyond one-off chat demos
- systems that need document understanding, retrieval, and agent execution in one stack
- projects that want a clearer repository structure for long-term iteration

## Acknowledgements

Lumen has been shaped and accelerated by ideas and practices from the following open-source projects:

- [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli)
- [bytedance/deer-flow](https://github.com/bytedance/deer-flow)
- [infiniflow/ragflow](https://github.com/infiniflow/ragflow)

Thanks to the maintainers and contributors behind these projects.

## License

Licensed under Apache License 2.0. See [LICENSE](./LICENSE).

## Contact

- Live demo: [https://ireader.online/](https://ireader.online/)
- Email: `ht20201031@163.com`
