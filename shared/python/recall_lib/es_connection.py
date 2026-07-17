#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
异步Elasticsearch连接模块

使用异步客户端提供高性能的ES操作
"""

import logging
from typing import List, Dict, Any, Optional
from urllib.parse import urlsplit, urlunsplit

from elasticsearch import AsyncElasticsearch

from ._logging import log_operation_failure

logger = logging.getLogger("recall_lib.es_connection")

ES_ENDPOINT_ERROR_MESSAGE = "Elasticsearch endpoint does not meet security requirements"


def normalize_es_endpoint(value: str) -> str:
    """Validate ES endpoint syntax while allowing deployment-local hostnames."""
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 2048 or "\\" in normalized:
        raise ValueError(ES_ENDPOINT_ERROR_MESSAGE)
    parsed = urlsplit(normalized)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(ES_ENDPOINT_ERROR_MESSAGE)
    try:
        port = parsed.port
        host = parsed.hostname.rstrip(".").lower().encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        raise ValueError(ES_ENDPOINT_ERROR_MESSAGE) from None
    if not host or any(character.isspace() for character in host):
        raise ValueError(ES_ENDPOINT_ERROR_MESSAGE)
    netloc_host = f"[{host}]" if ":" in host else host
    netloc = f"{netloc_host}:{port}" if port is not None else netloc_host
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


def create_es_connection(
    config: Optional[Dict[str, Any]] = None,
) -> "SimpleESConnection":
    """Build a connection without discarding authentication or timeout options."""
    options = dict(
        config
        or {
            "hosts": "http://localhost:9200",
            "timeout": 600,
        }
    )
    host = options.pop("hosts", "http://localhost:9200")
    return SimpleESConnection(host, **options)


def _summarize_bulk_failure(item: Any) -> Dict[str, Any]:
    """Return bounded bulk-error metadata without the original document or reason."""
    if not isinstance(item, dict) or not item:
        return {"operation": "unknown", "status": "unknown"}

    operation = next(iter(item))
    if operation not in {"index", "create", "update", "delete"}:
        operation = "unknown"
    details = item.get(operation)
    if not isinstance(details, dict):
        return {"operation": operation, "status": "unknown"}

    status = details.get("status")
    return {
        "operation": operation,
        "status": status if isinstance(status, int) else "unknown",
    }


def _log_bulk_failure(prefix: str, item: Any) -> None:
    """Log only structural bulk-error metadata, never the original `_source`."""
    summary = _summarize_bulk_failure(item)
    logger.error(
        "%s: operation=%s status=%s",
        prefix,
        summary["operation"],
        summary["status"],
    )


class SimpleESConnection:
    """
    异步Elasticsearch连接类
    
    使用 AsyncElasticsearch 提供完全异步的ES操作，避免阻塞事件循环。
    内置连接池，自动复用HTTP连接。
    """

    def __init__(self, hosts: str = "http://localhost:9200", **kwargs):
        """
        初始化连接配置（不立即连接）

        Args:
            hosts: ES服务器地址
            **kwargs: 其他ES连接参数（username, password, timeout等）
        """
        self.hosts = normalize_es_endpoint(hosts)
        self.kwargs = kwargs
        self.es: Optional[AsyncElasticsearch] = None
        self._connected = False

    async def connect(self):
        """异步连接到Elasticsearch"""
        if self._connected and self.es:
            return
        
        try:
            # 解析认证信息
            auth = None
            username = self.kwargs.get('username')
            password = self.kwargs.get('password')
            if username and password:
                auth = (username, password)

            # 创建异步ES客户端，内置连接池
            self.es = AsyncElasticsearch(
                hosts=[self.hosts],
                basic_auth=auth,
                verify_certs=bool(self.kwargs.get("verify_certs", True)),
                request_timeout=self.kwargs.get('timeout', 60),
                max_retries=3,
                retry_on_timeout=True,
                # 连接池配置
                maxsize=100  # 最大连接数
            )

            # 测试连接
            health = await self.es.cluster.health()
            logger.info("ES异步连接成功: 状态=%s", health["status"])
            self._connected = True

        except Exception as error:
            log_operation_failure(logger, "Elasticsearch connection", error)
            raise

    async def ensure_connected(self):
        """确保已连接"""
        if not self._connected or not self.es:
            await self.connect()
    
    async def close(self):
        """关闭连接"""
        if self.es:
            await self.es.close()
            self._connected = False
            logger.info("ES连接已关闭")

    async def create_index(self, index_name: str, vector_dim: int = 1024) -> bool:
        """
        异步创建索引

        Args:
            index_name: 索引名称
            vector_dim: 向量维度

        Returns:
            bool: 创建是否成功
        """
        await self.ensure_connected()
        
        if await self.index_exists(index_name):
            logger.info(f"索引 {index_name} 已存在")
            return True

        # DeepRAG兼容的mapping配置
        mapping = {
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "analysis": {
                    "analyzer": {
                        "text_analyzer": {
                            "tokenizer": "standard",
                            "filter": ["lowercase"]
                        },
                        "whitespace_analyzer": {
                            "tokenizer": "whitespace",
                            "filter": ["lowercase"]
                        }
                    }
                }
            },
            "mappings": {
                "properties": {
                    # === 基础标识字段 ===
                    "id": {"type": "keyword"},
                    "doc_id": {"type": "keyword"},
                    "docnm_kwd": {"type": "keyword"},

                    # === 内容字段（检索核心） ===
                    "content_with_weight": {
                        "type": "text",
                        "analyzer": "text_analyzer",
                        "store": True
                    },
                    "content_ltks": {
                        "type": "text",
                        "analyzer": "whitespace_analyzer",
                        "store": True
                    },
                    "content_sm_ltks": {
                        "type": "text",
                        "analyzer": "whitespace_analyzer",
                        "store": True
                    },

                    # === 标题字段（高权重检索） ===
                    "title_tks": {
                        "type": "text",
                        "analyzer": "whitespace_analyzer",
                        "store": True
                    },
                    "title_sm_tks": {
                        "type": "text",
                        "analyzer": "whitespace_analyzer",
                        "store": True
                    },

                    # === 重要字段（最高权重检索） ===
                    "important_kwd": {"type": "keyword"},
                    "important_tks": {
                        "type": "text",
                        "analyzer": "whitespace_analyzer",
                        "store": True
                    },
                    "question_tks": {
                        "type": "text",
                        "analyzer": "whitespace_analyzer",
                        "store": True
                    },
                    "question_kwd": {"type": "keyword"},

                    # === 位置和元数据字段 ===
                    "page_num_int": {"type": "integer"},
                    "position_int": {"type": "integer"},
                    "top_int": {"type": "integer"},

                    # === 状态和时间字段 ===
                    "available_int": {"type": "integer"},
                    "create_timestamp_flt": {"type": "float"},
                    "create_time": {"type": "date"},

                    # === 其他检索相关字段 ===
                    "img_id": {"type": "keyword"},
                    "knowledge_graph_kwd": {"type": "keyword"},
                    "chunk_index": {"type": "integer"},

                    # === 向量字段（动态添加） ===
                    f"q_{vector_dim}_vec": {
                        "type": "dense_vector",
                        "dims": vector_dim,
                        "index": True,
                        "similarity": "cosine"
                    }
                }
            }
        }

        try:
            await self.es.indices.create(index=index_name, body=mapping)
            logger.info(f"成功创建索引: {index_name}")
            return True
        except Exception as error:
            log_operation_failure(logger, "Elasticsearch index creation", error)
            return False

    async def index_exists(self, index_name: str) -> bool:
        """异步检查索引是否存在"""
        await self.ensure_connected()
        try:
            return await self.es.indices.exists(index=index_name)
        except Exception as error:
            log_operation_failure(logger, "Elasticsearch index existence check", error)
            return False

    async def get_existing_vector_fields(self, index_name: str) -> List[str]:
        """
        异步获取索引中已存在的向量字段列表
        
        Args:
            index_name: 索引名称
            
        Returns:
            List[str]: 向量字段名称列表（如 ['q_1024_vec', 'q_2048_vec']）
        """
        await self.ensure_connected()
        try:
            if not await self.index_exists(index_name):
                return []
            
            # 获取索引映射
            response = await self.es.indices.get_mapping(index=index_name)
            # ES 8.x 返回 ObjectApiResponse，需要转换为 dict
            if hasattr(response, 'body'):
                mapping = response.body
            else:
                mapping = dict(response)
            
            if index_name not in mapping:
                return []
            
            properties = mapping[index_name].get("mappings", {}).get("properties", {})
            
            # 查找所有 dense_vector 类型的字段
            vector_fields = []
            for field_name, field_config in properties.items():
                if field_config.get("type") == "dense_vector":
                    vector_fields.append(field_name)
            
            logger.info(f"索引 {index_name} 中存在的向量字段: {vector_fields}")
            return vector_fields
            
        except Exception as error:
            log_operation_failure(logger, "Elasticsearch vector field lookup", error)
            return []

    async def vector_field_exists(self, index_name: str, vector_field: str) -> bool:
        """
        异步检查指定的向量字段是否存在于索引中
        
        Args:
            index_name: 索引名称
            vector_field: 向量字段名称（如 'q_1024_vec'）
            
        Returns:
            bool: 字段是否存在
        """
        existing_fields = await self.get_existing_vector_fields(index_name)
        return vector_field in existing_fields

    async def add_vector_field(self, index_name: str, vector_dim: int) -> bool:
        """
        异步向现有索引添加新的向量字段
        
        Args:
            index_name: 索引名称
            vector_dim: 向量维度
            
        Returns:
            bool: 是否成功添加
        """
        await self.ensure_connected()
        try:
            if not await self.index_exists(index_name):
                logger.error(f"索引 {index_name} 不存在，无法添加向量字段")
                return False
            
            vector_field = f"q_{vector_dim}_vec"
            
            # 检查字段是否已存在
            if await self.vector_field_exists(index_name, vector_field):
                logger.info(f"向量字段 {vector_field} 已存在于索引 {index_name}")
                return True
            
            # 添加新的向量字段映射
            mapping = {
                "properties": {
                    vector_field: {
                        "type": "dense_vector",
                        "dims": vector_dim,
                        "index": True,
                        "similarity": "cosine"
                    }
                }
            }
            
            # 使用 put_mapping API 添加新字段
            await self.es.indices.put_mapping(
                index=index_name,
                body=mapping
            )
            
            logger.info(f"成功向索引 {index_name} 添加向量字段 {vector_field} (维度: {vector_dim})")
            return True
            
        except Exception as error:
            log_operation_failure(logger, "Elasticsearch vector field creation", error)
            return False

    async def ensure_vector_field(self, index_name: str, vector_dim: int) -> bool:
        """
        异步确保索引中存在指定维度的向量字段
        如果索引不存在，创建索引；如果字段不存在，添加字段
        
        Args:
            index_name: 索引名称
            vector_dim: 向量维度
            
        Returns:
            bool: 是否成功确保字段存在
        """
        await self.ensure_connected()
        try:
            # 如果索引不存在，创建索引
            if not await self.index_exists(index_name):
                logger.info(f"索引 {index_name} 不存在，创建新索引（向量维度: {vector_dim}）")
                return await self.create_index(index_name, vector_dim)
            
            # 索引存在，检查向量字段
            vector_field = f"q_{vector_dim}_vec"
            if await self.vector_field_exists(index_name, vector_field):
                logger.info(f"向量字段 {vector_field} 已存在于索引 {index_name}")
                return True
            
            # 向量字段不存在，添加字段
            logger.info(f"向量字段 {vector_field} 不存在，添加到索引 {index_name}")
            return await self.add_vector_field(index_name, vector_dim)
            
        except Exception as error:
            log_operation_failure(logger, "Elasticsearch vector field reconciliation", error)
            return False

    async def delete_index(self, index_name: str) -> bool:
        """异步删除索引"""
        await self.ensure_connected()
        try:
            if await self.index_exists(index_name):
                await self.es.indices.delete(index=index_name)
                logger.info(f"成功删除索引: {index_name}")
                return True
            else:
                logger.info(f"索引 {index_name} 不存在")
                return True
        except Exception as error:
            log_operation_failure(logger, "Elasticsearch index deletion", error)
            return False

    async def bulk_index(self, index_name: str, documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        异步批量索引文档

        Args:
            index_name: 索引名称
            documents: 文档列表

        Returns:
            Dict: 索引结果
        """
        await self.ensure_connected()
        
        if not documents:
            return {"success": 0, "errors": []}

        # 准备批量操作
        actions = []
        for doc in documents:
            # 使用chunk_id作为文档ID
            doc_id = doc.get("chunk_id") or doc.get("id")
            
            # 创建文档副本，移除元数据字段
            doc_source = doc.copy()
            # 移除不应该在_source中的元数据字段
            doc_source.pop("_id", None)
            doc_source.pop("chunk_id", None)
            doc_source.pop("id", None)
            
            action = {
                "_index": index_name,
                "_id": doc_id,
                "_source": doc_source
            }
            actions.append(action)

        try:
            # 使用async_streaming_bulk获取详细错误信息
            from elasticsearch.helpers import async_streaming_bulk, BulkIndexError
            
            success_count = 0
            errors = []
            
            try:
                # 使用async_streaming_bulk进行异步批量索引（可以迭代结果）
                async for success, info in async_streaming_bulk(
                    self.es,
                    actions,
                    index=index_name,
                    chunk_size=100,
                    request_timeout=60,
                    max_chunk_bytes=10485760  # 10MB
                ):
                    if success:
                        success_count += 1
                    else:
                        errors.append(_summarize_bulk_failure(info))
                        _log_bulk_failure("Document indexing failed", info)
            
            except BulkIndexError as bulk_error:
                # 从异常对象中提取错误信息
                if hasattr(bulk_error, 'errors') and bulk_error.errors:
                    logger.error(
                        "BulkIndexError raised: error_count=%s",
                        len(bulk_error.errors),
                    )
                    for error_item in bulk_error.errors:
                        errors.append(_summarize_bulk_failure(error_item))
                        _log_bulk_failure("Bulk document indexing failed", error_item)
                else:
                    errors.append(
                        {
                            "operation": "unknown",
                            "status": "unknown",
                            "error_type": type(bulk_error).__name__,
                        }
                    )
                    logger.error("BulkIndexError raised without item details")

            if errors:
                logger.error(f"批量索引失败: {len(errors)} document(s) failed to index.")
            else:
                logger.info(f"批量索引完成: 成功 {success_count} 个文档")
            
            return {
                "success": success_count,
                "errors": errors
            }

        except Exception as error:
            log_operation_failure(logger, "Elasticsearch bulk indexing", error)
            return {
                "success": 0,
                "errors": [
                    {
                        "operation": "unknown",
                        "status": "unknown",
                        "error_type": type(error).__name__,
                    }
                ],
            }

    async def search(self, index_name: str, query: Dict[str, Any], size: int = 10) -> Dict[str, Any]:
        """
        异步搜索文档

        Args:
            index_name: 索引名称
            query: 查询条件（可以是普通查询或包含KNN的完整查询体）
            size: 返回结果数量

        Returns:
            Dict: 搜索结果
        """
        await self.ensure_connected()
        try:
            # 检查是否为KNN查询或完整查询体
            if "knn" in query or "_source" in query or "highlight" in query:
                # 这是一个完整的查询体，直接使用
                if "size" not in query:
                    query["size"] = size
                body = query
            else:
                # 这是一个普通查询，需要包装
                body = {"query": query, "size": size}

            response = await self.es.search(
                index=index_name,
                body=body
            )
            # ES 8.x 返回 ObjectApiResponse，需要转换为 dict
            if hasattr(response, 'body'):
                return response.body
            return dict(response)
        except Exception as error:
            log_operation_failure(logger, "Elasticsearch search", error)
            raise

    async def delete_documents_by_doc_id(self, index_name: str, document_id: str) -> Dict[str, Any]:
        """
        异步根据document_id删除所有相关的分块文档

        Args:
            index_name: 索引名称
            document_id: 文档ID

        Returns:
            Dict: 删除结果
        """
        await self.ensure_connected()
        try:
            # 构建删除查询
            query = {
                "query": {
                    "term": {
                        "doc_id": document_id
                    }
                }
            }

            # 执行删除操作
            response = await self.es.delete_by_query(
                index=index_name,
                body=query,
                refresh=True,  # 立即刷新索引
                timeout="60s",
                wait_for_completion=True
            )

            deleted_count = response.get("deleted", 0)
            logger.info(f"成功删除文档 {document_id} 的 {deleted_count} 个分块")

            return {
                "success": True,
                "deleted_count": deleted_count,
                "document_id": document_id,
                "index_name": index_name,
                "message": f"成功删除文档 {document_id} 的 {deleted_count} 个分块"
            }

        except Exception as error:
            log_operation_failure(logger, "Elasticsearch document deletion", error)
            return {
                "success": False,
                "deleted_count": 0,
                "document_id": document_id,
                "index_name": index_name,
                "error": "Elasticsearch document deletion failed",
                "message": "文档删除失败",
            }

    async def get_health(self) -> Dict[str, Any]:
        """异步获取ES健康状态"""
        await self.ensure_connected()
        try:
            return await self.es.cluster.health()
        except Exception as error:
            log_operation_failure(logger, "Elasticsearch health check", error)
            return {
                "status": "error",
                "error": "Elasticsearch health check failed",
            }
