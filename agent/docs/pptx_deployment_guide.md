# PPTX Skill 部署指南

本文档说明 PPTX Skill 功能上线前，需要在各容器/宿主机中安装的依赖包和服务配置。

## 架构概览

```
宿主机
├── Docker daemon（已有）
├── reader_agent 容器（knowledge-retrieval:v1.2 镜像）
│   ├── Python 3.11 ✅ 已有
│   ├── Node.js 20 ✅ 已有
│   └── docker Python SDK ❌ 需安装
├── pptx-sandbox 容器（临时，由 Docker SDK 按需启动）
│   ├── Node.js 20 ✅（基于 node:20-slim）
│   ├── Python 3 ✅
│   ├── defusedxml / Pillow / markitdown ✅
│   ├── LibreOffice Impress ✅（视觉 QA）
│   ├── Poppler (pdftoppm) ✅（视觉 QA）
│   ├── pptxgenjs ✅（全局安装）
│   └── GCC ✅（soffice.py LD_PRELOAD shim）
├── reader_minio 容器 ✅ 已有
└── reader_nginx 容器 ✅ 已配置 /minio/ 代理
```

---

## 1. reader_agent 容器（必须）

基础镜像：`knowledge-retrieval:v1.2`（Debian trixie, Python 3.11, Node.js 20）

### 1.1 安装 Python 包

进入 reader_agent 容器后执行：

```bash
# Docker SDK — 容器沙盒模式的核心依赖
pip install "docker>=7.0.0"

# MinIO SDK — Agent 侧文件上传（镜像中已有 minio 7.2.20，确认即可）
pip show minio  # 应输出 7.2.20+
```

验证：

```bash
python3 -c "import docker; print(docker.__version__)"
# 应输出 7.x.x

python3 -c "from minio import Minio; print('OK')"
# 应输出 OK
```

---

## 2. 构建 pptx-sandbox 沙盒镜像（宿主机，必须）

Anthropic 官方 pptx skill 的工作流同时需要 Python 和 Node.js：
- Python 脚本：`thumbnail.py`、`unpack.py`、`pack.py`、`clean.py`（依赖 `defusedxml`、`Pillow`）
- Node.js 脚本：pptxgenjs 生成 PPT
- 系统工具：`markitdown`（PPTX 文本提取）、`soffice`（PDF 转换）、`pdftoppm`（图片转换）

我们提供了自定义 Dockerfile `docker/Dockerfile.pptx-sandbox`，基于 `knowledge-retrieval:v1.2`（已有 Python 3.11 + Node.js 20）添加了所有额外依赖。

### 2.1 构建镜像

```bash
cd /path/to/Reader
docker build -t pptx-sandbox:latest -f docker/Dockerfile.pptx-sandbox docker/
```

> 首次构建约需 5-10 分钟（主要是 LibreOffice 安装），镜像约 800MB。
> 如果不需要视觉 QA 功能，可以在 Dockerfile 中注释掉 `libreoffice-impress` 和 `poppler-utils`，镜像可缩小到约 300MB。

### 2.2 验证镜像

```bash
# 验证 Node.js
docker run --rm pptx-sandbox:latest node -v
# 应输出 v20.x.x

# 验证 Python + 依赖
docker run --rm pptx-sandbox:latest python3 -c "
import defusedxml; print('defusedxml OK')
from PIL import Image; print('Pillow OK')
"

# 验证 markitdown
docker run --rm pptx-sandbox:latest python3 -m markitdown --help 2>&1 | head -1

# 验证 pptxgenjs
docker run --rm pptx-sandbox:latest node -e "require('pptxgenjs'); console.log('pptxgenjs OK')"

# 验证 soffice（可选）
docker run --rm pptx-sandbox:latest soffice --version

# 验证 pdftoppm（可选）
docker run --rm pptx-sandbox:latest pdftoppm -v
```

### 2.3 SKILL.md 配置

Anthropic pptx skill 的 SKILL.md 中 `runtime.image` 已更新为 `pptx-sandbox:latest`：

```yaml
runtime:
  image: pptx-sandbox:latest
  network: bridge
  memory: 512m
  timeout: 120
```

Agent 加载此 skill 后，所有 `run_command` 调用都会在 `pptx-sandbox` 容器中执行，Python 和 Node.js 脚本均可正常运行。

---

## 3. Docker Socket 挂载（docker-compose.yml，已配置）

`reader_agent` 服务需要访问宿主机的 Docker daemon 来启动临时容器。以下配置已在 `docker/docker-compose.yml` 中完成：

```yaml
reader_agent:
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
  group_add:
    - "999"  # docker 组 GID
```

验证（在 reader_agent 容器内）：

```bash
python3 -c "
import docker
client = docker.from_env()
print(client.ping())
"
# 应输出 True
```

> 如果输出报错 `PermissionError` 或 `ConnectionRefusedError`，检查：
> 1. 宿主机 docker 组的 GID 是否为 999（`getent group docker`）
> 2. 如果不是 999，修改 `group_add` 为实际 GID

---

## 4. MinIO 存储桶（自动创建，无需手动操作）

Agent 首次上传文件时会自动创建 `agent-outputs` 存储桶。无需手动操作。

如果需要手动确认：

```bash
# 在宿主机上
docker exec reader_minio mc alias set local http://localhost:9000 reader reader_dev_password
docker exec reader_minio mc ls local/
# 查看是否有 agent-outputs 桶
```

---

## 5. Nginx 代理（已配置）

`nginx/reader_qaq.conf` 中已配置 `/minio/` 路径代理到 MinIO。Agent 上传文件后返回的下载链接格式为：

```
/minio/agent-outputs/{session_id}/{filename}
```

前端可直接通过此路径下载文件，无需额外配置。

---

## 6. 环境变量（已配置）

以下环境变量已在 `agent/.env` 中配置，无需修改：

| 变量 | 值 | 说明 |
|------|-----|------|
| `MINIO_ENDPOINT` | `reader_minio:9000` | MinIO 服务地址 |
| `MINIO_ACCESS_KEY` | `reader` | MinIO 访问密钥 |
| `MINIO_SECRET_KEY` | `reader_dev_password` | MinIO 密钥 |
| `MINIO_BUCKET` | `agent-outputs` | 存储桶名称 |
| `MINIO_SECURE` | `false` | 不使用 HTTPS |
| `MINIO_PUBLIC_ENDPOINT` | `nginx` | 通过 Nginx 代理返回下载链接 |

---

## 7. 两种 Skill 模式对比

| 特性 | Anthropic 官方 pptx | 自定义 pptx-generation |
|------|---------------------|----------------------|
| 位置 | `agent/skills/pptx/` | `agent/skills/pptx-generation/` |
| 沙盒镜像 | `pptx-sandbox:latest` | `node:20-slim` |
| 从零创建 PPT | ✅ pptxgenjs | ✅ pptxgenjs |
| 编辑现有 PPT | ✅ unpack/edit/pack 工作流 | ❌ 仅创建 |
| 读取 PPT 内容 | ✅ markitdown | ❌ |
| 缩略图预览 | ✅ thumbnail.py | ❌ |
| 视觉 QA | ✅ soffice + pdftoppm | ❌ |
| 设计指导 | ✅ 详细的配色/排版/字体建议 | ✅ 基础模板 |
| 安全隔离 | Docker 容器级隔离 | Docker 容器级隔离 |
| 网络访问 | bridge（npm install 需要） | bridge |

> 推荐使用 Anthropic 官方 pptx skill，功能更完整。两个 skill 可以共存，Agent 会根据上下文选择。

---

## 8. 快速部署检查清单

### 8.1 宿主机

```bash
# 1. 构建 pptx-sandbox 镜像
docker build -t pptx-sandbox:latest -f docker/Dockerfile.pptx-sandbox docker/

# 2. 验证镜像
docker run --rm pptx-sandbox:latest sh -c "node -v && python3 -c 'import defusedxml; print(\"OK\")'"

# 3. 确认 node:20-slim 也存在（自定义 pptx-generation skill 使用）
docker images node:20-slim --format "{{.Repository}}:{{.Tag}}"
```

### 8.2 reader_agent 容器内

```bash
# 1. Docker SDK
python3 -c "import docker; c=docker.from_env(); print('Docker:', c.ping())"

# 2. MinIO SDK
python3 -c "from minio import Minio; print('MinIO: OK')"

# 3. Docker socket 权限 + pptx-sandbox 镜像
python3 -c "
import docker
c = docker.from_env()
r = c.containers.run('pptx-sandbox:latest', 'python3 -c \"import defusedxml; print(42)\"', remove=True)
print('Container test:', r.decode().strip())
"
# 应输出 Container test: 42

# 4. Workspace 目录可写
python3 -c "
from pathlib import Path
p = Path('/tmp/agent_workspace/test')
p.mkdir(parents=True, exist_ok=True)
(p / 'test.txt').write_text('ok')
print('Workspace:', (p / 'test.txt').read_text())
import shutil; shutil.rmtree(str(p))
"
```

全部输出正常即可上线。

---

## 9. 可选：精简沙盒镜像

如果不需要视觉 QA 功能（`soffice` + `pdftoppm`），可以编辑 `docker/Dockerfile.pptx-sandbox`，注释掉 LibreOffice 和 Poppler 的安装行：

```dockerfile
# libreoffice-impress \
# poppler-utils \
```

这样镜像体积从约 800MB 缩小到约 300MB。视觉 QA 是 Anthropic skill 的可选功能，不影响核心的 PPT 创建和编辑流程。
