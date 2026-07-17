from pydantic import BaseModel, ConfigDict, Field


class VolumeMountConfig(BaseModel):
    """卷挂载配置。"""

    host_path: str = Field(..., description="宿主机路径")
    container_path: str = Field(..., description="容器内路径")
    read_only: bool = Field(default=False, description="是否只读挂载")


class SandboxConfig(BaseModel):
    """
    沙箱通用配置。

    通用选项：
        use: 沙箱提供者的类路径（必填）

    AioSandboxProvider 专有选项：
        image: 使用的 Docker 镜像（默认使用仓库验证过的不可变 sha256 digest）
        port: 沙箱容器基础端口（默认：8080）
        replicas: 并发沙箱容器最大数量（默认：3）。达到上限后会驱逐最近最少使用的实例以腾出空间。
        container_prefix: 容器名称前缀（默认：lumen-sandbox）
        idle_timeout: 沙箱释放前的空闲超时（秒）（默认：600 = 10 分钟）。设为 0 可禁用。
        mounts: 与容器共享目录的卷挂载列表
        environment: 注入容器的环境变量（以 `$` 开头的值会从宿主机环境解析）
    """

    use: str = Field(
        ...,
        description="沙箱提供者的类路径（例如：src.sandbox.local:LocalSandboxProvider）",
    )
    image: str | None = Field(
        default=None,
        description="沙箱容器使用的 Docker 镜像",
    )
    port: int | None = Field(
        default=None,
        description="沙箱容器基础端口",
    )
    replicas: int | None = Field(
        default=None,
        description="并发沙箱容器最大数量（默认：3）。达到上限后会驱逐最近最少使用的实例以腾出空间。",
    )
    container_prefix: str | None = Field(
        default=None,
        description="容器名称前缀",
    )
    idle_timeout: int | None = Field(
        default=None,
        description="沙箱释放前的空闲超时（秒）（默认：600 = 10 分钟）。设为 0 可禁用。",
    )
    pids_limit: int = Field(
        default=512,
        ge=32,
        le=4096,
        description="单个 Docker 沙箱允许的最大进程数。",
    )
    drop_all_capabilities: bool = Field(
        default=True,
        description="是否删除 Docker 沙箱的全部 Linux capabilities。",
    )
    mounts: list[VolumeMountConfig] = Field(
        default_factory=list,
        description="宿主机与容器之间共享目录的卷挂载列表",
    )
    environment: dict[str, str] = Field(
        default_factory=dict,
        description="注入到沙箱容器的环境变量。以 `$` 开头的值会从宿主机环境变量中解析。",
    )

    model_config = ConfigDict(extra="allow")
