#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全局HTTP客户端模块

提供异步HTTP客户端单例，实现连接池复用，提升HTTP请求性能
"""

import httpx
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class GlobalHTTPClient:
    """
    全局HTTP客户端单例
    
    提供异步HTTP客户端，内置连接池，自动复用HTTP连接
    """
    
    _instance: Optional[httpx.AsyncClient] = None
    _initialized = False
    
    @classmethod
    async def get_client(cls) -> httpx.AsyncClient:
        """
        获取全局异步HTTP客户端
        
        Returns:
            httpx.AsyncClient: 异步HTTP客户端实例
        """
        if cls._instance is None or not cls._initialized:
            cls._instance = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    timeout=120.0,      # 总超时
                    connect=10.0,       # 连接超时
                    read=60.0,          # 读取超时
                    write=30.0          # 写入超时
                ),
                limits=httpx.Limits(
                    max_keepalive_connections=50,  # 最大保持连接数
                    max_connections=100,           # 最大总连接数
                    keepalive_expiry=30.0          # 保持连接过期时间
                ),
                http2=False,  # 禁用HTTP/2（某些服务可能不支持）
                verify=False   # 禁用SSL验证（内网环境）
            )
            cls._initialized = True
            logger.info("全局HTTP客户端已创建，连接池配置: max_keepalive=50, max_connections=100")
        
        return cls._instance
    
    @classmethod
    async def close(cls):
        """关闭全局HTTP客户端"""
        if cls._instance is not None:
            await cls._instance.aclose()
            cls._instance = None
            cls._initialized = False
            logger.info("全局HTTP客户端已关闭")


# 便捷函数
async def get_http_client() -> httpx.AsyncClient:
    """获取全局HTTP客户端"""
    return await GlobalHTTPClient.get_client()


__all__ = ['GlobalHTTPClient', 'get_http_client']
