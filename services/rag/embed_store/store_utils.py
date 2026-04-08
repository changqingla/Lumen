#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepRAG 存储工具模块

本模块提供用于在存储到 Elasticsearch 之前验证分块数据的完整性和有效性。
"""

from typing import List, Dict, Any

class ChunkValidator:
    """
    分块数据验证器
    
    用于在存储到 Elasticsearch 之前验证分块数据的完整性和有效性。
    主要验证以下内容：
    1. 必需字段是否存在
    2. 向量化数据是否正确
    3. 文本内容是否有效
    4. 数据格式是否符合要求
    """
    
    @staticmethod
    def validate_chunks(chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        验证分块数据在存储前的完整性和有效性
        
        此方法会检查每个分块是否包含必需的字段，向量化数据是否正确格式化，
        以及内容是否有效。返回详细的验证结果，包括错误和警告信息。
        
        Args:
            chunks (List[Dict[str, Any]]): 待验证的分块数据列表
                每个分块应包含：
                - content_with_weight: 分块文本内容（必需）
                - q_*_vec: 向量化数据（必需，格式：q_{维度}_vec）
                - docnm_kwd: 文档名称（可选）
                - 其他元数据字段（可选）
                
        Returns:
            Dict[str, Any]: 验证结果字典
                {
                    "valid": bool,           # 是否通过验证
                    "errors": List[str],     # 错误信息列表
                    "warnings": List[str],   # 警告信息列表  
                    "total_chunks": int,     # 总分块数量
                    "vector_fields": List[str]  # 发现的向量字段类型
                }
        """
        # 检查是否提供了分块数据
        if not chunks:
            return {
                "valid": False,
                "errors": ["未提供分块数据"],
                "warnings": []
            }
        
        warnings = []  # 警告列表（不影响存储，但需要注意）
        errors = []    # 错误列表（会阻止存储）
        
        # 定义必需字段列表
        required_fields = ["content_with_weight"]  # 分块文本内容是存储的基本要求
        vector_fields = []  # 收集所有发现的向量字段
        
        # 逐个验证每个分块
        for i, chunk in enumerate(chunks):
            # 检查必需字段是否存在
            for field in required_fields:
                if field not in chunk:
                    errors.append(f"分块 {i}: 缺少必需字段 '{field}'")
            
            # 查找并验证向量字段
            # 向量字段格式：q_{维度}_vec（如 q_1024_vec）
            chunk_vector_fields = [k for k in chunk.keys() if k.startswith("q_") and k.endswith("_vec")]
            if not chunk_vector_fields:
                errors.append(f"分块 {i}: 未找到向量字段（需要格式为 'q_*_vec' 的字段）")
            else:
                vector_fields.extend(chunk_vector_fields)
            
            # 验证文本内容质量
            content = chunk.get("content_with_weight", "")
            if not content or not content.strip():
                warnings.append(f"分块 {i}: 内容为空或仅包含空白字符")
            
            # 验证向量数据格式
            for vf in chunk_vector_fields:
                vector = chunk[vf]
                # 检查向量是否为列表格式
                if not isinstance(vector, list):
                    errors.append(f"分块 {i}: 向量字段 '{vf}' 不是列表格式")
                # 检查向量是否为空
                elif len(vector) == 0:
                    errors.append(f"分块 {i}: 向量字段 '{vf}' 为空列表")
                # 检查向量元素是否为数值类型
                elif not all(isinstance(v, (int, float)) for v in vector):
                    errors.append(f"分块 {i}: 向量字段 '{vf}' 包含非数值元素")
        
        # 检查向量字段一致性
        # 通常所有分块应该使用相同维度的向量
        unique_vector_fields = list(set(vector_fields))
        if len(unique_vector_fields) > 1:
            warnings.append(f"发现多种向量字段类型: {unique_vector_fields}（建议统一向量维度）")
        
        # 返回完整的验证结果
        return {
            "valid": len(errors) == 0,  # 只有在没有错误时才标记为有效
            "errors": errors,           # 所有错误信息
            "warnings": warnings,       # 所有警告信息
            "total_chunks": len(chunks), # 验证的分块总数
            "vector_fields": unique_vector_fields  # 发现的向量字段类型
        }
    