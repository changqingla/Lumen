#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 统一服务 - 向后兼容入口

此文件保持 `python api/api.py` 的启动方式兼容。
实际的应用定义和路由已拆分到 app.py 和 routes/ 目录中。
"""

from app import app, main
from chunk_worker import process_chunk_in_process

if __name__ == "__main__":
    main()
