#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG API 共用工具模块

提供各个API服务共用的工具函数，避免代码重复
"""

import logging
from typing import Optional, Dict, Any, List
from embed_store.chunk_store import DocumentStore
from recall_lib import DeepRagPureRetriever, DeepRagRetrievalConfig
from core.nlp import rag_tokenizer
from error_boundary import log_rag_failure
import re
import time

logger = logging.getLogger(__name__)

class DeepRAGCommonUtils:
    """DeepRAG 通用工具类"""

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
                
        except Exception as error:
            log_rag_failure(logger, stage="chunk_edit", error=error)
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
            raise ValueError("编辑操作缺少有效的文档块标识")
        
        # 确保保持原有的chunk_id不变
        updated_chunk["chunk_id"] = original_chunk_id
        updated_chunk["_id"] = original_chunk_id
        
        logger.debug("编辑文档块时保留原有标识")
        
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
