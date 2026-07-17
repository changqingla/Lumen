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

import re
from io import BytesIO
import numpy as np
import pandas as pd

from deepdoc.parser.utils import get_text
from core.nlp import rag_tokenizer, tokenize
from deepdoc.parser import ExcelParser


class Excel(ExcelParser):
    def __call__(self, fnm, binary=None, from_page=0,
                 to_page=10000000000, callback=None):
        if not binary:
            wb = Excel._load_excel_to_workbook(fnm)
        else:
            wb = Excel._load_excel_to_workbook(BytesIO(binary))
        total = 0
        for sheetname in wb.sheetnames:
            total += len(list(wb[sheetname].rows))

        res, fails, done = [], [], 0
        rn = 0
        for sheetname in wb.sheetnames:
            ws = wb[sheetname]
            rows = list(ws.rows)
            if not rows:
                continue
            headers = [cell.value for cell in rows[0]]
            missed = set([i for i, h in enumerate(headers) if h is None])
            headers = [
                cell.value for i,
                cell in enumerate(
                    rows[0]) if i not in missed]
            if not headers:
                continue
            data = []
            for i, r in enumerate(rows[1:]):
                rn += 1
                if rn - 1 < from_page:
                    continue
                if rn - 1 >= to_page:
                    break
                row = [
                    cell.value for ii,
                    cell in enumerate(r) if ii not in missed]
                if len(row) != len(headers):
                    fails.append(str(i))
                    continue
                data.append(row)
                done += 1
            if np.array(data).size == 0:
                continue
            res.append(pd.DataFrame(np.array(data), columns=headers))

        callback(0.3, ("Extract records: {}~{}".format(from_page + 1, min(to_page, from_page + rn)) + (
            f"{len(fails)} failure, line: %s..." % (",".join(fails[:3])) if fails else "")))
        return res




def chunk(filename, binary=None, from_page=0, to_page=10000000000,
          lang="Chinese", callback=None, **kwargs):
    """
    简化的表格解析器 - 与 ir-table 策略一致，但不处理合并单元格
    
    支持的文件格式：
    - Excel (.xlsx, .xls): 标准表格文件
    - CSV/TXT (.csv, .txt): 文本格式表格，默认使用 TAB 分隔符
    
    解析策略：
    - 第一行作为列标题
    - 每行数据生成一个 chunk
    - 只生成 content_ltks 字段，格式为 "列名:值; 列名:值; ..."
    - 不进行复杂的数据类型推断和字段映射
    """

    if re.search(r"\.xlsx?$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse Excel file.")
        excel_parser = Excel()
        dfs = excel_parser(
            filename,
            binary,
            from_page=from_page,
            to_page=to_page,
            callback=callback)
    elif re.search(r"\.(txt|csv)$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse CSV/TXT file.")
        txt = get_text(filename, binary)
        lines = txt.split("\n")
        fails = []
        headers = lines[0].split(kwargs.get("delimiter", "\t"))
        rows = []
        for i, line in enumerate(lines[1:]):
            if i < from_page:
                continue
            if i >= to_page:
                break
            row = [field for field in line.split(kwargs.get("delimiter", "\t"))]
            if len(row) != len(headers):
                fails.append(str(i))
                continue
            rows.append(row)

        callback(0.3, ("Extract records: {}~{}".format(from_page, min(len(lines), to_page)) + (
            f"{len(fails)} failure, line: %s..." % (",".join(fails[:3])) if fails else "")))

        dfs = [pd.DataFrame(np.array(rows), columns=headers)]

    else:
        raise NotImplementedError(
            "file type not supported yet(excel, text, csv supported)")

    res = []
    eng = lang.lower() == "english"
    
    for df in dfs:
        # 移除常见的索引列
        for n in ["id", "_id", "index", "idx"]:
            if n in df.columns:
                del df[n]
        
        clmns = df.columns.values
        
        # 逐行生成 chunk
        for ii, row in df.iterrows():
            # 基础元数据
            d = {
                "docnm_kwd": filename,
                "title_tks": rag_tokenizer.tokenize(re.sub(r"\.[a-zA-Z]+$", "", filename))
            }
            
            # 构建行文本：列名:值; 列名:值; ...
            row_txt = []
            for col_name in clmns:
                value = row[col_name]
                
                # 跳过空值
                if value is None:
                    continue
                if pd.isna(value):
                    continue
                
                # 转换为字符串并检查是否为空
                str_value = str(value).strip()
                if not str_value or str_value.lower() in ['nan', 'none', 'null']:
                    continue
                
                # 添加到行文本
                row_txt.append(f"{col_name}:{str_value}")
            
            # 如果这行没有任何内容，跳过
            if not row_txt:
                continue
            
            # 生成完整文本并分词
            full_text = "; ".join(row_txt)
            tokenize(d, full_text, eng)
            res.append(d)

    callback(0.9, f"Generated {len(res)} chunks from table data.")
    return res
