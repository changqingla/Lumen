#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 文档块编辑服务类

提供文档块编辑功能的核心逻辑，被主API服务调用
"""

import logging
from typing import Dict, List, Optional, Any
from embedding.chunk_embedder import ChunkEmbedder, EmbeddingConfig
from common_utils import DeepRAGCommonUtils

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
                success_count, errors = await store.store_chunks(
                    [updated_chunk], batch_size=1, is_edit_mode=True
                )
            finally:
                # 清理ES连接
                try:
                    await store.close()
                except Exception as e:
                    logger.warning(f"关闭ES连接时出错: {e}")
            
            if errors:
                return {
                    "success": False,
                    "message": f"存储更新后的块失败: {errors[0]}",
                    "chunk_id": chunk_id,
                    "errors": errors
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
            
        except Exception as e:
            logger.error(f"处理块编辑失败: {e}")
            return {
                "success": False,
                "message": f"处理块编辑失败: {str(e)}",
                "chunk_id": chunk_id
            }
        finally:
            # 清理retriever资源
            if retriever:
                try:
                    await retriever.close()
                    logger.debug("Retriever ES连接已清理")
                except Exception as e:
                    logger.warning(f"关闭Retriever ES连接时出错: {e}")
    
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
            for chunk_edit in chunks:
                try:
                    chunk_id = chunk_edit.get("chunk_id")
                    if not chunk_id:
                        errors.append({
                            "chunk_data": chunk_edit,
                            "error": "缺少chunk_id字段"
                        })
                        failed_chunks += 1
                        continue
                    
                    # 获取原始块
                    original_chunk = await self.utils.fetch_chunk_by_id(retriever, chunk_id)
                    if not original_chunk:
                        errors.append({
                            "chunk_id": chunk_id,
                            "error": f"未找到chunk_id为 {chunk_id} 的块"
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
                    
                except Exception as e:
                    errors.append({
                        "chunk_data": chunk_edit,
                        "error": str(e)
                    })
                    failed_chunks += 1
            
            # 批量重新向量化
            if updated_chunks:
                try:
                    token_count, vector_size = embedder.embed_chunks_sync(updated_chunks)
                    
                    # 批量存储（编辑模式）
                    try:
                        store_success_count, store_errors = await store.store_chunks(
                            updated_chunks, batch_size=batch_size, is_edit_mode=True
                        )
                    finally:
                        # 清理ES连接
                        try:
                            await store.close()
                        except Exception as e:
                            logger.warning(f"关闭ES连接时出错: {e}")
                    
                    if store_errors:
                        errors.extend([{"error": err} for err in store_errors])
                        failed_chunks += len(store_errors)
                        successful_chunks -= len(store_errors)
                    
                except Exception as e:
                    logger.error(f"批量向量化或存储失败: {e}")
                    return {
                        "success": False,
                        "message": f"批量处理失败: {str(e)}",
                        "total_chunks": total_chunks,
                        "successful_chunks": 0,
                        "failed_chunks": total_chunks,
                        "errors": errors + [{"error": str(e)}]
                    }
            
            return {
                "success": successful_chunks > 0,
                "message": f"批量编辑完成: 成功 {successful_chunks} 个，失败 {failed_chunks} 个",
                "total_chunks": total_chunks,
                "successful_chunks": successful_chunks,
                "failed_chunks": failed_chunks,
                "errors": errors[:10]  # 只返回前10个错误
            }
            
        except Exception as e:
            logger.error(f"批量编辑处理失败: {e}")
            return {
                "success": False,
                "message": f"批量编辑处理失败: {str(e)}",
                "total_chunks": len(chunks),
                "successful_chunks": 0,
                "failed_chunks": len(chunks),
                "errors": [{"error": str(e)}]
            }
        finally:
            # 清理retriever资源
            if retriever:
                try:
                    await retriever.close()
                    logger.debug("批量编辑 - Retriever ES连接已清理")
                except Exception as e:
                    logger.warning(f"关闭批量编辑Retriever ES连接时出错: {e}")