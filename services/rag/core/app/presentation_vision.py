#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

"""
基于视觉模型的PPT/PDF/Excel文件处理模块

此模块使用视觉模型（如GPT-4V）来理解和提取PPT/PDF/Excel页面的内容，
相比传统的OCR方式，能更好地理解图表、布局和视觉信息。

支持的文件格式：
- PPT/PPTX: 演示文稿文件
- PDF: 便携式文档格式
- XLS/XLSX: Excel电子表格（每个工作表会被转换为一页）
"""

import copy
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

from core.nlp import tokenize, rag_tokenizer
from core.app.presentation import extract_pages_to_images


class PreFile:
    """
    基于视觉模型的PPT/PDF/Excel文件解析器
    
    使用流程：
    1. 将PPT/PDF/Excel文件按页转换为图片
    2. 使用视觉模型（CV Model）理解每页图片内容
    3. 生成结构化的chunk数据，可用于RAG检索
    
    支持格式：
    - PPT/PPTX: 演示文稿（每页一张图）
    - PDF: 文档（每页一张图）
    - XLS/XLSX: 电子表格（每个工作表一张图）
    """
    
    def __init__(self, cv_model, batch_size=10):
        """
        初始化PreFile解析器
        
        Args:
            cv_model: 视觉模型实例，需要有 describe(image, prompt=None) 方法
                     例如: GptV4, QWenCV, GeminiCV 等
            batch_size: 批量处理大小（默认10），表示一次并发处理多少张图片
        """
        self.cv_model = cv_model
        self.batch_size = max(1, min(batch_size, 20))  # 限制在 1-20 之间
        
    def __call__(self, filename, binary=None, from_page=0, to_page=100000,
                 lang="Chinese", callback=None, dpi=150, use_custom_prompt=False,
                 custom_prompt=None, keep_images=False, **kwargs):
        """
        处理PPT/PDF/Excel文件，生成chunks
        
        Args:
            filename: 文件路径（支持PPT/PPTX/PDF/XLS/XLSX）
            binary: 文件二进制数据（可选，暂不支持）
            from_page: 起始页码（从0开始）
            to_page: 结束页码
            lang: 语言类型，"Chinese" 或 "English"
            callback: 进度回调函数 callback(progress, message)
            dpi: 图片DPI分辨率（默认150）
            use_custom_prompt: 是否使用自定义提示词
            custom_prompt: 自定义提示词内容
            keep_images: 是否在chunk中保留图片对象（默认False以节省内存）
            **kwargs: 其他参数
            
        Returns:
            list: chunk列表，每个chunk是一个字典，包含页面信息和视觉模型提取的内容
            
        Note:
            - Excel文件：每个工作表会被转换为一页
        """
        if callback:
            callback(0, "开始处理文件...")
        
        # 第一步：提取图片到临时目录
        with tempfile.TemporaryDirectory() as temp_dir:
            if callback:
                callback(0.1, "正在将文件转换为图片...")
            
            try:
                image_files = extract_pages_to_images(
                    file_path=filename,
                    output_dir=temp_dir,
                    from_page=from_page,
                    to_page=to_page,
                    dpi=dpi,
                    callback=self._wrap_callback(callback, 0.1, 0.4)
                )
            except Exception as e:
                raise RuntimeError(f"图片提取失败: {e}")
            
            if not image_files:
                raise ValueError("未能提取任何图片")
            
            if callback:
                callback(0.4, f"已提取 {len(image_files)} 页，开始使用视觉模型分析...")
            
            # 初始化文档元数据
            import os
            file_name_only = os.path.basename(filename)  # 只取文件名，不要路径
            eng = lang.lower() == "english"
            doc = {
                "docnm_kwd": file_name_only,
                "title_tks": rag_tokenizer.tokenize(re.sub(r"\.[a-zA-Z]+$", "", file_name_only))
            }
            doc["title_sm_tks"] = rag_tokenizer.fine_grained_tokenize(doc["title_tks"])
            
            # 第二步：批量并发处理图片
            res = []
            total_images = len(image_files)
            
            # 按批次处理图片
            for batch_start in range(0, total_images, self.batch_size):
                batch_end = min(batch_start + self.batch_size, total_images)
                batch_paths = image_files[batch_start:batch_end]
                
                if callback:
                    progress = 0.4 + 0.6 * (batch_start / total_images)
                    callback(progress, f"正在并发处理第 {batch_start+1}-{batch_end} 页...")
                
                # 批量并发处理这一批图片
                batch_results = self._process_batch_concurrent(
                    batch_paths=batch_paths,
                    start_page_num=from_page + batch_start,
                    doc=doc,
                    eng=eng,
                    keep_images=keep_images,
                    use_custom_prompt=use_custom_prompt,
                    custom_prompt=custom_prompt,
                    callback=callback,
                    batch_progress_start=0.4 + 0.6 * (batch_start / total_images),
                    batch_progress_end=0.4 + 0.6 * (batch_end / total_images)
                )
                
                res.extend(batch_results)
            
            if callback:
                callback(1.0, f"完成！共处理 {len(res)} 页")
            
            return res
    
    def _process_batch_concurrent(self, batch_paths, start_page_num, doc, eng,
                                   keep_images, use_custom_prompt, custom_prompt,
                                   callback=None, batch_progress_start=0, batch_progress_end=1):
        """
        批量并发处理一批图片
        
        使用线程池并发调用视觉模型API（IO密集型任务），
        但保证返回结果的顺序与输入图片顺序一致。
        
        Args:
            batch_paths: 图片路径列表
            start_page_num: 起始页码
            doc: 文档基础元数据
            eng: 是否为英文
            keep_images: 是否保留图片对象
            use_custom_prompt: 是否使用自定义提示词
            custom_prompt: 自定义提示词
            callback: 进度回调函数
            batch_progress_start: 批次起始进度
            batch_progress_end: 批次结束进度
            
        Returns:
            list: 处理后的chunk列表，顺序与输入一致
        """
        batch_size = len(batch_paths)
        results = [None] * batch_size  # 预分配结果数组，保证顺序
        
        def process_single_image(idx, img_path):
            """处理单张图片（在线程中执行）"""
            try:
                page_num = start_page_num + idx + 1
                
                with Image.open(img_path) as img:
                    width, height = img.size
                    
                    # 复制图片以避免在 with 上下文外使用已关闭的图片
                    img_copy = img.copy()
                    # 转换RGBA为RGB
                    if img_copy.mode == 'RGBA':
                        img_copy = img_copy.convert('RGB')
                    
                    # 调用视觉模型（并发执行）
                    # 注意：直接传递PIL Image对象，cv_model.describe内部会转换为base64
                    content, token_count = self.cv_model.describe(
                        img_copy,
                        prompt=custom_prompt if (use_custom_prompt and custom_prompt) else None
                    )
                    
                    # 创建chunk
                    d = copy.deepcopy(doc)
                    d["page_num_int"] = [page_num]
                    d["top_int"] = [0]
                    d["position_int"] = [(page_num, 0, width, 0, height)]
                    
                    # 保留图片对象（如果需要）
                    if keep_images:
                        # 重新读取以避免关闭问题
                        with Image.open(img_path) as img_copy:
                            d["image"] = img_copy.copy()
                    
                    # 添加视觉模型提取的内容
                    d["vision_extracted"] = True
                    d["vision_token_count"] = token_count
                    
                    # 分词
                    tokenize(d, content, eng)
                    
                    return idx, d, None
                    
            except Exception as e:
                # 返回错误信息
                page_num = start_page_num + idx + 1
                return idx, None, f"处理第 {page_num} 页失败: {str(e)}"
        
        # 使用线程池并发处理（视觉模型API调用是IO密集型）
        with ThreadPoolExecutor(max_workers=self.batch_size) as executor:
            # 提交所有任务
            future_to_idx = {
                executor.submit(process_single_image, idx, img_path): idx
                for idx, img_path in enumerate(batch_paths)
            }
            
            # 收集结果（保持顺序）
            completed_count = 0
            for future in as_completed(future_to_idx):
                idx, chunk_data, error = future.result()
                
                if error:
                    # 创建错误chunk
                    page_num = start_page_num + idx + 1
                    error_chunk = copy.deepcopy(doc)
                    error_chunk["page_num_int"] = [page_num]
                    error_chunk["top_int"] = [0]
                    error_chunk["position_int"] = [(page_num, 0, 0, 0, 0)]
                    error_chunk["error"] = error
                    error_chunk["vision_extracted"] = False
                    tokenize(error_chunk, f"Error: {error}", eng)
                    results[idx] = error_chunk
                else:
                    results[idx] = chunk_data
                
                # 更新进度
                completed_count += 1
                if callback:
                    progress = batch_progress_start + (batch_progress_end - batch_progress_start) * (completed_count / batch_size)
                    callback(progress, f"已完成 {start_page_num + completed_count} 页")
        
        return results
    
    def _wrap_callback(self, callback, start_progress, end_progress):
        """
        包装回调函数，将进度映射到指定区间
        
        Args:
            callback: 原始回调函数
            start_progress: 起始进度（0-1）
            end_progress: 结束进度（0-1）
            
        Returns:
            包装后的回调函数
        """
        if not callback:
            return None
        
        def wrapped_callback(progress, msg):
            if isinstance(progress, (int, float)):
                mapped_progress = start_progress + (end_progress - start_progress) * progress
                callback(mapped_progress, msg)
            else:
                callback(progress, msg)
        
        return wrapped_callback
