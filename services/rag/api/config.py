#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Doc_Pipeline_Service 统一配置管理

使用 pydantic BaseSettings 从环境变量和 .env 文件加载配置，
替代原先散落在代码中的 os.getenv() 调用。
"""

from pathlib import Path
from typing import Set

from pydantic_settings import BaseSettings


# 项目根目录（rag/）
_project_root = Path(__file__).parent.parent.absolute()


class DocPipelineSettings(BaseSettings):
    """Doc_Pipeline_Service 配置"""

    # 服务
    HOST: str = "0.0.0.0"
    PORT: int = 7791

    # Elasticsearch
    ES_HOST: str = "http://elasticsearch:9200"
    RAG_INTERNAL_API_TOKEN: str = ""

    # Redis
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6378
    REDIS_USERNAME: str = "lumen"
    REDIS_PASSWORD: str = "agent123"
    REDIS_DB: int = 1

    # 并发控制
    MAX_CONCURRENT_TASKS: int = 4
    MAX_WORKERS: int = 16
    CHUNK_PROCESS_WORKERS: int = 4

    # 文件处理
    MAX_FILE_SIZE: int = 5 * 1024 * 1024 * 1024  # 5GB

    # 类属性（非环境变量）
    SUPPORTED_FORMATS: Set[str] = {
        ".pdf", ".docx", ".doc", ".txt", ".md",
        ".html", ".ppt", ".pptx", ".xls", ".xlsx", ".csv",
    }
    TEMP_DIR: str = str(_project_root / "tmp")

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = DocPipelineSettings()
