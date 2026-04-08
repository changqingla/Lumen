#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 统一服务 - FastAPI 应用入口
"""

import asyncio
import logging
import time
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# 添加项目根目录到路径
current_dir = Path(__file__).parent.absolute()
project_root = current_dir.parent
sys.path.insert(0, str(project_root))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 统一配置（pydantic BaseSettings）
from config import settings

# 导入依赖模块
from chunk.document_chunker import DocumentChunker
from core.utils import ParserType
from embedding.chunk_embedder import ChunkEmbedder, EmbeddingConfig
from embed_store.chunk_store import DocumentStore
from embed_store.store_utils import ChunkValidator
# 导入服务
from chunk_edit_service import ChunkEditService
from common_utils import DeepRAGCommonUtils
from document_parse_service import DocumentParseService, TaskStatus

# 导入schemas
from schemas import (
    ChunkRequest, EmbeddingRequest, StoreRequest,
    DocumentDeleteRequest, DocumentParseRequest,
    VisionExtractRequest, TaskStatusResponse, UnifiedResponse,
)


# 进程池处理函数（必须在模块级别定义，以便pickle序列化）
def process_chunk_in_process(file_path: str, parser_type: str, chunk_token_num: int, 
                           delimiter: str, language: str, layout_recognize: str,
                           zoomin: int, from_page: int, to_page: int, document_id: str = None,
                           cv_model_config: dict = None, vision_kwargs: dict = None,
                           ir_table_kwargs: dict = None):
    """
    在独立进程中执行文档分块处理
    
    这个函数运行在独立的进程中，拥有完全隔离的NLTK状态，
    避免了WordNet等NLTK组件的线程安全问题。
    
    参数:
        cv_model_config: 视觉模型配置（仅当 parser_type="ppt" 时需要）
        vision_kwargs: 视觉解析的额外参数（dpi, batch_size等）
        ir_table_kwargs: ir-table 解析器的额外参数（auto_unmerge, keep_title等）
    
    返回:
        包含 chunks, full_content 等信息的字典
    """
    import sys
    from pathlib import Path
    import time
    
    # 确保进程中也能找到项目模块
    current_dir = Path(__file__).parent.absolute()
    project_root = current_dir.parent
    sys.path.insert(0, str(project_root))
    
    try:
        from chunk.document_chunker import DocumentChunker
        
        start_time = time.time()
        
        # 在独立进程中创建分块器（拥有独立的NLTK状态）
        document_chunker = DocumentChunker(
            parser_type=parser_type,
            chunk_token_num=chunk_token_num,
            delimiter=delimiter,
            language=language,
            layout_recognize=layout_recognize,
            zoomin=zoomin,
            from_page=from_page,
            to_page=to_page,
            cv_model_config=cv_model_config,
            vision_batch_size=vision_kwargs.get('vision_batch_size', 10) if vision_kwargs else 10
        )
        
        # 准备额外的kwargs
        extra_kwargs = {}
        
        # 添加vision parser参数
        if vision_kwargs:
            extra_kwargs.update({
                'dpi': vision_kwargs.get('vision_dpi', 50),
                'keep_images': vision_kwargs.get('vision_keep_images', False),
                'use_custom_prompt': vision_kwargs.get('vision_use_custom_prompt', False),
                'custom_prompt': vision_kwargs.get('vision_custom_prompt', None)
            })
        
        # 添加ir-table parser参数
        if ir_table_kwargs:
            extra_kwargs.update({
                'auto_unmerge': ir_table_kwargs.get('auto_unmerge', True),
                'keep_title': ir_table_kwargs.get('keep_title', True),
                'unmerge_start_row': ir_table_kwargs.get('unmerge_start_row', 2),
                'unmerge_end_row': ir_table_kwargs.get('unmerge_end_row', None),
                'only_columns': ir_table_kwargs.get('only_columns', None),
                'exclude_sheets': ir_table_kwargs.get('exclude_sheets', None),
                'include_sheets': ir_table_kwargs.get('include_sheets', None)
            })
        
        # 执行分块处理（无线程安全问题），同时获取完整内容
        result = document_chunker.chunk_document(file_path=file_path, return_full_content=True, **extra_kwargs)
        
        # 处理返回结果
        if isinstance(result, tuple):
            chunks, full_content = result
        else:
            chunks = result
            full_content = ""
        
        # 为每个chunk添加32位的chunk_id、document_id和available_int字段
        import uuid
        for i, chunk in enumerate(chunks):
            # 使用UUID生成唯一的chunk_id，取前8位作为32位ID
            chunk_id = str(uuid.uuid4()).replace('-', '')[:16]
            chunk['chunk_id'] = chunk_id
            # 添加document_id字段
            if document_id:
                chunk['document_id'] = document_id
            # 添加available_int字段，默认值为1
            chunk['available_int'] = 1
        
        processing_time = time.time() - start_time
        
        return {
            "success": True,
            "chunks": chunks,
            "total_chunks": len(chunks),
            "full_content": full_content,
            "processing_time": processing_time,
            "parser_type": parser_type,
            "process_id": os.getpid()
        }
        
    except Exception as e:
        return {
            "success": False,
            "chunks": None,
            "total_chunks": 0,
            "full_content": "",
            "processing_time": time.time() - start_time,
            "parser_type": parser_type,
            "error": str(e),
            "process_id": os.getpid()
        }


class UnifiedService:
    """统一服务类 - 直接集成各模块功能"""

    def __init__(self):
        self.temp_dir = Path(settings.TEMP_DIR)
        self.temp_dir.mkdir(exist_ok=True)
        # 初始化chunk编辑服务
        self.chunk_edit_service = ChunkEditService()
        # 初始化文档解析服务
        self.document_parse_service = DocumentParseService(
            max_concurrent_tasks=settings.MAX_CONCURRENT_TASKS
        )
        # 使用共用工具类
        self.utils = DeepRAGCommonUtils()

    def save_temp_file(self, file_content: bytes, filename: str) -> str:
        """保存临时文件"""
        import uuid
        # 创建UUID子目录来避免文件名冲突，但保持原始文件名
        file_id = str(uuid.uuid4())
        temp_subdir = self.temp_dir / file_id
        temp_subdir.mkdir(exist_ok=True)
        temp_file_path = temp_subdir / filename
        
        with open(temp_file_path, "wb") as f:
            f.write(file_content)

        return str(temp_file_path)

    def cleanup_temp_file(self, file_path: str):
        """清理临时文件"""
        try:
            if os.path.exists(file_path):
                # 删除文件
                os.remove(file_path)
                # 删除父目录（如果是UUID子目录且为空）
                parent_dir = Path(file_path).parent
                if parent_dir != self.temp_dir and parent_dir.exists():
                    try:
                        parent_dir.rmdir()  # 只删除空目录
                    except OSError:
                        pass  # 目录不为空，忽略错误
        except Exception as e:
            logger.warning(f"清理临时文件失败 {file_path}: {str(e)}")

    def detect_parser_type(self, filename: str) -> str:
        """根据文件名检测解析器类型"""
        ext = Path(filename).suffix.lower()

        parser_map = {
            '.pdf': "general",
            '.docx': "general",
            '.doc': "general",
            '.txt': "general",
            '.md': "general",
            '.html': "general",
            '.csv': ParserType.TABLE,
            '.ppt': ParserType.PRESENTATION,
            '.pptx': ParserType.PRESENTATION,
            '.xls': ParserType.TABLE,
            '.xlsx': ParserType.TABLE,
        }

        return parser_map.get(ext, "general")

    async def process_chunk(self, file_content: bytes, filename: str, request: ChunkRequest) -> Dict[str, Any]:
        """
        异步处理文档分块
        
        - 传统解析器：使用进程池处理，避免NLTK线程安全问题
        - 视觉解析器：使用线程池处理（API调用是IO密集型）
        """
        async with semaphore:
            temp_file_path = None
            try:
                # 保存临时文件
                temp_file_path = self.save_temp_file(file_content, filename)

                # 确定解析器类型
                parser_type = request.parser_type

                # 判断是否是视觉解析器
                is_vision = parser_type == "ppt"
                
                if is_vision:
                    # 视觉解析器：在线程池中处理（IO密集型）
                    logger.info(f"使用视觉解析器处理: {filename} (批量大小: {request.vision_batch_size})")

                    # 构建 CV 模型配置
                    cv_model_config = {
                        "model_factory": request.cv_model_factory,
                        "model_name": request.cv_model_name or "default",
                        "api_key": request.cv_api_key,
                        "base_url": request.cv_base_url,
                        "lang": request.language
                    }
                    
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        executor,  # 使用线程池
                        self._process_chunk_with_vision_sync,
                        temp_file_path,
                        request,
                        cv_model_config
                    )
                else:
                    # 传统解析器：在进程池中处理
                    logger.info(f"使用传统解析器处理: {filename} (解析器: {parser_type})")
                    
                    # 构建 ir-table 参数（如果是 ir-table 解析器）
                    ir_table_kwargs = None
                    if parser_type == "ir-table":
                        # 处理逗号分隔的列表参数
                        only_columns = None
                        if request.ir_table_only_columns:
                            only_columns = [col.strip() for col in request.ir_table_only_columns.split(',')]
                        
                        exclude_sheets = None
                        if request.ir_table_exclude_sheets:
                            exclude_sheets = [sheet.strip() for sheet in request.ir_table_exclude_sheets.split(',')]
                        
                        include_sheets = None
                        if request.ir_table_include_sheets:
                            include_sheets = [sheet.strip() for sheet in request.ir_table_include_sheets.split(',')]
                        
                        ir_table_kwargs = {
                            'auto_unmerge': request.ir_table_auto_unmerge,
                            'keep_title': request.ir_table_keep_title,
                            'unmerge_start_row': request.ir_table_unmerge_start_row,
                            'unmerge_end_row': request.ir_table_unmerge_end_row,
                            'only_columns': only_columns,
                            'exclude_sheets': exclude_sheets,
                            'include_sheets': include_sheets
                        }
                        logger.info(f"ir-table 配置: auto_unmerge={request.ir_table_auto_unmerge}, "
                                  f"keep_title={request.ir_table_keep_title}, "
                                  f"unmerge_start_row={request.ir_table_unmerge_start_row}")
                    
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        chunk_executor,  # 使用进程池
                    process_chunk_in_process,
                    temp_file_path,
                    parser_type,
                    request.chunk_token_num,
                    request.delimiter,
                    request.language,
                    request.layout_recognize,
                    request.zoomin,
                    request.from_page,
                    request.to_page,
                        request.document_id,
                        None,  # 传统解析器不需要 cv_model_config
                        None,  # 传统解析器不需要 vision_kwargs
                        ir_table_kwargs  # ir-table 参数
                )

                # 添加文件大小信息
                result["file_size"] = len(file_content)
                if is_vision:
                    logger.info(f"视觉解析完成: {filename}")
                else:
                    logger.info(f"进程池处理完成: {filename} (进程ID: {result.get('process_id', 'unknown')})")
                
                return result

            finally:
                # 清理临时文件
                if temp_file_path:
                    self.cleanup_temp_file(temp_file_path)
    
    def _process_chunk_with_vision_sync(self, file_path: str, request: ChunkRequest,
                                        cv_model_config: Dict) -> Dict[str, Any]:
        """
        同步执行视觉解析（在线程池中调用）
        """
        import time
        start_time = time.time()
        
        try:
            from chunk.document_chunker import DocumentChunker
            
            # 创建分块器（带 CV 模型配置）
            chunker = DocumentChunker(
                parser_type=request.parser_type,
                chunk_token_num=request.chunk_token_num,
                delimiter=request.delimiter,
                language=request.language,
                layout_recognize=request.layout_recognize,
                zoomin=request.zoomin,
                from_page=request.from_page,
                to_page=request.to_page,
                cv_model_config=cv_model_config,
                vision_batch_size=request.vision_batch_size
            )
            
            # 执行分块（会自动批量并发处理），同时获取完整内容
            result = chunker.chunk_document(
                file_path=file_path,
                return_full_content=True,
                dpi=request.vision_dpi,
                keep_images=request.vision_keep_images,
                use_custom_prompt=request.vision_use_custom_prompt,
                custom_prompt=request.vision_custom_prompt
            )
            
            # 处理返回结果
            if isinstance(result, tuple):
                chunks, full_content = result
            else:
                chunks = result
                full_content = ""
            
            # 添加 chunk_id、document_id 和 available_int
            import uuid
            for chunk in chunks:
                chunk['chunk_id'] = str(uuid.uuid4()).replace('-', '')[:16]
                if request.document_id:
                    chunk['document_id'] = request.document_id
                chunk['available_int'] = 1
            
            processing_time = time.time() - start_time
            
            return {
                "success": True,
                "chunks": chunks,
                "total_chunks": len(chunks),
                "full_content": full_content,
                "processing_time": processing_time,
                "parser_type": request.parser_type,
                "vision_batch_size": request.vision_batch_size
            }
            
        except Exception as e:
            logger.error(f"视觉解析失败: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "chunks": None,
                "total_chunks": 0,
                "full_content": "",
                "processing_time": time.time() - start_time,
                "parser_type": request.parser_type,
                "error": str(e)
            }

    def process_embedding_sync(self, chunks: List[Dict[str, Any]], request: EmbeddingRequest) -> Dict[str, Any]:
        """同步执行向量化操作"""
        try:
            # 创建嵌入配置
            config = EmbeddingConfig(
                model_factory=request.model_factory,
                model_name=request.model_name,
                api_key=request.api_key or "",
                base_url=request.base_url,
                batch_size=request.batch_size,
                filename_embd_weight=request.filename_embd_weight
            )

            # 创建嵌入器
            embedder = ChunkEmbedder(config)

            # 执行向量化
            token_count, vector_size = embedder.embed_chunks_sync(chunks)

            # 生成统计信息
            embed_stats = {
                "total_chunks": len(chunks),
                "total_tokens": token_count,
                "vector_dimension": vector_size,
                "model_factory": config.model_factory,
                "model_name": config.model_name
            }

            return {
                "success": True,
                "chunks": chunks,
                "stats": embed_stats
            }

        except Exception as e:
            logger.error(f"向量化失败: {e}")
            return {
                "success": False,
                "message": f"向量化失败: {str(e)}",
                "chunks": None,
                "stats": None
            }

    async def process_embedding(self, chunks: List[Dict[str, Any]], request: EmbeddingRequest) -> Dict[str, Any]:
        """异步处理向量化"""
        async with semaphore:
            try:
                # 在线程池中执行向量化处理
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    executor,
                    self.process_embedding_sync,
                    chunks,
                    request
                )
                return result
            except Exception as e:
                logger.error(f"向量化处理失败: {e}")
                return {
                    "success": False,
                    "message": f"向量化处理失败: {str(e)}",
                    "chunks": None,
                    "stats": None
                }

    async def process_store(self, chunks: List[Dict[str, Any]], request: StoreRequest) -> Dict[str, Any]:
        """异步处理存储（完全异步，不使用线程池）"""
        async with semaphore:
            store = None
            try:
                # 验证分块数据
                validation = ChunkValidator.validate_chunks(chunks)
                if not validation["valid"]:
                    return {
                        "success": False,
                        "message": f"分块数据验证失败: {validation['errors']}",
                        "stored_count": 0,
                        "total_count": len(chunks),
                        "error_count": 1,
                        "errors": validation['errors']
                    }

                # 检测向量维度（用于日志记录）
                vector_dim = None
                for chunk in chunks:
                    for key in chunk.keys():
                        if key.startswith("q_") and key.endswith("_vec"):
                            if isinstance(chunk[key], list):
                                vector_dim = len(chunk[key])
                                break
                    if vector_dim:
                        break
                
                if vector_dim:
                    logger.info(f"准备存储 {len(chunks)} 个分块到索引 {request.index_name}，向量维度: {vector_dim}")
                else:
                    logger.warning(f"未检测到向量维度，分块数据可能缺少向量字段")

                # 创建存储器
                es_kwargs = {}
                if request.username and request.password:
                    es_kwargs.update({
                        "username": request.username,
                        "password": request.password
                    })
                if request.timeout:
                    es_kwargs["timeout"] = request.timeout

                store = DocumentStore(
                    es_host=request.es_host,
                    index_name=request.index_name,
                    **es_kwargs
                )

                # 异步执行存储
                success_count, errors = await store.store_chunks(
                    chunks,
                    batch_size=request.batch_size
                )

                result_message = f"成功存储 {success_count} 个分块"
                if vector_dim:
                    result_message += f"（向量维度: {vector_dim}）"

                return {
                    "success": len(errors) == 0,
                    "message": result_message,
                    "stored_count": success_count,
                    "total_count": len(chunks),
                    "error_count": len(errors),
                    "errors": errors[:10],
                    "vector_dimension": vector_dim
                }

            except Exception as e:
                logger.error(f"存储失败: {e}")
                return {
                    "success": False,
                    "message": f"存储失败: {str(e)}",
                    "stored_count": 0,
                    "total_count": len(chunks),
                    "error_count": 1,
                    "errors": [str(e)]
                }
            finally:
                if store:
                    try:
                        await store.close()
                        logger.debug("DocumentStore ES连接已清理")
                    except Exception as e:
                        logger.warning(f"关闭DocumentStore ES连接时出错: {e}")

    async def process_document_delete(self, request: DocumentDeleteRequest) -> Dict[str, Any]:
        """异步处理文档删除（完全异步，不使用线程池）"""
        async with semaphore:
            store = None
            try:
                es_kwargs = {}
                if request.username and request.password:
                    es_kwargs.update({
                        "username": request.username,
                        "password": request.password
                    })
                if request.timeout:
                    es_kwargs["timeout"] = request.timeout

                store = DocumentStore(
                    es_host=request.es_host,
                    index_name=request.index_name,
                    **es_kwargs
                )

                result = await store.delete_document_chunks(request.document_id)
                return result

            except Exception as e:
                logger.error(f"文档删除处理失败: {e}")
                return {
                    "success": False,
                    "deleted_count": 0,
                    "document_id": request.document_id,
                    "index_name": request.index_name,
                    "error": str(e),
                    "message": f"文档删除处理失败: {str(e)}"
                }
            finally:
                if store:
                    try:
                        await store.close()
                        logger.debug("DocumentStore ES连接已清理")
                    except Exception as e:
                        logger.warning(f"关闭DocumentStore ES连接时出错: {e}")


# 全局服务实例
unified_service = UnifiedService()


# 生命周期管理
@asynccontextmanager
async def lifespan(app):
    """应用生命周期管理"""
    # 启动
    logger.info("DeepRAG 统一服务启动中...")

    # 创建临时目录
    temp_dir = Path(settings.TEMP_DIR)
    temp_dir.mkdir(exist_ok=True)

    logger.info(f"服务配置:")
    logger.info(f"  - 最大工作线程数: {settings.MAX_WORKERS}")
    logger.info(f"  - 最大文件大小: {settings.MAX_FILE_SIZE / 1024 / 1024:.0f}MB")
    logger.info(f"  - 最大并发任务数: {settings.MAX_CONCURRENT_TASKS}")
    logger.info(f"  - Chunk处理进程池大小: {settings.CHUNK_PROCESS_WORKERS}")
    logger.info(f"  - 支持的文件格式: {', '.join(settings.SUPPORTED_FORMATS)}")
    logger.info(f"  - 临时文件目录: {settings.TEMP_DIR}")

    # 初始化文档解析服务
    logger.info("初始化文档解析服务...")
    await unified_service.document_parse_service.initialize()

    logger.info("DeepRAG 统一服务启动完成!")

    yield

    # 关闭
    logger.info("DeepRAG 统一服务关闭中...")

    # 关闭文档解析服务
    await unified_service.document_parse_service.shutdown()

    # 关闭线程池
    executor.shutdown(wait=True)

    # 关闭进程池
    chunk_executor.shutdown(wait=True)

    # 清理临时文件
    temp_dir = Path(settings.TEMP_DIR)
    if temp_dir.exists():
        import shutil
        for child in temp_dir.iterdir():
            if child.name == "task_payloads":
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)

    logger.info("DeepRAG 统一服务已关闭")

# 创建FastAPI应用
app = FastAPI(
    title="DeepRAG 统一服务",
    description="在同一个服务地址下提供四个核心模块的API接口",
    version="1.0.0",
    lifespan=lifespan
)

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局变量
executor = ThreadPoolExecutor(max_workers=settings.MAX_WORKERS)
chunk_executor = ProcessPoolExecutor(max_workers=settings.CHUNK_PROCESS_WORKERS)
semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_TASKS)

# 统计信息
stats = {
    "start_time": datetime.now(),
    "total_requests": 0,
    "successful_requests": 0,
    "failed_requests": 0,
    "chunk_requests": 0,
    "embedding_requests": 0,
    "store_requests": 0,
}

# 注册所有路由
from routes.health import router as health_router
from routes.parse import router as parse_router
from routes.embed import router as embed_router
from routes.store import router as store_router
from routes.delete import router as delete_router
from routes.task import router as task_router
from routes.chunk import router as chunk_router
from routes.recall import router as recall_router

app.include_router(health_router)
app.include_router(parse_router)
app.include_router(embed_router)
app.include_router(store_router)
app.include_router(delete_router)
app.include_router(task_router)
app.include_router(chunk_router)
app.include_router(recall_router)


def main():
    """启动服务器"""
    logger.info("启动 DeepRAG 统一服务...")

    uvicorn.run(
        app,
        host=settings.HOST,
        port=settings.PORT,
        workers=1,
        reload=False,
        access_log=True,
        log_level="info"
    )

if __name__ == "__main__":
    main()
