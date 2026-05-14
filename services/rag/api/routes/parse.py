#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 统一服务 - 文档解析和分块路由
"""

import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form

from config import settings
from file_security import normalize_upload_filename
from schemas import ChunkRequest, UnifiedResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/chunk", response_model=UnifiedResponse)
async def chunk_document(
    file: UploadFile = File(...),
    parser_type: str = Form("auto"),
    chunk_token_num: int = Form(256),
    delimiter: str = Form("\n。；！？"),
    language: str = Form("Chinese"),
    layout_recognize: str = Form("DeepDOC"),
    zoomin: int = Form(3),
    from_page: int = Form(0),
    to_page: int = Form(100000),
    document_id: str = Form(None),
    
    # CV 模型配置（仅用于视觉解析器，可选）
    cv_model_factory: Optional[str] = Form(None),
    cv_model_name: Optional[str] = Form(None),
    cv_api_key: Optional[str] = Form(None),
    cv_base_url: Optional[str] = Form(None),
    
    # 视觉解析参数（可选）
    vision_dpi: int = Form(50),
    vision_batch_size: int = Form(10),
    vision_keep_images: bool = Form(False),
    vision_use_custom_prompt: bool = Form(False),
    vision_custom_prompt: Optional[str] = Form(None),
    
    # ir-table 解析器参数（仅当 parser_type 为 ir-table 时使用）
    ir_table_auto_unmerge: bool = Form(True),
    ir_table_keep_title: bool = Form(True),
    ir_table_unmerge_start_row: int = Form(2),
    ir_table_unmerge_end_row: Optional[int] = Form(None),
    ir_table_only_columns: Optional[str] = Form(None),
    ir_table_exclude_sheets: Optional[str] = Form(None),
    ir_table_include_sheets: Optional[str] = Form(None),
):
    """
    文档分块接口 - 支持传统解析器、视觉解析器、智能表格解析器和PPT Markdown解析器
    """
    from app import unified_service, stats

    start_time = time.time()
    stats["total_requests"] += 1
    stats["chunk_requests"] += 1

    try:
        # 验证文件
        safe_filename = normalize_upload_filename(file.filename)

        file_ext = Path(safe_filename).suffix.lower()
        if file_ext not in settings.SUPPORTED_FORMATS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件格式: {file_ext}。支持的格式: {', '.join(settings.SUPPORTED_FORMATS)}"
            )

        # 读取文件内容
        file_content = await file.read()

        # 检查文件大小
        if len(file_content) > settings.MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"文件大小超过限制 ({settings.MAX_FILE_SIZE / 1024 / 1024:.0f}MB)"
            )

        # 确定解析器类型
        if parser_type == "auto":
            parser_type = unified_service.detect_parser_type(safe_filename)
        
        # 检查是否是视觉解析器
        is_vision_parser = parser_type == "ppt"
        
        # 如果是视觉解析器，验证必需参数
        if is_vision_parser:
            if not cv_model_factory:
                raise HTTPException(
                    status_code=400,
                    detail=f"使用 {parser_type} 解析器需要提供 cv_model_factory 参数"
                )
            if not cv_api_key:
                raise HTTPException(
                    status_code=400,
                    detail=f"使用 {parser_type} 解析器需要提供 cv_api_key 参数"
                )
            
            logger.info(
                f"使用视觉解析器: {parser_type}, "
                f"模型: {cv_model_factory}/{cv_model_name or 'default'}, "
                f"批量大小: {vision_batch_size}"
            )

        # 创建请求对象
        request = ChunkRequest(
            parser_type=parser_type,
            chunk_token_num=chunk_token_num,
            delimiter=delimiter,
            language=language,
            layout_recognize=layout_recognize,
            zoomin=zoomin,
            from_page=from_page,
            to_page=to_page,
            document_id=document_id,
            cv_model_factory=cv_model_factory,
            cv_model_name=cv_model_name,
            cv_api_key=cv_api_key,
            cv_base_url=cv_base_url,
            vision_dpi=vision_dpi,
            vision_batch_size=vision_batch_size,
            vision_keep_images=vision_keep_images,
            vision_use_custom_prompt=vision_use_custom_prompt,
            vision_custom_prompt=vision_custom_prompt,
            ir_table_auto_unmerge=ir_table_auto_unmerge,
            ir_table_keep_title=ir_table_keep_title,
            ir_table_unmerge_start_row=ir_table_unmerge_start_row,
            ir_table_unmerge_end_row=ir_table_unmerge_end_row,
            ir_table_only_columns=ir_table_only_columns,
            ir_table_exclude_sheets=ir_table_exclude_sheets,
            ir_table_include_sheets=ir_table_include_sheets
        )

        # 调用分块服务
        result = await unified_service.process_chunk(file_content, safe_filename, request)

        processing_time = time.time() - start_time

        if result["success"]:
            stats["successful_requests"] += 1
            
            response_data = result.copy()
            response_data["parser_type"] = parser_type
            response_data["is_vision_parser"] = is_vision_parser
            if is_vision_parser:
                response_data["cv_model"] = f"{cv_model_factory}/{cv_model_name or 'default'}"
                response_data["vision_batch_size"] = vision_batch_size
                response_data["vision_dpi"] = vision_dpi
            
            return UnifiedResponse(
                success=True,
                message=f"成功分块文档 {safe_filename}，生成 {result.get('total_chunks', 0)} 个分块",
                data=response_data,
                processing_time=processing_time,
                timestamp=datetime.now().isoformat()
            )
        else:
            stats["failed_requests"] += 1
            raise HTTPException(status_code=500, detail=f"分块处理失败: {result.get('error', '未知错误')}")

    except HTTPException:
        stats["failed_requests"] += 1
        raise
    except Exception as e:
        stats["failed_requests"] += 1
        logger.error(f"分块处理失败: {e}")
        raise HTTPException(status_code=500, detail=f"分块处理失败: {str(e)}")


@router.post("/api/parse-document", response_model=UnifiedResponse)
async def parse_document(
    file: UploadFile = File(...),
    parser_type: str = Form("auto"),
    chunk_token_num: int = Form(256),
    delimiter: str = Form("\n。；！？"),
    language: str = Form("Chinese"),
    layout_recognize: str = Form("DeepDOC"),
    zoomin: int = Form(3),
    from_page: int = Form(0),
    to_page: int = Form(10000000),
    document_id: str = Form(None),
    model_factory: str = Form(...),
    model_name: str = Form(...),
    api_key: str = Form(None),
    base_url: str = Form(None),
    embedding_batch_size: int = Form(16),
    filename_embd_weight: float = Form(0.1),
    es_host: str = Form("http://localhost:9200"),
    index_name: str = Form(...),
    store_batch_size: int = Form(100),
    es_username: str = Form(None),
    es_password: str = Form(None),
    es_timeout: int = Form(60),
    priority: str = Form("normal", description="任务优先级: low, normal, high, urgent"),
    # 视觉解析参数（可选，仅当 parser_type="ppt" 时需要）
    cv_model_factory: Optional[str] = Form(None, description="CV模型工厂 (qwen/gptv4/gemini/claude)"),
    cv_model_name: Optional[str] = Form(None, description="CV模型名称"),
    cv_api_key: Optional[str] = Form(None, description="CV模型API密钥"),
    cv_base_url: Optional[str] = Form(None, description="CV模型服务地址（可选）"),
    vision_dpi: int = Form(50, description="图片DPI分辨率"),
    vision_batch_size: int = Form(10, description="批量并发处理大小"),
    vision_keep_images: bool = Form(False, description="是否保留图片对象"),
    vision_use_custom_prompt: bool = Form(False, description="是否使用自定义提示词"),
    vision_custom_prompt: Optional[str] = Form(None, description="自定义提示词内容"),
    # ir-table 解析器参数（仅当 parser_type 为 ir-table 时使用）
    ir_table_auto_unmerge: bool = Form(True, description="是否自动处理合并单元格"),
    ir_table_keep_title: bool = Form(True, description="是否保护第一行作为标题"),
    ir_table_unmerge_start_row: int = Form(2, description="取消合并的起始行"),
    ir_table_unmerge_end_row: Optional[int] = Form(None, description="取消合并的结束行"),
    ir_table_only_columns: Optional[str] = Form(None, description="仅处理指定列，逗号分隔"),
    ir_table_exclude_sheets: Optional[str] = Form(None, description="排除的工作表名称，逗号分隔"),
    ir_table_include_sheets: Optional[str] = Form(None, description="仅包含的工作表名称，逗号分隔")
):
    """
    文档解析接口（分块+向量化+存储一体化）
    
    异步处理文档，支持任务状态查询
    """
    from app import unified_service, stats

    start_time = time.time()
    stats["total_requests"] += 1
    
    try:
        # 验证文件
        safe_filename = normalize_upload_filename(file.filename)

        file_ext = Path(safe_filename).suffix.lower()
        if file_ext not in settings.SUPPORTED_FORMATS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件格式: {file_ext}。支持的格式: {', '.join(settings.SUPPORTED_FORMATS)}"
            )

        # 读取文件内容
        file_content = await file.read()

        # 检查文件大小
        if len(file_content) > settings.MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"文件大小超过限制 ({settings.MAX_FILE_SIZE / 1024 / 1024:.0f}MB)"
            )

        # 自动检测解析器类型
        if parser_type == "auto":
            parser_type = unified_service.detect_parser_type(safe_filename)
        
        # 验证视觉解析器参数
        is_vision_parser = parser_type == "ppt"
        if is_vision_parser:
            if not cv_model_factory or not cv_api_key:
                raise HTTPException(
                    status_code=400,
                    detail=f"使用 {parser_type} 解析器需要提供 cv_model_factory 和 cv_api_key 参数"
                )
            logger.info(f"文档解析任务使用视觉解析器: {parser_type}, 模型: {cv_model_factory}/{cv_model_name or 'default'}, 批量大小: {vision_batch_size}")

        # 构建配置
        chunk_config = {
            "parser_type": parser_type,
            "chunk_token_num": chunk_token_num,
            "delimiter": delimiter,
            "language": language,
            "layout_recognize": layout_recognize,
            "zoomin": zoomin,
            "from_page": from_page,
            "to_page": to_page,
            "document_id": document_id
        }
        
        # 添加视觉解析配置（如果是视觉解析器）
        if is_vision_parser:
            chunk_config["cv_model_config"] = {
                "model_factory": cv_model_factory,
                "model_name": cv_model_name or "default",
                "api_key": cv_api_key,
                "base_url": cv_base_url,
                "lang": language
            }
            chunk_config["vision_dpi"] = vision_dpi
            chunk_config["vision_batch_size"] = vision_batch_size
            chunk_config["vision_keep_images"] = vision_keep_images
            chunk_config["vision_use_custom_prompt"] = vision_use_custom_prompt
            chunk_config["vision_custom_prompt"] = vision_custom_prompt
        
        # 添加 ir-table 解析配置（如果是 ir-table 解析器）
        if parser_type == "ir-table":
            only_columns = None
            if ir_table_only_columns:
                only_columns = [col.strip() for col in ir_table_only_columns.split(',')]
            
            exclude_sheets = None
            if ir_table_exclude_sheets:
                exclude_sheets = [sheet.strip() for sheet in ir_table_exclude_sheets.split(',')]
            
            include_sheets = None
            if ir_table_include_sheets:
                include_sheets = [sheet.strip() for sheet in ir_table_include_sheets.split(',')]
            
            chunk_config["ir_table_config"] = {
                "auto_unmerge": ir_table_auto_unmerge,
                "keep_title": ir_table_keep_title,
                "unmerge_start_row": ir_table_unmerge_start_row,
                "unmerge_end_row": ir_table_unmerge_end_row,
                "only_columns": only_columns,
                "exclude_sheets": exclude_sheets,
                "include_sheets": include_sheets
            }
            logger.info(f"文档解析任务使用 ir-table 解析器: auto_unmerge={ir_table_auto_unmerge}, keep_title={ir_table_keep_title}")
        
        embedding_config = {
            "model_factory": model_factory,
            "model_name": model_name,
            "api_key": api_key,
            "base_url": base_url,
            "batch_size": embedding_batch_size,
            "filename_embd_weight": filename_embd_weight
        }
        
        store_config = {
            "es_host": es_host,
            "index_name": index_name,
            "batch_size": store_batch_size,
            "username": es_username,
            "password": es_password,
            "timeout": es_timeout
        }

        # 验证优先级参数
        valid_priorities = ["low", "normal", "high", "urgent"]
        if priority.lower() not in valid_priorities:
            raise HTTPException(
                status_code=400,
                detail=f"无效的优先级: {priority}。支持的优先级: {', '.join(valid_priorities)}"
            )

        # 创建任务并加入队列
        task_id = await unified_service.document_parse_service.create_task(
            filename=safe_filename,
            file_content=file_content,
            chunk_config=chunk_config,
            embedding_config=embedding_config,
            store_config=store_config,
            priority=priority.lower()
        )

        processing_time = time.time() - start_time
        stats["successful_requests"] += 1

        # 获取队列统计信息
        queue_stats = await unified_service.document_parse_service.get_queue_stats()

        return UnifiedResponse(
            success=True,
            message=f"文档解析任务已创建并加入队列，任务ID: {task_id}",
            data={
                "task_id": task_id,
                "filename": safe_filename,
                "file_size": len(file_content),
                "status": "queued",
                "priority": priority.lower(),
                "message": "任务已创建，正在排队等待处理",
                "queue_info": {
                    "queue_length": queue_stats.get("queue_length", 0),
                    "processing_count": queue_stats.get("processing_count", 0),
                    "max_concurrent_tasks": queue_stats.get("max_concurrent_tasks", 10)
                }
            },
            processing_time=processing_time,
            timestamp=datetime.now().isoformat()
        )

    except HTTPException:
        stats["failed_requests"] += 1
        raise
    except Exception as e:
        stats["failed_requests"] += 1
        logger.error(f"文档解析API失败: {e}")
        raise HTTPException(status_code=500, detail=f"文档解析失败: {str(e)}")
