#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 文档块编辑服务类

提供文档块编辑功能的核心逻辑，被主API服务调用
"""

import logging
from typing import Any, Dict, List, Optional

from common_utils import DeepRAGCommonUtils
from embedding.chunk_embedder import ChunkEmbedder, EmbeddingConfig
from error_boundary import log_rag_failure, public_error_message

logger = logging.getLogger(__name__)

class ChunkEditService:
    """文档块编辑服务类"""
    
    def __init__(self):
        self.utils = DeepRAGCommonUtils()
    
    async def process_single_chunk_edit(self, chunk_id: str, content: Optional[str], 
                                 available_int: int, es_host: str, index_name: str,
                                 model_factory: str, model_name: str, 
                                 base_url: Optional[str] = None, api_key: Optional[str] = None,
                                 batch_size: int = 1, filename_embd_weight: float = 0.1,
                                 username: Optional[str] = None, password: Optional[str] = None,
                                 timeout: int = 60) -> Dict[str, Any]:
        """
        异步处理单个块编辑
        
        Args:
            chunk_id: 块ID
            content: 新内容（可选）
            available_int: 是否启用
            es_host: ES地址
            index_name: 索引名称
            model_factory: 模型工厂
            model_name: 模型名称
            base_url: 服务地址
            api_key: API密钥
            batch_size: 批处理大小
            filename_embd_weight: 文件名权重
            username: ES用户名
            password: ES密码
            timeout: 超时时间
            
        Returns:
            处理结果
        """
        retriever = None
        try:
            # 1. 创建检索器获取原始块数据
            retriever = self.utils.create_retriever(
                es_host=es_host,
                index_names=[index_name],
                page=1,
                page_size=1,
                timeout=timeout,
                username=username,
                password=password
            )
            
            # 2. 获取原始块数据
            original_chunk = await self.utils.fetch_chunk_by_id(retriever, chunk_id)
            if not original_chunk:
                return {
                    "success": False,
                    "message": f"未找到chunk_id为 {chunk_id} 的块",
                    "chunk_id": chunk_id
                }
            
            # 3. 检查是否提供了要更新的内容
            if not content and available_int == original_chunk.get("available_int", 1):
                return {
                    "success": False,
                    "message": "至少需要提供一个更新字段（content或available_int）",
                    "chunk_id": chunk_id
                }
            
            # 4. 验证原始块是否包含有效的chunk ID
            if not original_chunk.get("_id") and not original_chunk.get("chunk_id"):
                return {
                    "success": False,
                    "message": f"原始块缺少有效的ID字段，无法进行编辑操作。块数据: {list(original_chunk.keys())}",
                    "chunk_id": chunk_id
                }
            
            # 5. 更新块内容（自动分词处理）
            updated_chunk = self.utils.update_chunk_content(
                original_chunk, 
                content=content,
                available_int=available_int
            )
            
            # 6. 重新向量化
            config = EmbeddingConfig(
                model_factory=model_factory,
                model_name=model_name,
                api_key=api_key or "",
                base_url=base_url,
                batch_size=batch_size,
                filename_embd_weight=filename_embd_weight
            )
            
            embedder = ChunkEmbedder(config)
            token_count, vector_size = embedder.embed_chunks_sync([updated_chunk])
            
            # 7. 存储更新后的块
            store = self.utils.create_document_store(
                es_host=es_host,
                index_name=index_name,
                username=username,
                password=password,
                timeout=timeout
            )
            
            # 使用编辑模式存储，确保严格使用原有ID
            try:
                _success_count, errors = await store.store_chunks(
                    [updated_chunk], batch_size=1, is_edit_mode=True
                )
            finally:
                # 清理ES连接
                try:
                    await store.close()
                except Exception as error:
                    log_rag_failure(
                        logger,
                        stage="store_cleanup",
                        error=error,
                    )
            
            if errors:
                logger.error(
                    "RAG operation failed: stage=chunk_edit "
                    "error_type=StoreResultFailure error_count=%s",
                    len(errors),
                )
                return {
                    "success": False,
                    "message": public_error_message("chunk_edit"),
                    "chunk_id": chunk_id,
                    "errors": [],
                }
            
            # 构建返回的更新字段列表
            updated_fields = []
            if content:
                updated_fields.extend(["content_with_weight", "content_ltks", "content_sm_ltks"])
            if available_int != original_chunk.get("available_int", 1):
                updated_fields.append("available_int")
            
            return {
                "success": True,
                "message": f"成功更新块 {chunk_id}，自动重新分词和向量化",
                "chunk_id": chunk_id,
                "data": {
                    "token_count": token_count,
                    "vector_dimension": vector_size,
                    "available_int": available_int,
                    "updated_fields": updated_fields,
                    "auto_processed": True,
                    "processed_content": bool(content)
                }
            }
            
        except Exception as error:
            log_rag_failure(logger, stage="chunk_edit", error=error)
            return {
                "success": False,
                "message": public_error_message("chunk_edit"),
                "chunk_id": chunk_id,
            }
        finally:
            # 清理retriever资源
            if retriever:
                try:
                    await retriever.close()
                    logger.debug("Retriever ES连接已清理")
                except Exception as error:
                    log_rag_failure(
                        logger,
                        stage="retriever_cleanup",
                        error=error,
                    )
    
    async def process_batch_chunks_edit(self, chunks: List[Dict[str, Any]], es_host: str, 
                                 index_name: str, model_factory: str, model_name: str,
                                 base_url: Optional[str] = None, api_key: Optional[str] = None,
                                 batch_size: int = 16, filename_embd_weight: float = 0.1,
                                 username: Optional[str] = None, password: Optional[str] = None,
                                 timeout: int = 60, max_batch_size: int = 100) -> Dict[str, Any]:
        """
        异步处理批量块编辑
        
        Args:
            chunks: 要编辑的块列表
            es_host: ES地址
            index_name: 索引名称
            model_factory: 模型工厂
            model_name: 模型名称
            base_url: 服务地址
            api_key: API密钥
            batch_size: 批处理大小
            filename_embd_weight: 文件名权重
            username: ES用户名
            password: ES密码
            timeout: 超时时间
            max_batch_size: 最大批处理大小
            
        Returns:
            处理结果
        """
        retriever = None
        store = None
        try:
            total_chunks = len(chunks)
            if total_chunks > max_batch_size:
                return {
                    "success": False,
                    "message": f"批量大小超过限制 ({max_batch_size})",
                    "total_chunks": total_chunks,
                    "successful_chunks": 0,
                    "failed_chunks": total_chunks,
                    "errors": [{"error": "批量大小超过限制"}]
                }
            
            # 创建服务实例
            retriever = await self.utils.create_retriever_async(
                es_host=es_host,
                index_names=[index_name],
                timeout=timeout,
                username=username,
                password=password
            )
            
            embedding_config = EmbeddingConfig(
                model_factory=model_factory,
                model_name=model_name,
                api_key=api_key or "",
                base_url=base_url,
                batch_size=batch_size,
                filename_embd_weight=filename_embd_weight
            )
            
            embedder = ChunkEmbedder(embedding_config)
            
            store = self.utils.create_document_store(
                es_host=es_host,
                index_name=index_name,
                username=username,
                password=password,
                timeout=timeout
            )
            
            successful_chunks = 0
            failed_chunks = 0
            errors = []
            updated_chunks = []
            
            # 处理每个块
            for position, chunk_edit in enumerate(chunks):
                try:
                    chunk_id = chunk_edit.get("chunk_id")
                    if not chunk_id:
                        errors.append({
                            "position": position,
                            "error": "缺少chunk_id字段",
                        })
                        failed_chunks += 1
                        continue
                    
                    # 获取原始块
                    original_chunk = await self.utils.fetch_chunk_by_id(retriever, chunk_id)
                    if not original_chunk:
                        errors.append({
                            "position": position,
                            "error": "未找到指定文档块",
                        })
                        failed_chunks += 1
                        continue
                    
                    # 更新块内容（自动分词处理）
                    updated_chunk = self.utils.update_chunk_content(
                        original_chunk,
                        content=chunk_edit.get("content"),
                        available_int=chunk_edit.get("available_int")
                    )
                    updated_chunks.append(updated_chunk)
                    successful_chunks += 1
                    
                except Exception as error:
                    log_rag_failure(logger, stage="chunk_edit", error=error)
                    errors.append({
                        "position": position,
                        "error": public_error_message("chunk_edit"),
                    })
                    failed_chunks += 1
            
            # 批量重新向量化
            if updated_chunks:
                try:
                    _token_count, _vector_size = embedder.embed_chunks_sync(updated_chunks)
                    
                    # 批量存储（编辑模式）
                    _store_success_count, store_errors = await store.store_chunks(
                        updated_chunks, batch_size=batch_size, is_edit_mode=True
                    )
                    
                    if store_errors:
                        logger.error(
                            "RAG operation failed: stage=chunk_edit "
                            "error_type=StoreResultFailure error_count=%s",
                            len(store_errors),
                        )
                        errors.extend(
                            {"error": public_error_message("chunk_edit")}
                            for _ in store_errors
                        )
                        failed_chunks += len(store_errors)
                        successful_chunks = max(
                            0,
                            successful_chunks - len(store_errors),
                        )
                    
                except Exception as error:
                    log_rag_failure(logger, stage="chunk_edit", error=error)
                    return {
                        "success": False,
                        "message": public_error_message("chunk_edit"),
                        "total_chunks": total_chunks,
                        "successful_chunks": 0,
                        "failed_chunks": total_chunks,
                        "errors": errors + [
                            {"error": public_error_message("chunk_edit")}
                        ],
                    }
            
            return {
                "success": successful_chunks > 0,
                "message": f"批量编辑完成: 成功 {successful_chunks} 个，失败 {failed_chunks} 个",
                "total_chunks": total_chunks,
                "successful_chunks": successful_chunks,
                "failed_chunks": failed_chunks,
                "errors": errors[:10]  # 只返回前10个错误
            }
            
        except Exception as error:
            log_rag_failure(logger, stage="chunk_edit", error=error)
            return {
                "success": False,
                "message": public_error_message("chunk_edit"),
                "total_chunks": len(chunks),
                "successful_chunks": 0,
                "failed_chunks": len(chunks),
                "errors": [{"error": public_error_message("chunk_edit")}],
            }
        finally:
            if store:
                try:
                    await store.close()
                except Exception as error:
                    log_rag_failure(
                        logger,
                        stage="store_cleanup",
                        error=error,
                    )
            # 清理retriever资源
            if retriever:
                try:
                    await retriever.close()
                    logger.debug("批量编辑 - Retriever ES连接已清理")
                except Exception as error:
                    log_rag_failure(
                        logger,
                        stage="retriever_cleanup",
                        error=error,
                    )
