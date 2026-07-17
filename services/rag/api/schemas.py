#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 统一服务 - 请求/响应模型
"""

from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field


class ChunkRequest(BaseModel):
    """文档分块请求模型"""
    parser_type: str = Field(default="auto", description="解析器类型 (general/paper/ppt/ir-table等)")
    chunk_token_num: int = Field(default=256, ge=1, le=2048, description="每个分块的最大token数")
    delimiter: str = Field(default="\n。；！？", description="文本分割符")
    language: str = Field(default="Chinese", description="文档语言")
    layout_recognize: str = Field(default="DeepDOC", description="布局识别方法")
    zoomin: int = Field(default=3, ge=1, le=10, description="OCR缩放因子")
    from_page: int = Field(default=0, ge=0, description="起始页码")
    to_page: int = Field(default=100000, ge=1, description="结束页码")
    document_id: Optional[str] = Field(default=None, description="文档ID")
    
    # 新增：CV 模型配置（仅当 parser_type 为 *_vision 时必需）
    cv_model_factory: Optional[str] = Field(default=None, description="CV模型工厂 (qwen/gptv4/gemini/claude)")
    cv_model_name: Optional[str] = Field(default=None, description="CV模型名称")
    cv_api_key: Optional[str] = Field(default=None, description="CV模型API密钥")
    cv_base_url: Optional[str] = Field(default=None, description="CV模型服务地址（可选）")
    
    # 新增：视觉解析参数（可选）
    vision_dpi: int = Field(default=50, ge=10, le=300, description="图片DPI分辨率")
    vision_batch_size: int = Field(default=10, ge=1, le=20, description="批量并发处理大小")
    vision_keep_images: bool = Field(default=False, description="是否保留图片对象")
    vision_use_custom_prompt: bool = Field(default=False, description="是否使用自定义提示词")
    vision_custom_prompt: Optional[str] = Field(default=None, description="自定义提示词内容")
    
    # 新增：ir-table 解析器参数（仅当 parser_type 为 ir-table 时使用）
    ir_table_auto_unmerge: bool = Field(default=True, description="是否自动处理合并单元格")
    ir_table_keep_title: bool = Field(default=True, description="是否保护第一行作为标题")
    ir_table_unmerge_start_row: int = Field(default=2, ge=1, description="取消合并的起始行（默认从第2行开始）")
    ir_table_unmerge_end_row: Optional[int] = Field(default=None, description="取消合并的结束行（默认到最后一行）")
    ir_table_only_columns: Optional[str] = Field(default=None, description="仅处理指定列，逗号分隔，如'A,B,C'")
    ir_table_exclude_sheets: Optional[str] = Field(default=None, description="排除的工作表名称，逗号分隔")
    ir_table_include_sheets: Optional[str] = Field(default=None, description="仅包含的工作表名称，逗号分隔")


class EmbeddingRequest(BaseModel):
    """向量化请求模型"""
    chunks: List[Dict[str, Any]] = Field(..., description="文档分块列表")
    model_factory: str = Field(..., description="模型工厂名称")
    model_name: str = Field(..., description="模型名称")
    api_key: Optional[str] = Field(None, description="API 密钥")
    base_url: Optional[str] = Field(None, description="服务端点 URL")
    batch_size: int = Field(16, description="批处理大小")
    filename_embd_weight: float = Field(0.1, description="文件名嵌入权重")

class StoreRequest(BaseModel):
    """存储请求模型"""
    chunks: List[Dict[str, Any]] = Field(..., description="分块数据列表")
    es_host: str = Field(default="http://localhost:9200", description="Elasticsearch 地址")
    index_name: str = Field(default="deeprag_vectors", description="索引名称")
    batch_size: int = Field(default=100, ge=1, le=1000, description="批量大小")
    username: Optional[str] = Field(default=None, description="ES 用户名")
    password: Optional[str] = Field(default=None, description="ES 密码")
    timeout: int = Field(default=60, ge=10, le=300, description="超时时间(秒)")

class RecallRequest(BaseModel):
    """召回请求模型"""
    question: str = Field(..., description="查询问题")
    index_names: List[str] = Field(default=["deeprag_vectors"], description="ES索引名称列表")
    es_host: str = Field(default="http://localhost:9200", description="Elasticsearch地址")
    page: int = Field(default=1, ge=1, description="页码")
    top_n: int = Field(default=8, ge=1, le=10000, description="返回结果数量")
    similarity_threshold: float = Field(default=0.1, ge=0.0, le=1.0, description="相似度阈值")
    vector_similarity_weight: float = Field(default=0.3, ge=0.0, le=1.0, description="向量相似度权重")
    top_k: int = Field(default=1024, ge=1, le=10000, description="向量召回top-k数量")
    highlight: bool = Field(default=True, description="是否高亮")

    # 向量化模型配置
    model_factory: str = Field(default="Tongyi-Qianwen", description="向量化模型工厂")
    model_name: str = Field(default="text-embedding-v4", description="向量化模型名称")
    model_base_url: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode/v1", description="向量化模型服务地址")
    api_key: Optional[str] = Field(default=None, description="API密钥")

    # 重排序模型配置
    rerank_factory: Optional[str] = Field(default=None, description="重排序模型工厂")
    rerank_model_name: Optional[str] = Field(default=None, description="重排序模型名称")
    rerank_base_url: Optional[str] = Field(default=None, description="重排序模型服务地址")
    rerank_api_key: Optional[str] = Field(default=None, description="重排序模型API密钥")

    # 其他配置
    doc_ids: Optional[List[str]] = Field(default=None, description="指定文档ID列表")
    timeout: int = Field(default=600, ge=10, le=3600, description="超时时间(秒)")

class ChunkEditRequest(BaseModel):
    """文档块编辑请求模型"""
    chunk_id: str = Field(..., description="要编辑的块ID")
    es_host: str = Field(default="http://localhost:9200", description="Elasticsearch地址")
    index_name: str = Field(..., description="ES索引名称")
    
    # 原始内容字段 (只需要提供要更新的内容，系统会自动分词处理)
    content: Optional[str] = Field(None, description="新的文档内容（原始内容，系统会自动分词和处理）")
    
    # 控制字段
    available_int: int = Field(default=1, ge=0, le=1, description="是否启用 (0:禁用, 1:启用)")
    
    # 向量化配置
    model_factory: str = Field(..., description="模型工厂名称")
    model_name: str = Field(..., description="模型名称")
    api_key: Optional[str] = Field(None, description="API密钥")
    base_url: Optional[str] = Field(None, description="服务端点URL")
    batch_size: int = Field(default=1, description="批处理大小")
    filename_embd_weight: float = Field(default=0.1, description="文件名嵌入权重")
    
    # ES连接配置
    username: Optional[str] = Field(None, description="ES用户名")
    password: Optional[str] = Field(None, description="ES密码")
    timeout: int = Field(default=60, description="超时时间(秒)")

class ChunkBatchEditRequest(BaseModel):
    """批量文档块编辑请求模型"""
    chunks: List[Dict[str, Any]] = Field(..., description="要编辑的块列表，每个块包含chunk_id、content、available_int等字段")
    es_host: str = Field(default="http://localhost:9200", description="Elasticsearch地址")
    index_name: str = Field(..., description="ES索引名称")

    # 向量化配置
    model_factory: str = Field(..., description="模型工厂名称")
    model_name: str = Field(..., description="模型名称")
    api_key: Optional[str] = Field(None, description="API密钥")
    base_url: Optional[str] = Field(None, description="服务端点URL")
    batch_size: int = Field(default=16, description="批处理大小")
    filename_embd_weight: float = Field(default=0.1, description="文件名嵌入权重")

    # ES连接配置
    username: Optional[str] = Field(None, description="ES用户名")
    password: Optional[str] = Field(None, description="ES密码")
    timeout: int = Field(default=60, description="超时时间(秒)")

class DocumentDeleteRequest(BaseModel):
    """文档删除请求模型"""
    document_id: str = Field(..., description="要删除的文档ID")
    es_host: str = Field(default="http://localhost:9200", description="Elasticsearch地址")
    index_name: str = Field(..., description="ES索引名称")
    username: Optional[str] = Field(None, description="ES用户名")
    password: Optional[str] = Field(None, description="ES密码")
    timeout: int = Field(default=60, description="超时时间(秒)")

class TaskStatusResponse(BaseModel):
    """任务状态响应模型"""
    success: bool = Field(description="是否成功")
    task_id: Optional[str] = Field(None, description="任务ID")
    status: Optional[str] = Field(None, description="任务状态")
    progress: Optional[float] = Field(None, description="任务进度 (0.0-1.0)")
    message: Optional[str] = Field(None, description="状态消息")
    data: Optional[Dict[str, Any]] = Field(None, description="任务数据")
    timestamp: str = Field(description="时间戳")

class UnifiedResponse(BaseModel):
    """统一响应格式"""
    success: bool = Field(description="是否成功")
    message: str = Field(description="响应消息")
    data: Optional[Dict[str, Any]] = Field(default=None, description="响应数据")
    processing_time: float = Field(description="处理时间（秒）")
    timestamp: str = Field(description="时间戳")
