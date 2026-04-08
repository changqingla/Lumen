#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG API 共用工具模块

提供各个API服务共用的工具函数，避免代码重复
"""

import logging
from typing import Optional, Dict, Any, List
from embedding.chunk_embedder import ChunkEmbedder, EmbeddingConfig
from embed_store.chunk_store import DocumentStore
from recall.retriever import DeepRagPureRetriever, DeepRagRetrievalConfig
from core.llm import EmbeddingModel, RerankModel
from core.nlp import rag_tokenizer
import re
import time

logger = logging.getLogger(__name__)

class DeepRAGCommonUtils:
    """DeepRAG 通用工具类"""
    
    @staticmethod
    def create_embedding_model(model_factory: str, model_name: str, 
                              base_url: Optional[str] = None, api_key: Optional[str] = None):
        """创建向量化模型"""
        # 打印用户传入的 API key（带掩码保护）
        if api_key:
            masked_key = f"{api_key[:8]}...{api_key[-8:]}" if len(api_key) > 16 else "***"
            logger.info(f"[create_embedding_model] 用户传入的 api_key: {masked_key}")
        else:
            logger.info(f"[create_embedding_model] 用户传入的 api_key: None")
        
        if model_factory not in EmbeddingModel:
            available_factories = list(EmbeddingModel.keys())
            raise ValueError(f"不支持的嵌入模型工厂: {model_factory}. 可用工厂: {available_factories}")

        model_class = EmbeddingModel[model_factory]

        # SILICONFLOW, NovitaAI, GiteeAI 只需要 api_key 和 model_name，使用内置默认 URL
        if model_factory in ["SILICONFLOW", "NovitaAI", "GiteeAI"]:
            return model_class(api_key or "empty", model_name)
        
        # 其他需要 base_url 的模型
        if model_factory in ["LocalAI", "VLLM", "openai", "LM-Studio", "GPUStack"]:
            if not base_url:
                raise ValueError(f"{model_factory} 嵌入模型需要 base_url 参数")
            return model_class(api_key or "empty", model_name, base_url)
        elif model_factory == "HuggingFace":
            return model_class(api_key or "empty", model_name)
        elif model_factory == "OpenAI":
            if not api_key:
                raise ValueError("OpenAI 模型需要 API 密钥")
            return model_class(api_key, model_name)
        else:
            return model_class(api_key or "empty", model_name)
    
    @staticmethod
    def create_rerank_model(rerank_factory: str, rerank_model_name: str,
                           rerank_base_url: Optional[str] = None, rerank_api_key: Optional[str] = None):
        """创建重排序模型"""
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
    
    @staticmethod
    def create_retriever(es_host: str, index_names: List[str], page: int = 1, 
                        page_size: int = 10, similarity_threshold: float = 0.2,
                        vector_similarity_weight: float = 0.3, highlight: bool = True,
                        timeout: int = 60, username: Optional[str] = None, 
                        password: Optional[str] = None) -> DeepRagPureRetriever:
        """创建检索器实例（同步版本，实际连接延迟到首次使用）"""
        es_config = {
            "hosts": es_host,
            "timeout": timeout
        }
        if username and password:
            es_config.update({
                "username": username,
                "password": password
            })
            
        config = DeepRagRetrievalConfig(
            index_names=index_names,
            page=page,
            page_size=page_size,
            similarity_threshold=similarity_threshold,
            vector_similarity_weight=vector_similarity_weight,
            highlight=highlight,
            es_config=es_config
        )
        return DeepRagPureRetriever(config)
    
    @staticmethod
    async def create_retriever_async(es_host: str, index_names: List[str], page: int = 1, 
                        page_size: int = 10, similarity_threshold: float = 0.2,
                        vector_similarity_weight: float = 0.3, highlight: bool = True,
                        timeout: int = 60, username: Optional[str] = None, 
                        password: Optional[str] = None) -> DeepRagPureRetriever:
        """异步创建检索器实例并立即建立连接"""
        es_config = {
            "hosts": es_host,
            "timeout": timeout
        }
        if username and password:
            es_config.update({
                "username": username,
                "password": password
            })
            
        config = DeepRagRetrievalConfig(
            index_names=index_names,
            page=page,
            page_size=page_size,
            similarity_threshold=similarity_threshold,
            vector_similarity_weight=vector_similarity_weight,
            highlight=highlight,
            es_config=es_config
        )
        retriever = DeepRagPureRetriever(config)
        # 立即建立连接
        await retriever.ensure_connected()
        return retriever
    
    @staticmethod
    def create_document_store(es_host: str, index_name: str, 
                             username: Optional[str] = None, password: Optional[str] = None,
                             timeout: int = 60) -> DocumentStore:
        """创建文档存储器"""
        es_kwargs = {}
        if username and password:
            es_kwargs.update({
                "username": username,
                "password": password
            })
        if timeout:
            es_kwargs["timeout"] = timeout
            
        return DocumentStore(
            es_host=es_host,
            index_name=index_name,
            **es_kwargs
        )
    
    @staticmethod
    def process_content_with_tokenization(content: str, title: Optional[str] = None) -> Dict[str, str]:
        """
        对原始内容进行分词处理，生成所有需要的字段
        
        Args:
            content: 原始文档内容
            title: 原始标题（可选）
            
        Returns:
            包含所有分词字段的字典
        """
        result = {}
        
        # 处理内容
        if content:
            # 使用和原系统相同的逻辑处理内容
            result["content_with_weight"] = content
            
            # 清理HTML表格标签（与原系统保持一致）
            cleaned_content = re.sub(r"</?(table|td|caption|tr|th)( [^<>]{0,12})?>", " ", content)
            
            # 分词处理
            result["content_ltks"] = rag_tokenizer.tokenize(cleaned_content)
            result["content_sm_ltks"] = rag_tokenizer.fine_grained_tokenize(result["content_ltks"])
        
        # 处理标题
        if title:
            # 清理文件扩展名（如果有）
            cleaned_title = re.sub(r"\.[a-zA-Z]+$", "", title)
            result["title_tks"] = rag_tokenizer.tokenize(cleaned_title)
            result["title_sm_tks"] = rag_tokenizer.fine_grained_tokenize(result["title_tks"])
        
        return result
    
    @staticmethod
    async def fetch_chunk_by_id(retriever: DeepRagPureRetriever, chunk_id: str) -> Optional[Dict[str, Any]]:
        """异步根据chunk_id获取文档块"""
        try:
            # 确保ES已连接
            await retriever.ensure_connected()
            es_adapter = retriever.es_conn
            
            # 直接使用ES查询获取指定ID的文档
            query = {
                "query": {"term": {"_id": chunk_id}},
                "_source": True,
                "size": 1
            }
            
            result = await es_adapter.es_conn.search(
                index_name=retriever.config.index_names[0],
                query=query,
                size=1
            )
            
            if result["hits"]["total"]["value"] > 0:
                hit = result["hits"]["hits"][0]
                chunk_data = hit["_source"]
                chunk_data["_id"] = hit["_id"]
                # 确保chunk_id字段存在且与ES文档ID一致
                chunk_data["chunk_id"] = hit["_id"]
                return chunk_data
            else:
                return None
                
        except Exception as e:
            logger.error(f"获取块 {chunk_id} 失败: {e}")
            return None
    
    @staticmethod
    def update_chunk_content(original_chunk: Dict[str, Any], 
                           content: Optional[str] = None, 
                           available_int: Optional[int] = None) -> Dict[str, Any]:
        """
        更新块内容，自动处理分词
        
        Args:
            original_chunk: 原始块数据
            content: 新的内容（原始内容，会自动分词）
            available_int: 是否启用
            
        Returns:
            更新后的块数据
            
        Raises:
            ValueError: 当无法获取原有chunk ID时
        """
        # 复制原始块数据
        updated_chunk = original_chunk.copy()
        
        # 严格验证：编辑操作必须有原有的chunk ID
        original_chunk_id = None
        if "_id" in original_chunk and original_chunk["_id"]:
            original_chunk_id = original_chunk["_id"]
        elif "chunk_id" in original_chunk and original_chunk["chunk_id"]:
            original_chunk_id = original_chunk["chunk_id"]
        
        if not original_chunk_id:
            error_msg = f"编辑操作失败：无法获取原有chunk ID。原始块数据包含的ID字段: {[k for k in original_chunk.keys() if 'id' in k.lower()]}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # 确保保持原有的chunk_id不变
        updated_chunk["chunk_id"] = original_chunk_id
        updated_chunk["_id"] = original_chunk_id
        
        logger.info(f"编辑块 {original_chunk_id}：保持原有ID不变")
        
        # 自动分词处理 - 只处理明确提供的字段
        if content is not None:
            # 处理内容相关字段
            content_fields = DeepRAGCommonUtils.process_content_with_tokenization(content, None)
            # 只更新内容相关的字段
            for key in ["content_with_weight", "content_ltks", "content_sm_ltks"]:
                if key in content_fields:
                    updated_chunk[key] = content_fields[key]
        
        # 更新available_int字段
        if available_int is not None:
            updated_chunk["available_int"] = available_int
        
        # 更新时间戳
        updated_chunk["create_timestamp_flt"] = time.time()
        
        return updated_chunk