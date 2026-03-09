#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模型工厂函数

从 rag/api/common_utils.py 的 DeepRAGCommonUtils 中抽取的
create_embedding_model 和 create_rerank_model 函数。
依赖 rag.llm.EmbeddingModel 和 rag.llm.RerankModel 字典。
"""

import sys
import logging
from pathlib import Path
from typing import Any, Optional

# 添加DeepRAG根目录到路径，以解析 rag.llm 导入
current_dir = Path(__file__).parent.absolute()
deeprag_root = current_dir.parent / "rag"
sys.path.insert(0, str(deeprag_root))

from rag.llm import EmbeddingModel, RerankModel

logger = logging.getLogger(__name__)


def create_embedding_model(model_factory: str, model_name: str,
                           model_base_url: str = None, api_key: str = None) -> Any:
    """创建向量化模型

    Args:
        model_factory: 模型工厂名称（如 "Tongyi-Qianwen"、"OpenAI" 等）
        model_name: 模型名称
        model_base_url: 模型服务 base URL（部分工厂需要）
        api_key: API 密钥（部分工厂需要）

    Returns:
        对应工厂的 embedding 模型实例

    Raises:
        ValueError: 不支持的模型工厂或缺少必要参数
    """
    # 打印用户传入的 API key（带掩码保护）
    if api_key:
        masked_key = f"{api_key[:8]}...{api_key[-8:]}" if len(api_key) > 16 else "***"
        logger.info(f"[create_embedding_model] 用户传入的 api_key: {masked_key}")
    else:
        logger.info("[create_embedding_model] 用户传入的 api_key: None")

    if model_factory not in EmbeddingModel:
        available_factories = list(EmbeddingModel.keys())
        raise ValueError(f"不支持的嵌入模型工厂: {model_factory}. 可用工厂: {available_factories}")

    model_class = EmbeddingModel[model_factory]

    # SILICONFLOW, NovitaAI, GiteeAI 只需要 api_key 和 model_name，使用内置默认 URL
    if model_factory in ["SILICONFLOW", "NovitaAI", "GiteeAI"]:
        return model_class(api_key or "empty", model_name)

    # 其他需要 base_url 的模型
    if model_factory in ["LocalAI", "VLLM", "openai", "LM-Studio", "GPUStack"]:
        if not model_base_url:
            raise ValueError(f"{model_factory} 嵌入模型需要 base_url 参数")
        return model_class(api_key or "empty", model_name, model_base_url)
    elif model_factory == "HuggingFace":
        return model_class(api_key or "empty", model_name)
    elif model_factory == "OpenAI":
        if not api_key:
            raise ValueError("OpenAI 模型需要 API 密钥")
        return model_class(api_key, model_name)
    else:
        return model_class(api_key or "empty", model_name)


def create_rerank_model(rerank_factory: str, rerank_model_name: str,
                        rerank_base_url: str = None, rerank_api_key: str = None) -> Any:
    """创建重排序模型

    Args:
        rerank_factory: 重排序模型工厂名称
        rerank_model_name: 重排序模型名称
        rerank_base_url: 重排序模型服务 base URL（部分工厂需要）
        rerank_api_key: API 密钥

    Returns:
        对应工厂的 rerank 模型实例

    Raises:
        ValueError: 不支持的模型工厂或缺少必要参数
    """
    if rerank_factory not in RerankModel:
        available_factories = list(RerankModel.keys())
        raise ValueError(f"不支持的重排序模型工厂: {rerank_factory}. 可用工厂: {available_factories}")

    rerank_class = RerankModel[rerank_factory]

    # 准备参数
    key = rerank_api_key or "empty"
    model_name = rerank_model_name or ""
    base_url = rerank_base_url

    # 根据模型类型准备初始化参数
    if rerank_factory in ["LocalAI", "VLLM", "openai", "LM-Studio", "GPUStack"]:
        if not base_url:
            raise ValueError(f"{rerank_factory} 重排序模型需要 base_url 参数")
        return rerank_class(key, model_name, base_url)
    else:
        # 其他模型的标准初始化
        init_params = {"key": key, "model_name": model_name}
        if base_url:
            init_params["base_url"] = base_url
        return rerank_class(**init_params)
