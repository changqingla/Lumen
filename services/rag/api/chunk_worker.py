#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Process-pool compatible document chunk worker."""

import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional


def process_chunk_in_process(
    file_path: str,
    parser_type: str,
    chunk_token_num: int,
    delimiter: str,
    language: str,
    layout_recognize: str,
    zoomin: int,
    from_page: int,
    to_page: int,
    document_id: Optional[str] = None,
    cv_model_config: Optional[Dict[str, Any]] = None,
    vision_kwargs: Optional[Dict[str, Any]] = None,
    ir_table_kwargs: Optional[Dict[str, Any]] = None,
):
    """
    在独立进程中执行文档分块处理。

    该函数必须保持在模块顶层，供 ProcessPoolExecutor pickle 序列化。
    独立进程拥有隔离的 NLTK 状态，可避免 WordNet 等组件的线程安全问题。
    """
    current_dir = Path(__file__).parent.absolute()
    project_root = current_dir.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    start_time = time.time()

    try:
        from chunk.document_chunker import DocumentChunker

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
            vision_batch_size=vision_kwargs.get("vision_batch_size", 10) if vision_kwargs else 10,
        )

        extra_kwargs: Dict[str, Any] = {}

        if vision_kwargs:
            extra_kwargs.update({
                "dpi": vision_kwargs.get("vision_dpi", 50),
                "keep_images": vision_kwargs.get("vision_keep_images", False),
                "use_custom_prompt": vision_kwargs.get("vision_use_custom_prompt", False),
                "custom_prompt": vision_kwargs.get("vision_custom_prompt", None),
            })

        if ir_table_kwargs:
            extra_kwargs.update({
                "auto_unmerge": ir_table_kwargs.get("auto_unmerge", True),
                "keep_title": ir_table_kwargs.get("keep_title", True),
                "unmerge_start_row": ir_table_kwargs.get("unmerge_start_row", 2),
                "unmerge_end_row": ir_table_kwargs.get("unmerge_end_row", None),
                "only_columns": ir_table_kwargs.get("only_columns", None),
                "exclude_sheets": ir_table_kwargs.get("exclude_sheets", None),
                "include_sheets": ir_table_kwargs.get("include_sheets", None),
            })

        result = document_chunker.chunk_document(
            file_path=file_path,
            return_full_content=True,
            **extra_kwargs,
        )

        if isinstance(result, tuple):
            chunks, full_content = result
        else:
            chunks = result
            full_content = ""

        for chunk in chunks:
            chunk["chunk_id"] = str(uuid.uuid4()).replace("-", "")[:16]
            if document_id:
                chunk["document_id"] = document_id
            chunk["available_int"] = 1

        return {
            "success": True,
            "chunks": chunks,
            "total_chunks": len(chunks),
            "full_content": full_content,
            "processing_time": time.time() - start_time,
            "parser_type": parser_type,
            "process_id": os.getpid(),
        }

    except Exception:
        return {
            "success": False,
            "chunks": None,
            "total_chunks": 0,
            "full_content": "",
            "processing_time": time.time() - start_time,
            "parser_type": parser_type,
            "error": "文档分块失败",
            "process_id": os.getpid(),
        }
