"""

在 Kubernetes 中为每个沙箱动态创建并管理独立的 Pod。
每个 ``sandbox_id`` 都会对应一个 Pod 和一个 NodePort Service，
后端通过 ``{NODE_HOST}:{NodePort}`` 直接访问对应沙箱。

Provisioner 通过挂载的 kubeconfig（``~/.kube/config``）连接宿主机
Kubernetes 集群。沙箱 Pod 运行在宿主机 K8s 上，后端同样通过
``{NODE_HOST}:{NodePort}`` 直接访问。

接口：
    POST   /api/sandboxes              — 创建沙箱 Pod 与 Service
    DELETE /api/sandboxes/{sandbox_id} — 销毁沙箱 Pod 与 Service
    GET    /api/sandboxes/{sandbox_id} — 获取沙箱状态与访问地址
    GET    /api/sandboxes              — 列出全部沙箱
    GET    /health                     — Provisioner 健康检查

架构（docker/docker-compose.yml）：
    ┌────────────┐  HTTP  ┌─────────────┐  K8s API  ┌──────────────┐
    │ remote     │ ─────▸ │ provisioner │ ────────▸ │  host K8s    │
    │ _backend   │        │ :8002       │           │  API server  │
    └────────────┘        └─────────────┘           └──────┬───────┘
                                                           │ 创建
                          ┌─────────────┐           ┌──────▼───────┐
                          │   backend   │ ────────▸ │   sandbox    │
                          │             │  直连     │   Pod(s)     │
                          └─────────────┘ NodePort  └──────────────┘
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

import urllib3
from fastapi import FastAPI, HTTPException
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException
from pydantic import BaseModel

# 仅屏蔽 urllib3 的 InsecureRequestWarning
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ── 配置项（均可通过环境变量调整） ───────────────────────────────────────

K8S_NAMESPACE = os.environ.get("K8S_NAMESPACE", "lumen")
SANDBOX_IMAGE = os.environ.get(
    "SANDBOX_IMAGE",
    "crpi-wh1i56a4x558rrhm.cn-hangzhou.personal.cr.aliyuncs.com/changqinga/sandbox:latest",
)
SKILLS_HOST_PATH = os.environ.get("SKILLS_HOST_PATH", "/skills")
THREADS_HOST_PATH = os.environ.get("THREADS_HOST_PATH", "/.lumen/threads")

# provisioner 容器内 kubeconfig 的路径。
# 通常会把宿主机的 ~/.kube/config 挂载到这里。
KUBECONFIG_PATH = os.environ.get("KUBECONFIG_PATH", "/root/.kube/config")

# 后端容器访问宿主机 Kubernetes 节点 NodePort 服务时使用的主机名 / IP。
# 在 macOS 的 Docker Desktop 中通常是 ``host.docker.internal``；
# 在 Linux 中则可能是宿主机局域网 IP。
NODE_HOST = os.environ.get("NODE_HOST", "host.docker.internal")

# ── K8s 客户端初始化 ────────────────────────────────────────────────────

core_v1: k8s_client.CoreV1Api | None = None


def _init_k8s_client() -> k8s_client.CoreV1Api:
    """

    优先尝试加载挂载进来的 kubeconfig，失败后回退到集群内配置。
    当 provisioner 自身运行在 K8s 内部时，这个回退路径会很有用。
    """
    if os.path.exists(KUBECONFIG_PATH):
        if os.path.isdir(KUBECONFIG_PATH):
            raise RuntimeError(
                f"KUBECONFIG_PATH points to a directory, expected a file: {KUBECONFIG_PATH}"
            )
        try:
            k8s_config.load_kube_config(config_file=KUBECONFIG_PATH)
            logger.info(f"Loaded kubeconfig from {KUBECONFIG_PATH}")
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load kubeconfig from {KUBECONFIG_PATH}: {exc}"
            ) from exc
    else:
        logger.warning(
            f"Kubeconfig not found at {KUBECONFIG_PATH}; trying in-cluster config"
        )
        try:
            k8s_config.load_incluster_config()
        except Exception as exc:
            raise RuntimeError(
                "Failed to initialize Kubernetes client. "
                f"No kubeconfig at {KUBECONFIG_PATH}, and in-cluster config is unavailable: {exc}"
            ) from exc

    # 当从 Docker 容器内部访问宿主机 K8s API 时，
    # kubeconfig 里可能写的是 ``localhost`` 或 ``127.0.0.1``。
    # 这里允许通过环境变量重写服务地址，确保可以访问到宿主机。
    k8s_api_server = os.environ.get("K8S_API_SERVER")
    if k8s_api_server:
        configuration = k8s_client.Configuration.get_default_copy()
        configuration.host = k8s_api_server
        # 本地集群中自签名证书比较常见
        configuration.verify_ssl = False
        api_client = k8s_client.ApiClient(configuration)
        return k8s_client.CoreV1Api(api_client)

    return k8s_client.CoreV1Api()


def _wait_for_kubeconfig(timeout: int = 30) -> None:
    """若配置了 kubeconfig，则等待其出现；超时后继续走回退逻辑。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(KUBECONFIG_PATH):
            if os.path.isfile(KUBECONFIG_PATH):
                logger.info(f"Found kubeconfig file at {KUBECONFIG_PATH}")
                return
            if os.path.isdir(KUBECONFIG_PATH):
                raise RuntimeError(
                    "Kubeconfig path is a directory. "
                    f"Please mount a kubeconfig file at {KUBECONFIG_PATH}."
                )
            raise RuntimeError(
                f"Kubeconfig path exists but is not a regular file: {KUBECONFIG_PATH}"
            )
        logger.info(f"Waiting for kubeconfig at {KUBECONFIG_PATH} …")
        time.sleep(2)
    logger.warning(
        f"Kubeconfig not found at {KUBECONFIG_PATH} after {timeout}s; "
        "will attempt in-cluster Kubernetes config"
    )


def _ensure_namespace() -> None:
    """如命名空间不存在则自动创建。"""
    try:
        core_v1.read_namespace(K8S_NAMESPACE)
        logger.info(f"Namespace '{K8S_NAMESPACE}' already exists")
    except ApiException as exc:
        if exc.status == 404:
            ns = k8s_client.V1Namespace(
                metadata=k8s_client.V1ObjectMeta(
                    name=K8S_NAMESPACE,
                    labels={
                        "app.kubernetes.io/name": "lumen",
                        "app.kubernetes.io/component": "sandbox",
                    },
                )
            )
            core_v1.create_namespace(ns)
            logger.info(f"Created namespace '{K8S_NAMESPACE}'")
        else:
            raise


# ── FastAPI 生命周期 ─────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global core_v1
    _wait_for_kubeconfig()
    core_v1 = _init_k8s_client()
    _ensure_namespace()
    logger.info("Provisioner is ready (using host Kubernetes)")
    yield


app = FastAPI(title="lumen Sandbox Provisioner", lifespan=lifespan)


# ── 请求 / 响应模型 ─────────────────────────────────────────────────────


class CreateSandboxRequest(BaseModel):
    sandbox_id: str
    thread_id: str


class SandboxResponse(BaseModel):
    sandbox_id: str
    sandbox_url: str  # 直连访问地址，例如 http://host.docker.internal:{NodePort}
    status: str


# ── K8s 资源辅助函数 ─────────────────────────────────────────────────────


def _pod_name(sandbox_id: str) -> str:
    return f"sandbox-{sandbox_id}"


def _svc_name(sandbox_id: str) -> str:
    return f"sandbox-{sandbox_id}-svc"


def _sandbox_url(node_port: int) -> str:
    """基于配置的 NODE_HOST 生成沙箱访问地址。"""
    return f"http://{NODE_HOST}:{node_port}"


def _build_pod(sandbox_id: str, thread_id: str) -> k8s_client.V1Pod:
    """构造单个沙箱的 Pod 清单。"""
    return k8s_client.V1Pod(
        metadata=k8s_client.V1ObjectMeta(
            name=_pod_name(sandbox_id),
            namespace=K8S_NAMESPACE,
            labels={
                "app": "lumen-sandbox",
                "sandbox-id": sandbox_id,
                "app.kubernetes.io/name": "lumen",
                "app.kubernetes.io/component": "sandbox",
            },
        ),
        spec=k8s_client.V1PodSpec(
            containers=[
                k8s_client.V1Container(
                    name="sandbox",
                    image=SANDBOX_IMAGE,
                    image_pull_policy="IfNotPresent",
                    ports=[
                        k8s_client.V1ContainerPort(
                            name="http",
                            container_port=8080,
                            protocol="TCP",
                        )
                    ],
                    readiness_probe=k8s_client.V1Probe(
                        http_get=k8s_client.V1HTTPGetAction(
                            path="/v1/sandbox",
                            port=8080,
                        ),
                        initial_delay_seconds=5,
                        period_seconds=5,
                        timeout_seconds=3,
                        failure_threshold=3,
                    ),
                    liveness_probe=k8s_client.V1Probe(
                        http_get=k8s_client.V1HTTPGetAction(
                            path="/v1/sandbox",
                            port=8080,
                        ),
                        initial_delay_seconds=10,
                        period_seconds=10,
                        timeout_seconds=3,
                        failure_threshold=3,
                    ),
                    resources=k8s_client.V1ResourceRequirements(
                        requests={
                            "cpu": "100m",
                            "memory": "256Mi",
                            "ephemeral-storage": "500Mi",
                        },
                        limits={
                            "cpu": "1000m",
                            "memory": "1Gi",
                            "ephemeral-storage": "500Mi",
                        },
                    ),
                    volume_mounts=[
                        k8s_client.V1VolumeMount(
                            name="skills",
                            mount_path="/mnt/skills",
                            read_only=True,
                        ),
                        k8s_client.V1VolumeMount(
                            name="user-data",
                            mount_path="/mnt/user-data",
                            read_only=False,
                        ),
                    ],
                    security_context=k8s_client.V1SecurityContext(
                        privileged=False,
                        allow_privilege_escalation=True,
                    ),
                )
            ],
            volumes=[
                k8s_client.V1Volume(
                    name="skills",
                    host_path=k8s_client.V1HostPathVolumeSource(
                        path=SKILLS_HOST_PATH,
                        type="Directory",
                    ),
                ),
                k8s_client.V1Volume(
                    name="user-data",
                    host_path=k8s_client.V1HostPathVolumeSource(
                        path=f"{THREADS_HOST_PATH}/{thread_id}/user-data",
                        type="DirectoryOrCreate",
                    ),
                ),
            ],
            restart_policy="Always",
        ),
    )


def _build_service(sandbox_id: str) -> k8s_client.V1Service:
    """构造 NodePort Service 清单（端口由 K8s 自动分配）。"""
    return k8s_client.V1Service(
        metadata=k8s_client.V1ObjectMeta(
            name=_svc_name(sandbox_id),
            namespace=K8S_NAMESPACE,
            labels={
                "app": "lumen-sandbox",
                "sandbox-id": sandbox_id,
                "app.kubernetes.io/name": "lumen",
                "app.kubernetes.io/component": "sandbox",
            },
        ),
        spec=k8s_client.V1ServiceSpec(
            type="NodePort",
            ports=[
                k8s_client.V1ServicePort(
                    name="http",
                    port=8080,
                    target_port=8080,
                    protocol="TCP",
                    # 省略 nodePort，让 K8s 从可用范围内自动分配
                )
            ],
            selector={
                "sandbox-id": sandbox_id,
            },
        ),
    )


def _get_node_port(sandbox_id: str) -> int | None:
    """读取 Service 上由 K8s 分配的 NodePort。"""
    try:
        svc = core_v1.read_namespaced_service(_svc_name(sandbox_id), K8S_NAMESPACE)
        for port in svc.spec.ports or []:
            if port.name == "http":
                return port.node_port
    except ApiException:
        pass
    return None


def _get_pod_phase(sandbox_id: str) -> str:
    """返回 Pod 阶段（Pending / Running / Succeeded / Failed / Unknown）。"""
    try:
        pod = core_v1.read_namespaced_pod(_pod_name(sandbox_id), K8S_NAMESPACE)
        return pod.status.phase or "Unknown"
    except ApiException:
        return "NotFound"


# ── API 接口 ─────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    """Provisioner 健康检查。"""
    return {"status": "ok"}


@app.post("/api/sandboxes", response_model=SandboxResponse)
async def create_sandbox(req: CreateSandboxRequest):
    """

    如果沙箱已经存在，则直接返回现有信息，保持幂等。
    """
    sandbox_id = req.sandbox_id
    thread_id = req.thread_id

    logger.info(
        f"Received request to create sandbox '{sandbox_id}' for thread '{thread_id}'"
    )

    # ── 快速路径：沙箱已存在 ───────────────────────────────────────────
    existing_port = _get_node_port(sandbox_id)
    if existing_port:
        return SandboxResponse(
            sandbox_id=sandbox_id,
            sandbox_url=_sandbox_url(existing_port),
            status=_get_pod_phase(sandbox_id),
        )

    # ── 创建 Pod ──────────────────────────────────────────────────────
    try:
        core_v1.create_namespaced_pod(K8S_NAMESPACE, _build_pod(sandbox_id, thread_id))
        logger.info(f"Created Pod {_pod_name(sandbox_id)}")
    except ApiException as exc:
        if exc.status != 409:  # 409 表示资源已存在
            raise HTTPException(
                status_code=500, detail=f"Pod creation failed: {exc.reason}"
            )

    # ── 创建 Service ─────────────────────────────────────────────────
    try:
        core_v1.create_namespaced_service(K8S_NAMESPACE, _build_service(sandbox_id))
        logger.info(f"Created Service {_svc_name(sandbox_id)}")
    except ApiException as exc:
        if exc.status != 409:
            # Service 创建失败时回滚已创建的 Pod
            try:
                core_v1.delete_namespaced_pod(_pod_name(sandbox_id), K8S_NAMESPACE)
            except ApiException:
                pass
            raise HTTPException(
                status_code=500, detail=f"Service creation failed: {exc.reason}"
            )

    # ── 读取自动分配的 NodePort ───────────────────────────────────────
    node_port: int | None = None
    for _ in range(20):
        node_port = _get_node_port(sandbox_id)
        if node_port:
            break
        time.sleep(0.5)

    if not node_port:
        raise HTTPException(
            status_code=500, detail="NodePort was not allocated in time"
        )

    return SandboxResponse(
        sandbox_id=sandbox_id,
        sandbox_url=_sandbox_url(node_port),
        status=_get_pod_phase(sandbox_id),
    )


@app.delete("/api/sandboxes/{sandbox_id}")
async def destroy_sandbox(sandbox_id: str):
    """销毁一个沙箱对应的 Pod 和 Service。"""
    errors: list[str] = []

    # 删除 Service
    try:
        core_v1.delete_namespaced_service(_svc_name(sandbox_id), K8S_NAMESPACE)
        logger.info(f"Deleted Service {_svc_name(sandbox_id)}")
    except ApiException as exc:
        if exc.status != 404:
            errors.append(f"service: {exc.reason}")

    # 删除 Pod
    try:
        core_v1.delete_namespaced_pod(_pod_name(sandbox_id), K8S_NAMESPACE)
        logger.info(f"Deleted Pod {_pod_name(sandbox_id)}")
    except ApiException as exc:
        if exc.status != 404:
            errors.append(f"pod: {exc.reason}")

    if errors:
        raise HTTPException(
            status_code=500, detail=f"Partial cleanup: {', '.join(errors)}"
        )

    return {"ok": True, "sandbox_id": sandbox_id}


@app.get("/api/sandboxes/{sandbox_id}", response_model=SandboxResponse)
async def get_sandbox(sandbox_id: str):
    """返回指定沙箱的当前状态和访问地址。"""
    node_port = _get_node_port(sandbox_id)
    if not node_port:
        raise HTTPException(status_code=404, detail=f"Sandbox '{sandbox_id}' not found")

    return SandboxResponse(
        sandbox_id=sandbox_id,
        sandbox_url=_sandbox_url(node_port),
        status=_get_pod_phase(sandbox_id),
    )


@app.get("/api/sandboxes")
async def list_sandboxes():
    """列出当前命名空间内由 provisioner 管理的全部沙箱。"""
    try:
        services = core_v1.list_namespaced_service(
            K8S_NAMESPACE,
            label_selector="app=lumen-sandbox",
        )
    except ApiException as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to list services: {exc.reason}"
        )

    sandboxes: list[SandboxResponse] = []
    for svc in services.items:
        sid = (svc.metadata.labels or {}).get("sandbox-id")
        if not sid:
            continue
        node_port = None
        for port in svc.spec.ports or []:
            if port.name == "http":
                node_port = port.node_port
                break
        if node_port:
            sandboxes.append(
                SandboxResponse(
                    sandbox_id=sid,
                    sandbox_url=_sandbox_url(node_port),
                    status=_get_pod_phase(sid),
                )
            )

    return {"sandboxes": sandboxes, "count": len(sandboxes)}
