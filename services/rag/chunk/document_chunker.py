#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文档分块器

提供完整的文档分块功能。
它复用了整个 Ragflow 处理流水线，包括 OCR、版面识别、表格结构识别和智能分块策略。
"""

import sys
import logging

from typing import List, Dict, Any, Optional, Union
from pathlib import Path
from timeit import default_timer as timer

# 添加 DeepRAG 根目录到 Python 路径
current_dir = Path(__file__).parent.absolute()
DeepRAG_root = current_dir.parent
sys.path.insert(0, str(DeepRAG_root))

from core.app import (
    naive, book, presentation, manual, laws, qa, table,
     one, email, presentation_vision, ppt_md_parser
)

# 导入智能表格解析器（使用标准 import 方式，兼容 Cython 编译）
from core.app import ir_table

from core.utils import  ParserType  # 工具函数和解析器类型



class DocumentChunker:
    """
    基于 DeepRAG 的文档分块器

    这个类使用 DeepRAG 的先进文档理解算法提供完整的文档分块功能。
    支持多种文档类型和解析策略，能够智能识别文档结构并进行语义分块。

    主要特性：
    - 支持 PDF、Word、Excel、PowerPoint、Markdown 等多种格式
    - 智能版面识别和内容提取
    - 基于语义的分块策略
    - 表格和图像的结构化处理
    - 多种专业领域的解析器（学术论文、法律文档、技术手册等）
    """

    # 解析器工厂映射 - 与 DeepRAG 的 task_executor.py 保持一致
    PARSER_FACTORY = {
        "general": naive,           # 通用解析器
        ParserType.NAIVE: naive,    # 简单文本解析器
        ParserType.BOOK: book,      # 书籍解析器
        ParserType.PRESENTATION: presentation,  # 演示文稿解析器（传统OCR方式）
        ParserType.MANUAL: manual,  # 技术手册解析器
        ParserType.LAWS: laws,      # 法律文档解析器
        ParserType.QA: qa,          # 问答文档解析器
        ParserType.TABLE: table,    # 表格解析器（需要规范表格）
        ParserType.ONE: one,        # 单页文档解析器
        ParserType.EMAIL: email,    # 邮件解析器

        # 智能表格解析器（自动处理合并单元格，支持XLS/XLSX/CSV）
        "ir-table": ir_table,       # 智能表格解析器

        # 基于视觉模型的解析器（支持PPT/PPTX/PDF文件，需要特殊处理）
        "ppt": "vision_parser",   # 视觉模型解析（支持PPT/PPTX/PDF）

        # PPT Markdown 解析器（解析包含 ##PPT 标志的 Markdown 文件）
        "ppt_parser": ppt_md_parser,  # PPT Markdown 解析器
    }
    
    def __init__(self,
                 parser_type: str = "general",
                 chunk_token_num: int = 256,
                 delimiter: str = "\n。；！？",
                 language: str = "Chinese",
                 layout_recognize: str = "DeepDOC",
                 zoomin: int = 3,
                 from_page: int = 0,
                 to_page: int = 10000000,  # 修改为1000万，足够处理大多数Excel文件
                 # 新增：视觉模型配置（仅用于视觉解析器）
                 cv_model_config: Optional[Dict[str, Any]] = None,
                 vision_batch_size: int = 10):
        """
        初始化文档分块器

        Args:
            parser_type (str): 解析器类型 (general, paper, book, ppt_vision 等)
            chunk_token_num (int): 每个分块的最大 token 数量
            delimiter (str): 文本分割符，用于分块边界识别
            language (str): 文档语言 (Chinese/English)
            layout_recognize (str): 版面识别方法 (DeepDOC/Plain Text)
            zoomin (int): OCR 缩放因子，影响图像识别精度
            from_page (int): 起始页码（从0开始）
            to_page (int): 结束页码（默认1000万）
            cv_model_config (Optional[Dict]): 视觉模型配置（仅用于 *_vision 解析器）
                必需字段: model_factory, api_key
                可选字段: model_name, base_url, lang
            vision_batch_size (int): 视觉解析批量大小（默认10，表示一次并发处理10张图片）
        """
        self.parser_type = parser_type.lower()  # 统一转为小写
        self.chunk_token_num = chunk_token_num
        self.delimiter = delimiter
        self.language = language
        self.layout_recognize = layout_recognize
        self.zoomin = zoomin
        self.from_page = from_page
        self.to_page = to_page
        self.cv_model_config = cv_model_config
        self.vision_batch_size = vision_batch_size

        # 验证解析器类型是否支持
        if self.parser_type not in self.PARSER_FACTORY:
            raise ValueError(f"不支持的解析器类型: {parser_type}. "
                           f"支持的类型: {list(self.PARSER_FACTORY.keys())}")

        # 获取解析器类型标识
        parser_value = self.PARSER_FACTORY[self.parser_type]
        
        # 判断是否是视觉解析器
        if parser_value == "vision_parser":
            # 视觉解析器：需要 CV 模型配置
            if not cv_model_config:
                raise ValueError(
                    f"使用 {parser_type} 解析器需要提供 cv_model_config 参数，"
                    f"包括: model_factory, api_key 等"
                )
            
            # 创建 CV 模型实例
            self.cv_model = self._create_cv_model(cv_model_config)
            
            # 创建 PreFile 解析器实例
            self.chunker = presentation_vision.PreFile(
                cv_model=self.cv_model,
                batch_size=vision_batch_size
            )
            self.is_vision_parser = True
            
            logging.info(
                f"初始化视觉解析器: {self.parser_type}, "
                f"模型: {cv_model_config.get('model_factory')}/{cv_model_config.get('model_name')}, "
                f"批量大小: {vision_batch_size}"
            )
        else:
            # 传统解析器：直接使用模块
            self.chunker = parser_value
            self.is_vision_parser = False

        # 设置日志配置
        self._setup_logging()

        # 解析器配置参数（仅用于传统解析器）
        if not self.is_vision_parser:
            self.parser_config = {
                "chunk_token_num": self.chunk_token_num,  # 分块大小
                "delimiter": self.delimiter,              # 分割符
                "layout_recognize": self.layout_recognize # 版面识别方法
            }

        logging.info(f"文档分块器初始化完成，使用解析器: {self.parser_type}")
    
    def _create_cv_model(self, config: Dict[str, Any]):
        """
        根据配置创建 CV 模型实例
        
        Args:
            config: CV 模型配置字典
                必需字段: model_factory, api_key
                可选字段: model_name, base_url, lang
                
        Returns:
            CV 模型实例
            
        Raises:
            ValueError: 配置不正确或不支持的模型工厂
        """
        try:
            from core.llm import cv_model
            
            factory = config.get("model_factory") or config.get("factory")
            model_name = config.get("model_name", "default")
            api_key = config.get("api_key")
            base_url = config.get("base_url")
            lang = config.get("lang", self.language)
            
            if not factory or not api_key:
                raise ValueError("cv_model_config 必须包含 model_factory 和 api_key")
            
            # CV 模型工厂映射表（使用 cv_model.py 中定义的 _FACTORY_NAME）
            model_map = {
                # OpenAI 系列
                "OpenAI": cv_model.GptV4,
                "Azure-OpenAI": cv_model.AzureGptV4,
                
                # 国内大模型
                "Tongyi-Qianwen": cv_model.QWenCV,
                "Tencent Hunyuan": cv_model.HunyuanCV,
                "ZHIPU-AI": cv_model.Zhipu4V,
                "01.AI": cv_model.YiCV,
                "StepFun": cv_model.StepFunCV,
                "SILICONFLOW": cv_model.SILICONFLOWCV,
                
                # 国际大模型
                "Gemini": cv_model.GeminiCV,
                "Anthropic": cv_model.AnthropicCV,
                "xAI": cv_model.xAICV,
                "NVIDIA": cv_model.NvidiaCV,
                "TogetherAI": cv_model.TogetherAICV,
                "OpenRouter": cv_model.OpenRouterCV,
                "Google Cloud": cv_model.GoogleCV,
                
                # 本地部署
                "LM-Studio": cv_model.LmStudioCV,
                "LocalAI": cv_model.LocalAICV,
                "Xinference": cv_model.XinferenceCV,
                "GPUStack": cv_model.GPUStackCV,
                "VLLM": cv_model.OpenAI_APICV,
                "OpenAI-API-Compatible": cv_model.OpenAI_APICV,
                "Moonshot": cv_model.LocalCV,
            }
            
            model_class = model_map.get(factory)
            
            if not model_class:
                raise ValueError(
                    f"不支持的 CV 模型工厂: {factory}。"
                    f"支持的类型: {list(set(model_map.keys()))}"
                )
            
            # 创建模型实例
            model_kwargs = {
                "key": api_key,
                "model_name": model_name,
                "lang": lang
            }
            if base_url:
                model_kwargs["base_url"] = base_url
            
            logging.info(f"创建 CV 模型: {factory}/{model_name}")
            return model_class(**model_kwargs)
            
        except ImportError as e:
            logging.error(f"导入 CV 模型模块失败: {e}")
            raise ValueError(f"无法导入 CV 模型模块，请确保已安装相关依赖: {e}")
        except Exception as e:
            logging.error(f"创建 CV 模型失败: {e}")
            raise
    
    def _setup_logging(self):
        """
        设置日志配置

        配置日志格式和级别，用于跟踪分块处理过程
        """
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

    def _progress_callback(self, progress: float = None, msg: str = ""):
        """
        分块处理进度回调函数

        用于跟踪文档处理进度，提供实时状态反馈

        Args:
            progress (float): 进度百分比 (0.0-1.0)
            msg (str): 状态消息
        """
        if progress is not None:
            logging.info(f"处理进度: {progress:.1%} - {msg}")
        else:
            logging.info(f"状态: {msg}")
    
    def chunk_document(self,
                      file_path: Union[str, Path],
                      binary_data: Optional[bytes] = None,
                      return_full_content: bool = False,
                      **kwargs) -> Union[List[Dict[str, Any]], tuple]:
        """
        对文档进行分块

        Args:
            file_path (Union[str, Path]): 文档文件路径
            binary_data (Optional[bytes]): 文档二进制数据（可选，如果提供则不读取文件）
            return_full_content (bool): 是否返回完整内容（默认False）
            **kwargs: 特定解析器的额外参数

        Returns:
            如果 return_full_content=True: (List[Dict[str, Any]], str) - (分块列表, 完整内容)
            否则: List[Dict[str, Any]] - 分块列表
        """
        file_path = Path(file_path) if isinstance(file_path, str) else file_path
        filename = file_path.name

        logging.info(f"开始对文档进行分块处理: {filename}")
        logging.info(f"使用解析器: {self.parser_type}")

        start_time = timer()

        try:
            # 如果未提供二进制数据，则从文件读取
            if binary_data is None:
                if not file_path.exists():
                    raise FileNotFoundError(f"文件不存在: {file_path}")
                with open(file_path, 'rb') as f:
                    binary_data = f.read()

            full_content = ""  # 存储完整内容
            
            # 根据解析器类型选择不同的处理方式
            if self.is_vision_parser:
                # 视觉解析器：调用 PreFile.__call__
                chunk_params = {
                    'filename': str(file_path),              # 文件路径
                    'binary': binary_data,                   # 二进制数据
                    'from_page': self.from_page,            # 起始页码
                    'to_page': self.to_page,                # 结束页码
                    'lang': self.language,                   # 文档语言
                    'callback': self._progress_callback,     # 进度回调函数
                    'dpi': kwargs.get('dpi', 150),          # 图片DPI
                    'keep_images': kwargs.get('keep_images', False),  # 是否保留图片
                    'use_custom_prompt': kwargs.get('use_custom_prompt', False),  # 自定义提示词
                    'custom_prompt': kwargs.get('custom_prompt', None),  # 提示词内容
                }
                
                logging.info(f"使用视觉模型解析，批量大小: {self.vision_batch_size}")
                self._progress_callback(0.0, "开始视觉解析...")
                chunks = self.chunker(**chunk_params)
                
                # 视觉解析器：从chunks中提取完整内容
                if return_full_content:
                    full_content_parts = []
                    for chunk in chunks:
                        if isinstance(chunk, dict) and 'content_with_weight' in chunk:
                            full_content_parts.append(chunk['content_with_weight'])
                        elif isinstance(chunk, dict) and 'content_ltks' in chunk:
                            full_content_parts.append(chunk.get('content_ltks', ''))
                    full_content = "\n".join(full_content_parts)
                
            else:
                # 传统解析器：调用 chunk 函数
                chunk_params = {
                    'filename': filename,                    # 文件名
                    'binary': binary_data,                   # 二进制数据
                    'from_page': self.from_page,            # 起始页码
                    'to_page': self.to_page,                # 结束页码
                    'lang': self.language,                   # 文档语言
                    'callback': self._progress_callback,     # 进度回调函数
                    'parser_config': self.parser_config,     # 解析器配置
                    **kwargs                                 # 额外参数
                }
                
                # 只有 naive 解析器支持 return_full_content 参数
                # 其他解析器需要从 chunks 中提取完整内容
                is_naive_parser = self.parser_type in ["general", "naive"]
                if return_full_content and is_naive_parser:
                    chunk_params['return_full_content'] = True
                
                self._progress_callback(0.0, "开始文档处理...")
                result = self.chunker.chunk(**chunk_params)
                
                # 处理返回结果
                if return_full_content and is_naive_parser and isinstance(result, tuple):
                    chunks, full_content = result
                else:
                    chunks = result
                    # 对于非 naive 解析器，从 chunks 中提取完整内容
                    if return_full_content:
                        full_content_parts = []
                        for chunk in chunks:
                            if isinstance(chunk, dict):
                                # 尝试从不同字段提取内容
                                content = chunk.get('content_with_weight') or chunk.get('content_ltks') or chunk.get('content', '')
                                if content:
                                    full_content_parts.append(str(content))
                        full_content = "\n".join(full_content_parts)

            processing_time = timer() - start_time
            logging.info(f"文档分块处理完成，耗时 {processing_time:.2f}s")
            logging.info(f"生成了 {len(chunks)} 个分块")

            if return_full_content:
                return chunks, full_content
            return chunks

        except Exception as e:
            logging.error(f"文档分块处理出错 {filename}: {str(e)}")
            raise
    
    def chunk_batch(self,
                   file_paths: List[Union[str, Path]],
                   **kwargs) -> Dict[str, List[Dict[str, Any]]]:
        """
        批量处理多个文档的分块

        按顺序处理多个文档文件，对每个文件执行分块操作。
        即使某个文件处理失败，也会继续处理其他文件。

        Args:
            file_paths (List[Union[str, Path]]): 要处理的文件路径列表
            **kwargs: 分块处理的额外参数

        Returns:
            Dict[str, List[Dict[str, Any]]]: 文件路径到分块结果的映射字典
        """
        results = {}
        total_files = len(file_paths)

        logging.info(f"开始批量分块处理，共 {total_files} 个文件")

        for i, file_path in enumerate(file_paths):
            try:
                logging.info(f"正在处理文件 {i+1}/{total_files}: {file_path}")
                chunks = self.chunk_document(file_path, **kwargs)
                results[str(file_path)] = chunks
            except Exception as e:
                logging.error(f"处理文件失败 {file_path}: {str(e)}")
                results[str(file_path)] = []  # 失败时返回空列表

        logging.info(f"批量分块处理完成，已处理 {len(results)} 个文件")
        return results
