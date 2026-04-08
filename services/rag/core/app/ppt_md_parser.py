#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PPT Markdown 解析器

用于解析包含 ##PPT 标志的 Markdown 文件，按照 ##PPT 标志进行分块。
这种格式通常由 vision_parser 生成，每页 PPT 内容以 ##PPT 开头。
"""

import re
import copy
import logging
from io import BytesIO
from core.nlp import rag_tokenizer, tokenize


def chunk(filename, binary=None, from_page=0, to_page=100000,
          lang="Chinese", callback=None, **kwargs):
    """
    按 ##PPT 标志分块 Markdown 文件
    
    分块规则：
    1. 精确匹配 ##PPT（不允许空格）
    2. 每个 ##PPT 开始一个新的 chunk（包含标题行）
    3. 文件开头没有 ##PPT 的内容作为第一个独立块
    4. 使用 chunk 顺序作为页码（不从标题提取）
    
    Args:
        filename: 文件名
        binary: 文件二进制内容
        from_page: 起始页码（从0开始）
        to_page: 结束页码
        lang: 文档语言 (Chinese/English)
        callback: 进度回调函数
        **kwargs: 额外参数
        
    Returns:
        list: chunk 列表，每个 chunk 包含标准字段
    """
    if callback:
        callback(0.1, "开始解析 Markdown 文件...")
    
    # 判断是否是英文
    eng = lang.lower() == "english"
    
    # 初始化文档元数据
    doc = {
        "docnm_kwd": filename,
        "title_tks": rag_tokenizer.tokenize(re.sub(r"\.[a-zA-Z]+$", "", filename))
    }
    doc["title_sm_tks"] = rag_tokenizer.fine_grained_tokenize(doc["title_tks"])
    
    # 读取文件内容
    if binary:
        content = binary.decode('utf-8', errors='ignore')
    else:
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()

    # 将 \n 字符串替换为真正的换行符（兼容旧格式）
    content = content.replace('\\n', '\n')

    if callback:
        callback(0.3, "文件读取完成，开始分块...")

    # 按 ##PPT 标志分块
    # 支持两种模式：
    # 1. 换行符分隔：##PPT 在行首
    # 2. 直接分隔：遇到 ##PPT 就分块（适用于 ; 分隔或无换行的情况）

    # 使用正则表达式分割：遇到 ##PPT 就分块
    # 使用 lookahead 保留 ##PPT 标记
    parts = re.split(r'(?=##PPT)', content)

    # 过滤空块并清理
    chunks_raw = []
    for part in parts:
        part = part.strip()
        if part:
            chunks_raw.append(part)
    
    if callback:
        callback(0.6, f"分块完成，共 {len(chunks_raw)} 个块...")
    
    # 应用页码范围过滤
    chunks_raw = chunks_raw[from_page:min(to_page, len(chunks_raw))]
    
    # 转换为标准 chunk 格式
    res = []
    for idx, chunk_text in enumerate(chunks_raw):
        # 跳过空块
        chunk_text = chunk_text.strip()
        if not chunk_text:
            continue
        
        # 创建 chunk 字典
        d = copy.deepcopy(doc)
        
        # 页码（使用实际索引 + from_page）
        page_num = from_page + idx
        d["page_num_int"] = [page_num]
        d["top_int"] = [0]
        d["position_int"] = [(page_num, 0, 0, 0, 0)]
        
        # 标记使用了 ppt_parser
        d["ppt_parser"] = True
        
        # 提取标题（如果有）
        first_line = chunk_text.split('\n')[0]
        if re.match(r'^##PPT', first_line):
            d["ppt_title"] = first_line
        
        # 调用 tokenize 进行分词（会自动添加 content_with_weight, content_ltks, content_sm_ltks）
        tokenize(d, chunk_text, eng)
        
        res.append(d)
    
    if callback:
        callback(0.9, f"分词完成，生成 {len(res)} 个 chunks...")
    
    logging.info(f"PPT Markdown 解析完成: {filename}, 共 {len(res)} 个 chunks")
    
    if callback:
        callback(1.0, "解析完成")
    
    return res

