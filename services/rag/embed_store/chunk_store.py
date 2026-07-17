#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简化的文档分块存储模块

专注于将解析后的文档分块存储到Elasticsearch
"""

import logging
import uuid
import base64
import io
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from recall_lib import SimpleESConnection
from recall_lib._logging import log_operation_failure

logger = logging.getLogger('embed_store.chunk_store')


class DocumentStore:
    """
    简化的文档存储器
    专注于将解析后的文档分块存储到ES
    """

    def __init__(self,
                 es_host: str = "http://localhost:9200",
                 index_name: str = "test-test",
                 **es_kwargs):
        """
        初始化文档存储器

        Args:
            es_host: Elasticsearch地址
            index_name: 索引名称
            **es_kwargs: ES连接参数
        """
        self.index_name = index_name
        self.es_conn = SimpleESConnection(es_host, **es_kwargs)
        self.vector_dim = None

    def _detect_vector_dimension(self, chunks: List[Dict[str, Any]]) -> int:
        """
        从分块数据中检测向量维度

        Args:
            chunks: 分块数据列表

        Returns:
            int: 向量维度
        """
        for chunk in chunks:
            for key, value in chunk.items():
                if key.startswith("q_") and key.endswith("_vec"):
                    if isinstance(value, list) and len(value) > 0:
                        return len(value)

        raise ValueError("未找到向量字段或向量为空")

    def _normalize_chunk(self, chunk: Dict[str, Any], chunk_index: int, is_edit_mode: bool = False) -> Dict[str, Any]:
        """
        标准化分块数据格式 - 基于实际数据结构，保留所有原始字段并添加必要的DeepRAG兼容字段

        Args:
            chunk: 原始分块数据
            chunk_index: 分块索引
            is_edit_mode: 是否为编辑模式（编辑模式下严格要求原有ID存在）

        Returns:
            Dict: 标准化后的分块数据
            
        Raises:
            ValueError: 编辑模式下无法获取原有chunk ID时
        """
        # 第一步：处理PIL.Image对象，转换为可序列化的格式
        normalized = chunk.copy()

        # 处理image字段中的PIL.Image对象
        if "image" in normalized and normalized["image"] is not None:
            image_obj = normalized["image"]
            if hasattr(image_obj, 'mode') and hasattr(image_obj, 'size'):  # 检查是否为PIL.Image对象
                try:
                    # 将PIL.Image转换为base64字符串
                    buffer = io.BytesIO()
                    # 如果图像模式是RGBA，转换为RGB以支持JPEG格式
                    if image_obj.mode == 'RGBA':
                        image_obj = image_obj.convert('RGB')
                    image_obj.save(buffer, format='JPEG', quality=85)
                    image_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                    normalized["image"] = image_base64
                    normalized["image_info"] = {
                        "format": "JPEG",
                        "mode": image_obj.mode,
                        "size": image_obj.size,
                        "encoding": "base64"
                    }
                    logger.debug(f"PIL.Image转换为base64成功，尺寸: {image_obj.size}")
                except Exception as error:
                    log_operation_failure(
                        logger,
                        "Chunk image conversion",
                        error,
                        level=logging.WARNING,
                    )
                    normalized["image"] = None
                    normalized["image_info"] = {"error": "Image conversion failed"}

        # 检查其他可能包含PIL.Image对象的字段
        for key, value in list(normalized.items()):
            if value is not None and hasattr(value, 'mode') and hasattr(value, 'size'):
                try:
                    buffer = io.BytesIO()
                    if value.mode == 'RGBA':
                        value = value.convert('RGB')
                    value.save(buffer, format='JPEG', quality=85)
                    image_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                    normalized[key] = image_base64
                    logger.debug(f"字段 {key} 中的PIL.Image转换为base64成功")
                except Exception as error:
                    log_operation_failure(
                        logger,
                        "Chunk image field conversion",
                        error,
                        level=logging.WARNING,
                    )
                    normalized[key] = None

        # 第二步：添加必要的标识字段
        chunk_id = None
        if "chunk_id" in chunk and chunk["chunk_id"]:
            chunk_id = chunk["chunk_id"]
        elif "_id" in chunk and chunk["_id"]:
            chunk_id = chunk["_id"]
        
        if is_edit_mode:
            # 编辑模式：必须有原有的chunk ID，否则报错
            if not chunk_id:
                error_msg = f"编辑模式下无法获取原有chunk ID。块数据包含的ID字段: {[k for k in chunk.keys() if 'id' in k.lower()]}"
                logger.error(error_msg)
                raise ValueError(error_msg)
            normalized["id"] = chunk_id
            normalized["chunk_id"] = chunk_id
            logger.info("编辑模式：复用已有 chunk ID")
        else:
            # 新建模式：优先使用已有ID，否则生成新ID
            if chunk_id:
                normalized["id"] = chunk_id
                if "chunk_id" not in normalized:
                    normalized["chunk_id"] = chunk_id
            else:
                new_id = str(uuid.uuid4())
                normalized["id"] = new_id
                normalized["chunk_id"] = new_id
                logger.debug("新建模式：已生成 chunk ID")
        
        # 优先使用输入数据中的document_id，如果没有则使用文档名
        if "document_id" in chunk and chunk["document_id"]:
            normalized["doc_id"] = chunk["document_id"]
            #logger.info(f"使用document_id作为doc_id: {chunk['document_id']}")
        else:
            normalized["doc_id"] = chunk.get("docnm_kwd", "unknown")  # 使用文档名作为doc_id
            #logger.info(f"使用docnm_kwd作为doc_id: {normalized['doc_id']}, document_id存在: {'document_id' in chunk}, document_id值: {chunk.get('document_id', 'None')}")
        
        normalized["chunk_index"] = chunk_index

        # 第三步：添加DeepRAG系统需要但数据中不存在的字段（使用默认值）
        # 这些字段在检索中可能被用到，提供默认值确保兼容性

        # 重要字段（如果不存在则设为空）
        if "important_kwd" not in normalized:
            normalized["important_kwd"] = []
        if "important_tks" not in normalized:
            normalized["important_tks"] = ""
        if "question_tks" not in normalized:
            normalized["question_tks"] = ""
        if "question_kwd" not in normalized:
            normalized["question_kwd"] = []

        # 状态字段
        if "available_int" not in normalized:
            normalized["available_int"] = 1  # 默认可用

        # 时间字段
        if "create_timestamp_flt" not in normalized:
            normalized["create_timestamp_flt"] = datetime.now().timestamp()
        if "create_time" not in normalized:
            normalized["create_time"] = datetime.now().isoformat()

        # 其他可选字段
        if "img_id" not in normalized:
            normalized["img_id"] = ""
        if "knowledge_graph_kwd" not in normalized:
            normalized["knowledge_graph_kwd"] = []

        # 第四步：确保数据类型正确
        # 确保关键词字段是列表格式
        for field in ["important_kwd", "question_kwd", "knowledge_graph_kwd"]:
            if isinstance(normalized.get(field), str):
                normalized[field] = [normalized[field]] if normalized[field] else []

        return normalized


    async def ensure_vector_field(self, vector_dim: int) -> bool:
        """
        异步确保索引中存在指定维度的向量字段
        
        这是推荐的方法，会自动处理：
        1. 索引不存在时创建索引
        2. 索引存在但缺少向量字段时添加字段
        3. 向量字段已存在时直接返回成功
        
        Args:
            vector_dim: 向量维度
            
        Returns:
            bool: 是否成功确保字段存在
        """
        return await self.es_conn.ensure_vector_field(self.index_name, vector_dim)

    async def store_chunks(self,
                    chunks: List[Dict[str, Any]],
                    batch_size: int = 100,
                    progress_callback: Optional[callable] = None,
                    is_edit_mode: bool = False) -> Tuple[int, List[str]]:
        """
        异步存储文档分块

        Args:
            chunks: 分块数据列表
            batch_size: 批量大小
            progress_callback: 进度回调函数
            is_edit_mode: 是否为编辑模式

        Returns:
            Tuple[int, List[str]]: (成功数量, 错误列表)
        """
        if not chunks:
            raise ValueError("没有要存储的分块数据")

        # 检测向量维度
        self.vector_dim = self._detect_vector_dimension(chunks)
        logger.info(f"检测到向量维度: {self.vector_dim}")

        # 异步确保向量字段存在（自动处理索引创建或字段添加）
        if not await self.ensure_vector_field(self.vector_dim):
            raise RuntimeError("Elasticsearch vector field reconciliation failed")

        # 标准化分块数据
        if progress_callback:
            progress_callback(0.1, "正在标准化分块数据...")

        normalized_chunks = []
        for i, chunk in enumerate(chunks):
            try:
                normalized = self._normalize_chunk(chunk, i, is_edit_mode)
                normalized_chunks.append(normalized)
            except Exception as error:
                log_operation_failure(logger, "Chunk normalization", error)
                continue

        logger.info(f"标准化完成: {len(normalized_chunks)}/{len(chunks)} 个分块")

        # 批量存储
        if progress_callback:
            progress_callback(0.2, "开始批量存储...")

        total_success = 0
        all_errors = []

        for i in range(0, len(normalized_chunks), batch_size):
            batch = normalized_chunks[i:i + batch_size]

            try:
                # 异步批量索引
                result = await self.es_conn.bulk_index(self.index_name, batch)
                total_success += result["success"]
                all_errors.extend(result["errors"])

                # 进度回调
                if progress_callback:
                    progress = 0.2 + 0.7 * (i + len(batch)) / len(normalized_chunks)
                    progress_callback(progress, f"已存储 {total_success} 个分块")

            except Exception as error:
                log_operation_failure(logger, "Chunk batch storage", error)
                all_errors.append("Document batch storage failed")

        if progress_callback:
            progress_callback(1.0, f"存储完成: {total_success} 个分块")

        logger.info(f"存储完成: 成功 {total_success} 个，错误 {len(all_errors)} 个")
        return total_success, all_errors

    async def delete_index(self) -> bool:
        """异步删除索引"""
        return await self.es_conn.delete_index(self.index_name)

    async def index_exists(self) -> bool:
        """异步检查索引是否存在"""
        return await self.es_conn.index_exists(self.index_name)

    async def delete_document_chunks(self, document_id: str) -> Dict[str, Any]:
        """
        异步删除指定文档的所有分块

        Args:
            document_id: 文档ID

        Returns:
            Dict: 删除结果
        """
        try:
            result = await self.es_conn.delete_documents_by_doc_id(self.index_name, document_id)
            logger.info(
                "文档分块删除完成: success=%s deleted_count=%s",
                bool(result.get("success")),
                int(result.get("deleted_count", 0)),
            )
            return result
        except Exception as error:
            log_operation_failure(logger, "Document chunk deletion", error)
            return {
                "success": False,
                "deleted_count": 0,
                "document_id": document_id,
                "index_name": self.index_name,
                "error": "Elasticsearch document deletion failed",
                "message": "文档删除失败",
            }

    async def get_health(self) -> Dict[str, Any]:
        """异步获取ES健康状态"""
        return await self.es_conn.get_health()

    async def close(self):
        """关闭ES连接，释放资源"""
        if self.es_conn:
            try:
                await self.es_conn.close()
                logger.info("DocumentStore ES连接已关闭")
            except Exception as error:
                log_operation_failure(
                    logger,
                    "Document store connection close",
                    error,
                    level=logging.WARNING,
                )
