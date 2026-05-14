#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 文档解析服务

提供一体化的文档分块+向量化处理功能，支持异步处理和任务状态查询
基于Redis的任务队列机制，支持高并发和任务排队
"""

import asyncio
import logging
import uuid
import os
import shutil
from datetime import datetime
from typing import Dict, List, Optional, Any, Set
from enum import Enum
from pathlib import Path

from embedding.chunk_embedder import ChunkEmbedder, EmbeddingConfig
from common_utils import DeepRAGCommonUtils
from file_security import normalize_upload_filename

logger = logging.getLogger(__name__)

class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"          # 等待处理
    QUEUED = "queued"           # 已排队等待
    PROCESSING = "processing"    # 正在处理
    CHUNKING = "chunking"        # 文档分块中
    EMBEDDING = "embedding"      # 向量化中
    STORING = "storing"          # 存储中
    COMPLETED = "completed"      # 完成
    FAILED = "failed"           # 失败
    CANCELLED = "cancelled"      # 已取消

class DocumentParseTask:
    """文档解析任务"""
    
    def __init__(self, task_id: str, filename: str, file_size: int, 
                 chunk_config: Dict[str, Any], embedding_config: Dict[str, Any],
                 store_config: Dict[str, Any]):
        self.task_id = task_id
        self.filename = filename
        self.file_size = file_size
        self.chunk_config = chunk_config
        self.embedding_config = embedding_config
        self.store_config = store_config
        
        # 任务状态
        self.status = TaskStatus.PENDING
        self.progress = 0.0  # 0.0 - 1.0
        self.message = "任务已创建，等待处理"
        self.created_at = datetime.now()
        self.started_at = None
        self.completed_at = None
        
        # 结果数据
        self.total_chunks = 0
        self.processed_chunks = 0
        self.stored_chunks = 0
        self.errors = []
        self.result_data = {}
        self.full_content = ""  # 文档完整内容
        self.source_path: Optional[str] = None
        self.cancel_requested = False
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "filename": self.filename,
            "file_size": self.file_size,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "total_chunks": self.total_chunks,
            "processed_chunks": self.processed_chunks,
            "stored_chunks": self.stored_chunks,
            "errors": self.errors[-10:],  # 只返回最近10个错误
            "result_data": self.result_data,
            "full_content": self.full_content,  # 文档完整内容（与result_data同级）
            "cancel_requested": self.cancel_requested,
        }

    def to_persisted_dict(self) -> Dict[str, Any]:
        """转换为可持久化的任务元数据"""
        return {
            "task_id": self.task_id,
            "filename": self.filename,
            "file_size": self.file_size,
            "chunk_config": self.chunk_config,
            "embedding_config": self.embedding_config,
            "store_config": self.store_config,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "total_chunks": self.total_chunks,
            "processed_chunks": self.processed_chunks,
            "stored_chunks": self.stored_chunks,
            "errors": self.errors[-10:],
            "result_data": self.result_data,
            "source_path": self.source_path,
            "cancel_requested": self.cancel_requested,
        }

class DocumentParseService:
    """文档解析服务类 - 基于Redis任务队列"""

    def __init__(self, max_concurrent_tasks: int = 10):
        self.utils = DeepRAGCommonUtils()
        self.tasks: Dict[str, DocumentParseTask] = {}  # 任务存储
        self.max_concurrent_tasks = max_concurrent_tasks
        self._task_lock = asyncio.Lock()

        # Redis任务队列
        try:
            from .redis_task_queue import RedisTaskQueue, QueuePriority
        except ImportError:
            # 当作为脚本直接运行时，使用绝对导入
            from redis_task_queue import RedisTaskQueue, QueuePriority

        # 统一配置
        try:
            from .config import settings as _settings
        except ImportError:
            from config import settings as _settings
        self._settings = _settings

        self.task_queue = RedisTaskQueue(
            redis_host=_settings.REDIS_HOST,
            redis_port=_settings.REDIS_PORT,
            redis_username=_settings.REDIS_USERNAME,
            redis_password=_settings.REDIS_PASSWORD,
            redis_db=_settings.REDIS_DB,
            max_concurrent_tasks=max_concurrent_tasks
        )
        self.QueuePriority = QueuePriority

        # 后台任务处理器
        self._worker_tasks: Set[asyncio.Task] = set()
        self._shutdown_event = asyncio.Event()
        self._workers_started = False
        self._payload_dir = Path(_settings.TEMP_DIR) / "task_payloads"
        self._payload_dir.mkdir(parents=True, exist_ok=True)
    
    async def initialize(self):
        """初始化服务"""
        async with self._task_lock:
            # 初始化Redis任务队列
            await self.task_queue.initialize()

            # 启动后台工作进程
            if not self._workers_started:
                await self.start_workers()
                self._workers_started = True

    async def start_workers(self, num_workers: int = None):
        """启动后台工作进程"""
        if num_workers is None:
            num_workers = min(self.max_concurrent_tasks, 5)  # 最多5个worker

        logger.info(f"启动 {num_workers} 个后台工作进程")

        for i in range(num_workers):
            worker_task = asyncio.create_task(self._worker_loop(f"worker-{i}"))
            self._worker_tasks.add(worker_task)
            worker_task.add_done_callback(self._worker_tasks.discard)

    def _payload_path_for(self, task_id: str, filename: str) -> Path:
        safe_name = normalize_upload_filename(filename)
        task_dir = self._payload_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        payload_path = task_dir / safe_name
        payload_path.resolve().relative_to(task_dir.resolve())
        return payload_path

    def _cleanup_task_payload(self, task: DocumentParseTask) -> None:
        if not task.source_path:
            return

        task_dir = Path(task.source_path).parent
        shutil.rmtree(task_dir, ignore_errors=True)
        task.source_path = None

    async def _persist_task(self, task: DocumentParseTask) -> None:
        self.tasks[task.task_id] = task
        await self.task_queue.set_task_data(task.task_id, task.to_persisted_dict())

    async def _restore_task(self, task_id: str) -> Optional[DocumentParseTask]:
        task_data = await self.task_queue.get_task_data(task_id)
        if not task_data:
            return None

        task = DocumentParseTask(
            task_id=task_data["task_id"],
            filename=normalize_upload_filename(task_data["filename"]),
            file_size=task_data["file_size"],
            chunk_config=task_data.get("chunk_config", {}),
            embedding_config=task_data.get("embedding_config", {}),
            store_config=task_data.get("store_config", {}),
        )
        task.status = TaskStatus(task_data.get("status", TaskStatus.PENDING.value))
        task.progress = task_data.get("progress", 0.0)
        task.message = task_data.get("message", task.message)
        created_at = task_data.get("created_at")
        started_at = task_data.get("started_at")
        completed_at = task_data.get("completed_at")
        if created_at:
            task.created_at = datetime.fromisoformat(created_at)
        if started_at:
            task.started_at = datetime.fromisoformat(started_at)
        if completed_at:
            task.completed_at = datetime.fromisoformat(completed_at)
        task.total_chunks = task_data.get("total_chunks", 0)
        task.processed_chunks = task_data.get("processed_chunks", 0)
        task.stored_chunks = task_data.get("stored_chunks", 0)
        task.errors = task_data.get("errors", [])
        task.result_data = task_data.get("result_data", {})
        task.source_path = task_data.get("source_path")
        task.cancel_requested = bool(task_data.get("cancel_requested", False))

        self.tasks[task_id] = task
        return task

    async def _get_or_restore_task(self, task_id: str) -> Optional[DocumentParseTask]:
        task = self.tasks.get(task_id)
        if task:
            return task
        return await self._restore_task(task_id)

    async def _finalize_cancelled_task(self, task: DocumentParseTask, message: str) -> Dict[str, Any]:
        task.status = TaskStatus.CANCELLED
        task.progress = min(task.progress, 0.95)
        task.message = message
        task.completed_at = datetime.now()
        task.cancel_requested = True
        self._cleanup_task_payload(task)
        await self._persist_task(task)
        return {
            "success": False,
            "cancelled": True,
            "message": message,
            "task_id": task.task_id,
        }

    async def _worker_loop(self, worker_name: str):
        """工作进程循环"""
        logger.info(f"工作进程 {worker_name} 已启动")

        while not self._shutdown_event.is_set():
            try:
                # 从队列中取出任务
                task_id = await self.task_queue.dequeue_task()

                if task_id:
                    logger.info(f"工作进程 {worker_name} 开始处理任务 {task_id}")

                    # 处理任务
                    result = await self.process_document_async(task_id)

                    # 更新队列状态
                    if result.get("cancelled", False):
                        await self.task_queue.mark_task_cancelled(task_id)
                    elif result.get("success", False):
                        await self.task_queue.complete_task(task_id)
                    else:
                        await self.task_queue.fail_task(task_id, result.get("message", "处理失败"))

                    logger.info(f"工作进程 {worker_name} 完成任务 {task_id}")
                else:
                    # 没有任务，等待一段时间
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                logger.info(f"工作进程 {worker_name} 被取消")
                break
            except Exception as e:
                logger.error(f"工作进程 {worker_name} 处理任务时出错: {e}")
                await asyncio.sleep(5)  # 出错后等待5秒再继续

        logger.info(f"工作进程 {worker_name} 已停止")

    async def create_task(self, filename: str, file_content: bytes,
                         chunk_config: Dict[str, Any], embedding_config: Dict[str, Any],
                         store_config: Dict[str, Any], priority: str = "normal") -> str:
        """
        创建文档解析任务并加入队列

        Args:
            filename: 文件名
            file_content: 文件内容
            chunk_config: 分块配置
            embedding_config: 向量化配置
            store_config: 存储配置
            priority: 任务优先级 (low, normal, high, urgent)

        Returns:
            任务ID
        """
        # 确保服务已初始化
        if not self._workers_started:
            await self.initialize()

        task_id = str(uuid.uuid4())
        safe_filename = normalize_upload_filename(filename)

        # 创建任务对象
        task = DocumentParseTask(
            task_id=task_id,
            filename=safe_filename,
            file_size=len(file_content),
            chunk_config=chunk_config,
            embedding_config=embedding_config,
            store_config=store_config
        )

        # 设置任务状态为排队
        task.status = TaskStatus.QUEUED
        task.message = "任务已创建，正在排队等待处理"
        task.source_path = str(self._payload_path_for(task_id, safe_filename))
        with open(task.source_path, "wb") as payload_file:
            payload_file.write(file_content)

        # 存储任务
        self.tasks[task_id] = task

        # 解析优先级
        priority_map = {
            "low": self.QueuePriority.LOW,
            "normal": self.QueuePriority.NORMAL,
            "high": self.QueuePriority.HIGH,
            "urgent": self.QueuePriority.URGENT
        }
        task_priority = priority_map.get(priority.lower(), self.QueuePriority.NORMAL)

        # 加入Redis队列
        success = await self.task_queue.enqueue_task(
            task_id,
            task_priority,
            task_data=task.to_persisted_dict(),
        )

        if success:
            logger.info(f"创建文档解析任务: {task_id}, 文件: {filename}, 优先级: {priority}")
        else:
            task.status = TaskStatus.FAILED
            task.message = "任务加入队列失败"
            self._cleanup_task_payload(task)
            await self._persist_task(task)
            logger.error(f"任务 {task_id} 加入队列失败")

        return task_id
    
    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        获取任务状态（包含队列位置信息）

        Args:
            task_id: 任务ID

        Returns:
            任务状态信息
        """
        task = await self._get_or_restore_task(task_id)
        if not task:
            return None

        task_dict = task.to_dict()

        # 如果任务在队列中，获取队列位置
        if task.status == TaskStatus.QUEUED:
            position = await self.task_queue.get_task_position(task_id)
            if position:
                task_dict["queue_position"] = position
                task_dict["message"] = f"任务排队中，当前位置: {position}"

        return task_dict
    
    async def list_tasks(self, limit: int = 50, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        列出任务
        
        Args:
            limit: 返回任务数量限制
            status_filter: 状态过滤
            
        Returns:
            任务列表
        """
        persisted_tasks = await self.task_queue.list_task_data()
        for task_id in persisted_tasks:
            if task_id not in self.tasks:
                await self._restore_task(task_id)

        tasks = list(self.tasks.values())
        
        # 按状态过滤
        if status_filter:
            tasks = [t for t in tasks if t.status.value == status_filter]
        
        # 按创建时间排序（最新的在前）
        tasks.sort(key=lambda x: x.created_at, reverse=True)
        
        # 限制数量
        tasks = tasks[:limit]
        
        return [task.to_dict() for task in tasks]
    
    async def process_document_async(self, task_id: str) -> Dict[str, Any]:
        """
        异步处理文档解析任务（由工作进程调用）

        Args:
            task_id: 任务ID

        Returns:
            处理结果
        """
        task = await self._get_or_restore_task(task_id)
        if not task:
            return {
                "success": False,
                "message": f"任务 {task_id} 不存在",
                "task_id": task_id
            }
        
        try:
            if task.cancel_requested:
                return await self._finalize_cancelled_task(task, "任务在开始处理前已取消")

            # 更新任务状态
            task.status = TaskStatus.PROCESSING
            task.started_at = datetime.now()
            task.message = "开始处理文档"
            task.progress = 0.0
            await self._persist_task(task)

            # 步骤1: 文档分块
            task.status = TaskStatus.CHUNKING
            task.message = "开始处理文档分块"
            task.progress = 0.1
            await self._persist_task(task)
            
            logger.info(f"开始处理文档解析任务: {task_id}")
            
            # 步骤1: 文档分块
            chunk_result = await self._process_chunking(task)
            if task.cancel_requested:
                return await self._finalize_cancelled_task(task, "已收到取消请求，文档分块完成后停止后续处理")
            if not chunk_result["success"]:
                task.status = TaskStatus.FAILED
                task.message = f"文档分块失败: {chunk_result['message']}"
                task.completed_at = datetime.now()
                await self._persist_task(task)
                return chunk_result
            
            chunks = chunk_result["chunks"]
            full_content = chunk_result.get("full_content", "")  # 获取完整内容
            task.total_chunks = len(chunks)
            task.progress = 0.4
            await self._persist_task(task)
            
            # 步骤2: 向量化
            task.status = TaskStatus.EMBEDDING
            task.message = f"开始向量化 {len(chunks)} 个分块"
            await self._persist_task(task)
            
            embedding_result = await self._process_embedding(task, chunks)
            if task.cancel_requested:
                return await self._finalize_cancelled_task(task, "已收到取消请求，向量化完成后停止后续处理")
            if not embedding_result["success"]:
                task.status = TaskStatus.FAILED
                task.message = f"向量化失败: {embedding_result['message']}"
                task.completed_at = datetime.now()
                await self._persist_task(task)
                return embedding_result
            
            task.processed_chunks = len(chunks)
            task.progress = 0.7
            await self._persist_task(task)

            if task.cancel_requested:
                return await self._finalize_cancelled_task(task, "已收到取消请求，已在存储前停止任务")
            
            # 步骤3: 存储
            task.status = TaskStatus.STORING
            task.message = f"开始存储 {len(chunks)} 个向量化分块"
            await self._persist_task(task)
            
            store_result = await self._process_storing(task, chunks)
            if not store_result["success"]:
                task.status = TaskStatus.FAILED
                task.message = f"存储失败: {store_result['message']}"
                task.completed_at = datetime.now()
                await self._persist_task(task)
                return store_result
            
            # 任务完成
            task.status = TaskStatus.COMPLETED
            task.progress = 1.0
            task.stored_chunks = store_result.get("stored_count", 0)
            task.message = f"处理完成: 成功存储 {task.stored_chunks} 个分块"
            task.completed_at = datetime.now()
            task.full_content = full_content  # 保存完整内容到外层
            
            # 保存结果数据（不再包含full_content）
            task.result_data = {
                "total_chunks": task.total_chunks,
                "stored_chunks": task.stored_chunks,
                "vector_dimension": embedding_result.get("vector_dimension", 0),
                "total_tokens": embedding_result.get("token_count", 0),
                "processing_time": (task.completed_at - task.started_at).total_seconds(),
                "index_name": task.store_config.get("index_name")
            }
            self._cleanup_task_payload(task)
            await self._persist_task(task)
            
            logger.info(f"文档解析任务完成: {task_id}")
            
            return {
                "success": True,
                "message": task.message,
                "task_id": task_id,
                "data": task.result_data
            }
            
        except Exception as e:
            logger.error(f"文档解析任务失败 {task_id}: {e}")
            
            task = self.tasks.get(task_id)
            if task:
                task.status = TaskStatus.FAILED
                task.message = f"处理异常: {str(e)}"
                task.completed_at = datetime.now()
                task.errors.append(str(e))
                await self._persist_task(task)
            
            return {
                "success": False,
                "message": f"文档解析失败: {str(e)}",
                "task_id": task_id
            }
    
    async def _process_chunking(self, task: DocumentParseTask) -> Dict[str, Any]:
        """处理文档分块"""
        try:
            # 使用现有的分块逻辑（从api.py中复制）
            import sys
            import os
            import importlib
            # 添加当前目录到路径以便导入
            current_dir = os.path.dirname(os.path.abspath(__file__))
            if current_dir not in sys.path:
                sys.path.insert(0, current_dir)
            
            # 动态导入 process_chunk_in_process 函数
            # 兼容不同部署形态：
            # - 源码拆分后函数位于 app.py（module: app）
            # - 向后兼容入口 api.py（module: api）
            # - 历史/编译产物可能使用 api.app 或 api.api
            process_chunk_in_process = None
            for module_name in ['app', 'api', 'api.app', 'api.api']:
                try:
                    module = importlib.import_module(module_name)
                    if hasattr(module, 'process_chunk_in_process'):
                        process_chunk_in_process = getattr(module, 'process_chunk_in_process')
                        break
                except ImportError:
                    continue
            
            if process_chunk_in_process is None:
                raise ImportError("无法导入 process_chunk_in_process 函数")
            
            # 保存临时文件
            temp_dir = Path("/tmp/deeprag_parse")
            temp_dir.mkdir(exist_ok=True)
            # 使用UUID作为文件夹名，保持原始文件名
            task_temp_dir = temp_dir / task.task_id
            task_temp_dir.mkdir(exist_ok=True)
            safe_filename = normalize_upload_filename(task.filename)
            temp_file_path = task_temp_dir / safe_filename
            temp_file_path.resolve().relative_to(task_temp_dir.resolve())
            if not task.source_path or not Path(task.source_path).exists():
                raise FileNotFoundError(f"任务源文件不存在: {task.source_path}")

            shutil.copy2(task.source_path, temp_file_path)
            
            try:
                # 准备视觉解析参数（如果需要）
                cv_model_config = task.chunk_config.get("cv_model_config")
                vision_kwargs = None
                if cv_model_config:
                    vision_kwargs = {
                        'vision_dpi': task.chunk_config.get("vision_dpi", 50),
                        'vision_batch_size': task.chunk_config.get("vision_batch_size", 10),
                        'vision_keep_images': task.chunk_config.get("vision_keep_images", False),
                        'vision_use_custom_prompt': task.chunk_config.get("vision_use_custom_prompt", False),
                        'vision_custom_prompt': task.chunk_config.get("vision_custom_prompt", None)
                    }
                
                # 执行分块处理
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,  # 使用默认线程池
                    process_chunk_in_process,
                    str(temp_file_path),
                    task.chunk_config.get("parser_type", "auto"),
                    task.chunk_config.get("chunk_token_num", 256),
                    task.chunk_config.get("delimiter", "\n。；！？"),
                    task.chunk_config.get("language", "Chinese"),
                    task.chunk_config.get("layout_recognize", "DeepDOC"),
                    task.chunk_config.get("zoomin", 3),
                    task.chunk_config.get("from_page", 0),
                    task.chunk_config.get("to_page", 100000),
                    task.chunk_config.get("document_id"),
                    cv_model_config,
                    vision_kwargs
                )
                
                if result["success"]:
                    return {
                        "success": True,
                        "chunks": result["chunks"],
                        "full_content": result.get("full_content", ""),  # 新增：完整内容
                        "message": f"成功分块，生成 {len(result['chunks'])} 个分块"
                    }
                else:
                    return {
                        "success": False,
                        "message": result.get("error", "分块处理失败")
                    }
            
            finally:
                # 清理临时目录和文件
                if task_temp_dir.exists():
                    shutil.rmtree(task_temp_dir)
        
        except Exception as e:
            logger.error(f"分块处理异常: {e}")
            return {
                "success": False,
                "message": f"分块处理异常: {str(e)}"
            }
    
    async def _process_embedding(self, task: DocumentParseTask, chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """处理向量化"""
        try:
            # 创建向量化配置
            config = EmbeddingConfig(
                model_factory=task.embedding_config["model_factory"],
                model_name=task.embedding_config["model_name"],
                api_key=task.embedding_config.get("api_key", ""),
                base_url=task.embedding_config.get("base_url"),
                batch_size=task.embedding_config.get("batch_size", 16),
                filename_embd_weight=task.embedding_config.get("filename_embd_weight", 0.1)
            )
            
            # 执行向量化
            embedder = ChunkEmbedder(config)
            
            loop = asyncio.get_event_loop()
            token_count, vector_size = await loop.run_in_executor(
                None,
                embedder.embed_chunks_sync,
                chunks
            )
            
            return {
                "success": True,
                "token_count": token_count,
                "vector_dimension": vector_size,
                "message": f"成功向量化 {len(chunks)} 个分块"
            }
        
        except Exception as e:
            logger.error(f"向量化处理异常: {e}")
            return {
                "success": False,
                "message": f"向量化处理异常: {str(e)}"
            }
    
    async def _process_storing(self, task: DocumentParseTask, chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """处理存储"""
        try:
            # 创建文档存储器
            store = self.utils.create_document_store(
                es_host=task.store_config["es_host"],
                index_name=task.store_config["index_name"],
                username=task.store_config.get("username"),
                password=task.store_config.get("password"),
                timeout=task.store_config.get("timeout", 60)
            )
            
            # 执行异步存储（不再使用线程池）
            try:
                success_count, errors = await store.store_chunks(
                    chunks,
                    task.store_config.get("batch_size", 100)
                )
            finally:
                # 清理ES连接
                try:
                    await store.close()
                except Exception as e:
                    logger.warning(f"关闭ES连接时出错: {e}")
            
            return {
                "success": len(errors) == 0,
                "stored_count": success_count,
                "errors": errors,
                "message": f"成功存储 {success_count} 个分块"
            }
        
        except Exception as e:
            logger.error(f"存储处理异常: {e}")
            return {
                "success": False,
                "message": f"存储处理异常: {str(e)}"
            }
    
    async def get_queue_stats(self) -> Dict[str, Any]:
        """获取队列统计信息"""
        return await self.task_queue.get_queue_stats()

    async def cancel_task(self, task_id: str) -> Dict[str, Any]:
        """取消任务"""
        task = await self._get_or_restore_task(task_id)
        if not task:
            return {"success": False, "reason": "not_found"}

        active_statuses = {
            TaskStatus.PROCESSING,
            TaskStatus.CHUNKING,
            TaskStatus.EMBEDDING,
            TaskStatus.STORING,
        }

        if task.status in active_statuses or await self.task_queue.is_task_processing(task_id):
            task.cancel_requested = True
            task.message = "已收到取消请求，当前步骤结束后将停止后续处理"
            await self._persist_task(task)
            return {"success": True, "state": "cancellation_requested", "task": task.to_dict()}

        removed = await self.task_queue.remove_queued_task(task_id)
        if removed or task.status in {TaskStatus.PENDING, TaskStatus.QUEUED}:
            task.status = TaskStatus.CANCELLED
            task.cancel_requested = True
            task.message = "任务已取消"
            task.completed_at = datetime.now()
            self._cleanup_task_payload(task)
            await self._persist_task(task)
            return {"success": True, "state": "cancelled", "task": task.to_dict()}

        return {"success": False, "reason": "not_cancellable"}

    async def cleanup_old_tasks(self, max_age_hours: int = 24) -> int:
        """清理旧任务"""
        # 清理Redis中的过期任务
        redis_cleaned = await self.task_queue.cleanup_expired_tasks(max_age_hours)

        persisted_tasks = await self.task_queue.list_task_data()
        for task_id in persisted_tasks:
            if task_id not in self.tasks:
                await self._restore_task(task_id)

        # 清理本地任务存储
        current_time = datetime.now()
        to_remove = []

        for task_id, task in self.tasks.items():
            age_hours = (current_time - task.created_at).total_seconds() / 3600
            if age_hours > max_age_hours:
                to_remove.append(task_id)

        for task_id in to_remove:
            task = self.tasks.pop(task_id)
            self._cleanup_task_payload(task)
            await self.task_queue.delete_task_data(task_id)
            logger.info(f"清理旧任务: {task_id}")

        total_cleaned = redis_cleaned + len(to_remove)
        logger.info(f"总共清理了 {total_cleaned} 个旧任务")

        return total_cleaned

    async def shutdown(self):
        """关闭服务"""
        logger.info("正在关闭文档解析服务...")

        # 设置关闭事件
        self._shutdown_event.set()

        # 等待所有工作进程完成
        if self._worker_tasks:
            logger.info(f"等待 {len(self._worker_tasks)} 个工作进程完成...")
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)

        # 关闭Redis连接
        await self.task_queue.close()

        logger.info("文档解析服务已关闭")
