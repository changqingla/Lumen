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

import copy
import re
from io import BytesIO

from PIL import Image

from core.nlp import tokenize, is_english
from core.nlp import rag_tokenizer
from deepdoc.parser import PptParser
from PyPDF2 import PdfReader as pdf2_read


class Ppt(PptParser):
    def __call__(self, fnm, from_page, to_page, callback=None):
        txts = super().__call__(fnm, from_page, to_page)

        callback(0.5, "Text extraction finished.")
        import aspose.slides as slides
        import aspose.pydrawing as drawing
        imgs = []
        with slides.Presentation(BytesIO(fnm)) as presentation:
            for i, slide in enumerate(presentation.slides[from_page: to_page]):
                buffered = BytesIO()
                slide.get_thumbnail(
                    0.5, 0.5).save(
                    buffered, drawing.imaging.ImageFormat.jpeg)
                imgs.append(Image.open(buffered))
        assert len(imgs) == len(
            txts), "Slides text and image do not match: {} vs. {}".format(len(imgs), len(txts))
        callback(0.9, "Image extraction finished")
        self.is_english = is_english(txts)
        return [(txts[i], imgs[i]) for i in range(len(txts))]


def chunk(filename, binary=None, from_page=0, to_page=100000,
          lang="Chinese", callback=None, **kwargs):
    """
    The supported file formats are pdf, pptx.
    Every page will be treated as a chunk. And the thumbnail of every page will be stored.
    PPT file will be parsed by using this method automatically, setting-up for every PPT file is not necessary.
    """
    eng = lang.lower() == "english"
    doc = {
        "docnm_kwd": filename,
        "title_tks": rag_tokenizer.tokenize(re.sub(r"\.[a-zA-Z]+$", "", filename))
    }
    doc["title_sm_tks"] = rag_tokenizer.fine_grained_tokenize(doc["title_tks"])
    res = []
    if re.search(r"\.pptx?$", filename, re.IGNORECASE):
        ppt_parser = Ppt()
        for pn, (txt, img) in enumerate(ppt_parser(
                filename if not binary else binary, from_page, 1000000, callback)):
            d = copy.deepcopy(doc)
            pn += from_page
            d["image"] = img
            d["page_num_int"] = [pn + 1]
            d["top_int"] = [0]
            d["position_int"] = [(pn + 1, 0, img.size[0], 0, img.size[1])]
            tokenize(d, txt, eng)
            res.append(d)
        return res

    raise NotImplementedError(
        "file type not supported yet(pptx supported)")


def extract_pages_to_images(file_path, output_dir="output_images", from_page=0, to_page=100000, 
                            dpi=50, callback=None):
    """
    将PPT/PPTX/PDF/XLS/XLSX文件按页提取为图片，保存到指定目录结构
    
    目录结构: output_dir/文件名/文件名_0001.png
    
    Args:
        file_path: 文件路径（支持PPT/PPTX/PDF/XLS/XLSX格式）
        output_dir: 输出根目录，默认为"output_images"
        from_page: 起始页码（从0开始）
        to_page: 结束页码
        dpi: 图片分辨率（默认50）
        callback: 进度回调函数 callback(progress, message)
        
    Returns:
        list: 导出的图片文件路径列表
        
    Example:
        >>> extract_pages_to_images("report.pdf", "output")
        # 将生成: output/report/report_0001.png, output/report/report_0002.png, ...
        
        >>> extract_pages_to_images("data.xlsx", "output")
        # Excel文件：每个工作表会生成一张图片
        # 将生成: output/data/data_0001.png, output/data/data_0002.png, ...
    """
    import os
    
    # 获取文件名（不含扩展名）
    filename_with_ext = os.path.basename(file_path)
    filename = re.sub(r"\.[a-zA-Z]+$", "", filename_with_ext)
    
    # 创建输出目录: output_dir/文件名/
    target_dir = os.path.join(output_dir, filename)
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
    
    exported_files = []
    
    def progress_wrapper(progress, msg=""):
        """内部进度回调包装"""
        if callback:
            callback(progress, msg)
    
    # 判断文件类型并处理
    if re.search(r"\.pptx?$", file_path, re.IGNORECASE):
        # 处理PPT/PPTX文件 - 通过PDF中间格式转换
        # 注意：LibreOffice的 --convert-to png 只能转换第一页，所以必须先转PDF
        if callback:
            callback(0, "开始处理PPT文件...")
        
        try:
            import subprocess
            import tempfile
            
            # 创建临时目录
            with tempfile.TemporaryDirectory() as temp_dir:
                if callback:
                    callback(0.1, "使用LibreOffice转换PPT为PDF...")
                
                # 第一步：使用LibreOffice将PPT转换为PDF
                cmd = [
                    'soffice',
                    '--headless',
                    '--nofirststartwizard',
                    '--norestore',
                    '--nologo',
                    '--convert-to', 'pdf',
                    '--outdir', temp_dir,
                    file_path
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                
                if result.returncode != 0:
                    error_msg = result.stderr if result.stderr else "未知错误"
                    raise RuntimeError(f"LibreOffice转换失败: {error_msg}")
                
                # 找到生成的PDF文件
                pdf_filename = os.path.splitext(os.path.basename(file_path))[0] + '.pdf'
                pdf_path = os.path.join(temp_dir, pdf_filename)
                
                if not os.path.exists(pdf_path):
                    raise RuntimeError(f"未找到转换后的PDF文件: {pdf_path}")
                
                if callback:
                    callback(0.3, f"PPT已转换为PDF，开始提取图片（使用 {dpi} DPI）...")
                
                # 第二步：使用pdf2image将PDF转换为高质量PNG
                from pdf2image import convert_from_path
                
                # 使用用户指定的DPI，如果太低则至少使用50 DPI
                actual_dpi = max(dpi, 50)
                
                images = convert_from_path(
                    pdf_path,
                    dpi=actual_dpi,
                    first_page=from_page + 1,
                    last_page=min(to_page, 10000),
                    fmt='png'
                )
                
                if callback:
                    callback(0.5, f"已提取 {len(images)} 页，开始保存...")
                
                # 保存每一页图片
                for i, img in enumerate(images):
                    page_num = from_page + i + 1
                    output_filename = f"{filename}_{page_num:04d}.png"
                    output_path = os.path.join(target_dir, output_filename)
                    
                    # 保存为PNG
                    img.save(output_path, "PNG")
                    exported_files.append(output_path)
                    
                    if callback:
                        progress = 0.5 + 0.5 * (i + 1) / len(images)
                        callback(progress, f"已处理 {i + 1}/{len(images)} 页")
            
        except FileNotFoundError:
            raise ImportError(
                "PPT/PPTX文件处理需要安装 LibreOffice 和 pdf2image:\n"
                "  - Ubuntu/Debian: \n"
                "    sudo apt-get install libreoffice poppler-utils\n"
                "    pip install pdf2image\n"
                "  - macOS: \n"
                "    brew install --cask libreoffice\n"
                "    brew install poppler\n"
                "    pip install pdf2image\n"
                "  - Windows: 从 https://www.libreoffice.org/ 下载安装"
            )
        
        if callback:
            callback(1.0, f"完成！共导出 {len(exported_files)} 张图片")
        
        return exported_files
    
    elif re.search(r"\.xlsx?$", file_path, re.IGNORECASE):
        # 处理Excel文件 - 通过PDF中间格式转换
        # 注意：每个工作表会成为PDF的一页
        if callback:
            callback(0, "开始处理Excel文件...")
        
        try:
            import subprocess
            import tempfile
            
            # 创建临时目录
            with tempfile.TemporaryDirectory() as temp_dir:
                if callback:
                    callback(0.1, "使用LibreOffice转换Excel为PDF...")
                
                # 第一步：使用LibreOffice将Excel转换为PDF
                cmd = [
                    'soffice',
                    '--headless',
                    '--nofirststartwizard',
                    '--norestore',
                    '--nologo',
                    '--convert-to', 'pdf',
                    '--outdir', temp_dir,
                    file_path
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                
                if result.returncode != 0:
                    error_msg = result.stderr if result.stderr else "未知错误"
                    raise RuntimeError(f"LibreOffice转换失败: {error_msg}")
                
                # 找到生成的PDF文件
                pdf_filename = os.path.splitext(os.path.basename(file_path))[0] + '.pdf'
                pdf_path = os.path.join(temp_dir, pdf_filename)
                
                if not os.path.exists(pdf_path):
                    raise RuntimeError(f"未找到转换后的PDF文件: {pdf_path}")
                
                if callback:
                    callback(0.3, f"Excel已转换为PDF，开始提取图片（使用 {dpi} DPI）...")
                
                # 第二步：使用pdf2image将PDF转换为高质量PNG
                from pdf2image import convert_from_path
                
                # 使用用户指定的DPI，如果太低则至少使用50 DPI
                actual_dpi = max(dpi, 50)
                
                images = convert_from_path(
                    pdf_path,
                    dpi=actual_dpi,
                    first_page=from_page + 1,
                    last_page=min(to_page, 10000),
                    fmt='png'
                )
                
                if callback:
                    callback(0.5, f"已提取 {len(images)} 个工作表，开始保存...")
                
                # 保存每一页图片（每个工作表）
                for i, img in enumerate(images):
                    page_num = from_page + i + 1
                    output_filename = f"{filename}_{page_num:04d}.png"
                    output_path = os.path.join(target_dir, output_filename)
                    
                    # 保存为PNG
                    img.save(output_path, "PNG")
                    exported_files.append(output_path)
                    
                    if callback:
                        progress = 0.5 + 0.5 * (i + 1) / len(images)
                        callback(progress, f"已处理 {i + 1}/{len(images)} 个工作表")
            
        except FileNotFoundError:
            raise ImportError(
                "Excel文件处理需要安装 LibreOffice 和 pdf2image:\n"
                "  - Ubuntu/Debian: \n"
                "    sudo apt-get install libreoffice poppler-utils\n"
                "    pip install pdf2image\n"
                "  - macOS: \n"
                "    brew install --cask libreoffice\n"
                "    brew install poppler\n"
                "    pip install pdf2image\n"
                "  - Windows: 从 https://www.libreoffice.org/ 下载安装"
            )
        
        if callback:
            callback(1.0, f"完成！共导出 {len(exported_files)} 张图片（{len(exported_files)} 个工作表）")
        
        return exported_files
    
    elif re.search(r"\.pdf$", file_path, re.IGNORECASE):
        # 处理PDF文件 - 使用pdf2image库（高质量、高效率）
        if callback:
            callback(0, "开始处理PDF文件...")
        
        try:
            from pdf2image import convert_from_path
        except ImportError:
            raise ImportError(
                "需要安装 pdf2image 库来处理PDF文件。\n"
                "安装方法:\n"
                "  pip install pdf2image\n"
                "  # Linux还需要: sudo apt-get install poppler-utils\n"
                "  # macOS还需要: brew install poppler\n"
                "  # Windows: 下载poppler并添加到PATH"
            )
        
        # 转换PDF为PIL Image对象列表
        images = convert_from_path(
            file_path,
            dpi=dpi,
            first_page=from_page + 1,  # pdf2image的页码从1开始
            last_page=min(to_page, 10000)
        )
        
        # 保存每一页图片
        for i, img in enumerate(images):
            page_num = from_page + i + 1
            # 生成文件名: 文件名_0001.png
            output_filename = f"{filename}_{page_num:04d}.png"
            output_path = os.path.join(target_dir, output_filename)
            
            img.save(output_path, "PNG")
            exported_files.append(output_path)
            
            # 更新进度
            if callback:
                progress = (i + 1) / len(images)
                callback(progress, f"已处理 {i + 1}/{len(images)} 页")
        
        if callback:
            callback(1.0, f"完成！共导出 {len(exported_files)} 张图片")
        
        return exported_files
    
    else:
        raise ValueError(f"不支持的文件格式: {filename_with_ext}，仅支持 PPT/PPTX/PDF/XLS/XLSX")
