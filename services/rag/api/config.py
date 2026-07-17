#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Doc_Pipeline_Service 统一配置管理

使用 pydantic BaseSettings 从环境变量和 .env 文件加载配置，
替代原先散落在代码中的 os.getenv() 调用。
"""

from pathlib import Path
from typing import Set

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# 项目根目录（rag/）
_project_root = Path(__file__).parent.parent.absolute()


class DocPipelineSettings(BaseSettings):
    """Doc_Pipeline_Service 配置"""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

    # 服务
    HOST: str = "0.0.0.0"
    PORT: int = 7791

    # Elasticsearch
    ES_HOST: str = "http://elasticsearch:9200"
    ES_USERNAME: str = ""
    ES_PASSWORD: str = ""
    RAG_INTERNAL_API_TOKEN: str = ""

    # Worker credentials. Async task metadata stores no secret values; workers
    # resolve these settings immediately before calling external services.
    EMBEDDING_BASE_URL: str = ""
    EMBEDDING_API_KEY: str = ""
    CV_BASE_URL: str = ""
    CV_API_KEY: str = ""

    # Redis
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6378
    REDIS_USERNAME: str = "lumen"
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 1

    # 并发控制
    MAX_CONCURRENT_TASKS: int = 4
    MAX_WORKERS: int = 16
    CHUNK_PROCESS_WORKERS: int = 4
    TASK_VISIBILITY_TIMEOUT_SECONDS: float = Field(default=120.0, gt=0)
    TASK_HEARTBEAT_INTERVAL_SECONDS: float = Field(default=30.0, gt=0)
    TASK_STALE_RECOVERY_INTERVAL_SECONDS: float = Field(default=15.0, gt=0)

    # 文件处理
    MAX_FILE_SIZE: int = 100 * 1024 * 1024  # 100MB

    # 类属性（非环境变量）
    SUPPORTED_FORMATS: Set[str] = {
        ".pdf",
        ".docx",
        ".doc",
        ".txt",
        ".md",
        ".html",
        ".ppt",
        ".pptx",
        ".xls",
        ".xlsx",
        ".csv",
    }
    TEMP_DIR: str = str(_project_root / "tmp")

    @model_validator(mode="after")
    def validate_task_lease_timing(self):
        """Keep heartbeat cadence strictly inside the Redis visibility window."""
        if self.TASK_HEARTBEAT_INTERVAL_SECONDS >= self.TASK_VISIBILITY_TIMEOUT_SECONDS:
            raise ValueError(
                "TASK_HEARTBEAT_INTERVAL_SECONDS must be less than "
                "TASK_VISIBILITY_TIMEOUT_SECONDS"
            )
        return self

settings = DocPipelineSettings()
