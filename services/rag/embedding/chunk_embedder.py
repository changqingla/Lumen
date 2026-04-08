# -*- coding: utf-8 -*-
"""
DeepRAG 文档分块嵌入器

主要功能：
1. 支持多种嵌入模型（OpenAI、通义千问、智谱AI、LocalAI等）
2. 批量处理文档分块，提高处理效率

"""

import sys
import logging
import re
from typing import List, Dict, Any, Tuple
from pathlib import Path
import numpy as np
# from timeit import default_timer as timer

# 添加 DeepRAG 根目录到 Python 路径
current_dir = Path(__file__).parent.absolute()
DeepRAG_root = current_dir.parent
sys.path.insert(0, str(DeepRAG_root))

from core.llm import EmbeddingModel


class EmbeddingConfig:
    """
    嵌入模型配置类（替代基于租户的配置）

    管理嵌入模型的所有配置参数，支持多种模型类型和自定义设置。
    """

    def __init__(self,
                 model_factory: str = "BAAI",
                 model_name: str = "BAAI/bge-large-zh-v1.5",
                 api_key: str = "",
                 base_url: str = "",
                 max_tokens: int = 8192,
                 filename_embd_weight: float = 0.1,
                 batch_size: int = 16,
                 **kwargs):
        """
        初始化嵌入配置

        Args:
            model_factory (str): 模型工厂名称（如 "BAAI", "OpenAI", "VLLM"）
            model_name (str): 具体的模型名称
            api_key (str): 模型的 API 密钥（如果需要）
            base_url (str): 模型 API 的基础 URL（如果需要）
            max_tokens (int): 模型的最大 token 数量
            filename_embd_weight (float): 文件名嵌入在最终向量中的权重
            batch_size (int): 批处理大小
            **kwargs: 其他模型特定参数
        """
        self.model_factory = model_factory
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.filename_embd_weight = filename_embd_weight
        self.batch_size = batch_size
        self.kwargs = kwargs



class IndependentEmbeddingModel:
    """
    独立嵌入模型包装器（替代 LLMBundle 用于嵌入）

    这个类封装了嵌入模型的初始化和调用逻辑，支持多种模型类型。
    """

    def __init__(self, config: EmbeddingConfig):
        """
        初始化独立嵌入模型

        Args:
            config (EmbeddingConfig): 嵌入配置对象
        """
        self.config = config
        self.model = None
        self._initialize_model()
    
    def _initialize_model(self):
        """
        根据配置初始化嵌入模型

        使用原始的 EmbeddingModel 字典来获取模型类。
        """
        try:
            if self.config.model_factory not in EmbeddingModel:
                available_factories = list(EmbeddingModel.keys())
                raise ValueError(f"不支持的嵌入模型工厂: {self.config.model_factory}。可用工厂: {available_factories}")

            model_class = EmbeddingModel[self.config.model_factory]

            # 根据模型类型准备初始化参数
            if self.config.model_factory in ["LocalAI", "VLLM", "openai", "LM-Studio", "GPUStack"]:
                # 这些模型需要特定的参数顺序: key, model_name, base_url
                if not self.config.base_url:
                    raise ValueError(f"{self.config.model_factory} 嵌入模型需要 base_url 参数")

                self.model = model_class(
                    self.config.api_key or "empty",  # key 作为位置参数
                    self.config.model_name,          # model_name 作为位置参数
                    self.config.base_url             # base_url 作为位置参数
                )
            else:
                # 其他模型的标准初始化
                init_params = {
                    "model_name": self.config.model_name,
                    **self.config.kwargs
                }

                # 添加 API 密钥（某些模型如 BAAI 需要 key 参数，即使为空）
                init_params["key"] = self.config.api_key or ""

                # 如果提供了基础 URL，则添加
                if self.config.base_url:
                    init_params["base_url"] = self.config.base_url

                # 初始化模型
                self.model = model_class(**init_params)

            logging.info(f"嵌入模型初始化成功: {self.config.model_factory}/{self.config.model_name}")

        except Exception as e:
            logging.error(f"嵌入模型初始化失败: {e}")
            raise
    
    def encode(self, texts: List[str]) -> Tuple[np.ndarray, int]:
        """
        将文本编码为嵌入向量

        Args:
            texts (List[str]): 要编码的文本列表

        Returns:
            Tuple[np.ndarray, int]: (嵌入向量数组, token 数量)
        """
        if not self.model:
            raise RuntimeError("嵌入模型未初始化")

        try:
            embeddings, token_count = self.model.encode(texts)
            return embeddings, token_count
        except Exception as e:
            logging.error(f"文本编码失败: {e}")
            raise

    def encode_queries(self, query: str) -> Tuple[np.ndarray, int]:
        """
        将查询文本编码为嵌入向量

        Args:
            query (str): 要编码的查询文本

        Returns:
            Tuple[np.ndarray, int]: (嵌入向量数组, token 数量)
        """
        if not self.model:
            raise RuntimeError("嵌入模型未初始化")

        try:
            embedding, token_count = self.model.encode_queries(query)
            return embedding, token_count
        except Exception as e:
            logging.error(f"查询编码失败: {e}")
            raise


class ChunkEmbedder:
    """
    基于 DeepRAG 的分块嵌入器

    支持批量处理和多种嵌入模型。
    """

    def __init__(self, config: EmbeddingConfig):
        """
        初始化分块嵌入器

        Args:
            config (EmbeddingConfig): 嵌入配置，必须提供

        Raises:
            ValueError: 当 config 为 None 时抛出异常
        """
        if config is None:
            raise ValueError(
                "必须提供嵌入配置！请创建 EmbeddingConfig 实例并指定：\n"
                "- model_factory: 模型工厂名称\n"
                "- model_name: 模型名称\n"
                "- 其他必要参数（api_key, base_url 等）\n\n"
                "示例：\n"
                "config = EmbeddingConfig(\n"
                "    model_factory='VLLM',\n"
                "    model_name='bge-m3',\n"
                "    base_url='http://localhost:8000/v1'\n"
                ")\n"
                "embedder = ChunkEmbedder(config)"
            )

        self.config = config
        self.embedding_model = IndependentEmbeddingModel(self.config)
        self.vector_size = None

        # 初始化向量大小
        self._initialize_vector_size()

        logging.info(f"分块嵌入器初始化完成，向量维度: {self.vector_size}")
    
    def _initialize_vector_size(self):
        """
        通过编码测试字符串初始化向量大小

        使用一个简单的测试字符串来确定模型输出的向量维度。
        """
        try:
            test_embeddings, _ = self.embedding_model.encode(["test"])
            self.vector_size = len(test_embeddings[0])
        except Exception as e:
            logging.error(f"向量大小初始化失败: {e}")
            raise

    def _progress_callback(self, progress: float = None, msg: str = ""):
        """
        进度回调函数

        Args:
            progress (float): 进度百分比（0-1）
            msg (str): 状态消息
        """
        if progress is not None:
            logging.info(f"嵌入进度: {progress:.1%} - {msg}")
        else:
            logging.info(f"嵌入状态: {msg}")
    
    async def embed_chunks(self,
                          chunks: List[Dict[str, Any]],
                          parser_config: Dict[str, Any] = None,
                          callback=None) -> Tuple[int, int]:
        """
        嵌入文档分块

        Args:
            chunks (List[Dict[str, Any]]): 要嵌入的文档分块列表
            parser_config (Dict[str, Any]): 解析器配置（可选）
            callback: 进度回调函数（可选）

        Returns:
            Tuple[int, int]: (token 数量, 向量维度)
        """
        if parser_config is None:
            parser_config = {}
        
        if callback is None:
            callback = self._progress_callback
        
        # 直接调用embed_chunks_sync方法，避免重复代码
        return self.embed_chunks_sync(chunks, parser_config, callback)
    
    def embed_chunks_sync(self,
                         chunks: List[Dict[str, Any]],
                         parser_config: Dict[str, Any] = None,
                         callback=None) -> Tuple[int, int]:
        """
        embed_chunks 的同步版本

        Args:
            chunks (List[Dict[str, Any]]): 要嵌入的文档分块列表
            parser_config (Dict[str, Any]): 解析器配置（可选）
            callback: 进度回调函数（可选）

        Returns:
            Tuple[int, int]: (token 数量, 向量维度)
        """
        # Call the embed_chunks method directly (it's now synchronous)
        if parser_config is None:
            parser_config = {}

        if callback is None:
            callback = self._progress_callback

        batch_size = self.config.batch_size

        # Extract titles and contents (same logic as DeepRAG)
        titles, contents = [], []
        for doc in chunks:
            titles.append(doc.get("docnm_kwd", "Title"))

            # Use question keywords if available, otherwise use content
            content = "\n".join(doc.get("question_kwd", []))
            if not content:
                content = doc["content_with_weight"]

            content = re.sub(r"</?(table|td|caption|tr|th)( [^<>]{0,12})?>", " ", content)
            if not content:
                content = "None"
            contents.append(content)

        token_count = 0

        # Encode titles (修复：为每个chunk单独编码title，与CLI保持一致)
        if len(titles) == len(contents):
            title_embeddings, tc = self.embedding_model.encode(titles)
            token_count += tc

        # Encode contents in batches 
        content_embeddings = np.array([])
        for i in range(0, len(contents), batch_size):
            batch_contents = contents[i:i + batch_size]
            embeddings, tc = self.embedding_model.encode(batch_contents)

            if len(content_embeddings) == 0:
                content_embeddings = embeddings
            else:
                content_embeddings = np.concatenate((content_embeddings, embeddings), axis=0)

            token_count += tc
            callback(progress=0.7 + 0.2 * (i + 1) / len(contents), msg="")

        # Combine title and content embeddings 
        title_weight = float(parser_config.get("filename_embd_weight", self.config.filename_embd_weight))

        if len(titles) == len(contents):
            final_embeddings = (title_weight * title_embeddings +
                              (1 - title_weight) * content_embeddings)
        else:
            final_embeddings = content_embeddings

        # Add embeddings to chunks 
        assert len(final_embeddings) == len(chunks)
        vector_size = 0

        for i, chunk in enumerate(chunks):
            embedding_vector = final_embeddings[i].tolist()
            vector_size = len(embedding_vector)
            chunk[f"q_{len(embedding_vector)}_vec"] = embedding_vector

        return token_count, vector_size
    
    def embed_single_chunk(self, chunk: Dict[str, Any]) -> Dict[str, Any]:
        """
        嵌入单个分块

        Args:
            chunk (Dict[str, Any]): 要嵌入的单个文档分块

        Returns:
            Dict[str, Any]: 添加了嵌入向量的分块
        """
        chunks = [chunk]
        token_count, vector_size = self.embed_chunks_sync(chunks)
        return chunks[0]

    def get_vector_size(self) -> int:
        """
        获取嵌入模型的向量大小

        Returns:
            int: 向量维度
        """
        return self.vector_size

    def get_embedding_field_name(self) -> str:
        """
        获取分块中嵌入向量的字段名

        Returns:
            str: 嵌入向量字段名（格式：q_{维度}_vec）
        """
        return f"q_{self.vector_size}_vec"
