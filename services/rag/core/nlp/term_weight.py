#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
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
词汇权重计算模块

该模块提供词汇重要性权重计算功能，主要用于：
1. 文本预处理和分词
2. 词汇权重计算（基于TF-IDF、词性、命名实体等特征）
3. 停用词过滤和词汇合并
4. 命名实体识别和分类

核心算法：
- 结合词频(TF)、逆文档频率(IDF)、词性标注(POS)、命名实体识别(NER)
- 多维度权重融合，提供精确的词汇重要性评分
- 支持中英文混合文本处理

主要用于RAG系统中的关键词提取、查询重写、文档排序等场景。
"""

import logging
import math
import json
import re
import os
import numpy as np
from core.nlp import rag_tokenizer
from core.utils import get_project_base_directory


class Dealer:
    """
    词汇权重计算处理器类

    负责文本的预处理、分词、权重计算等功能。

    主要功能：
    1. 文本预处理和清洗
    2. 智能分词和词汇合并
    3. 多维度权重计算（TF-IDF + NER + POS）
    4. 停用词过滤
    5. 命名实体识别和分类

    权重计算基于以下特征：
    - 词频(Term Frequency)
    - 逆文档频率(Inverse Document Frequency)
    - 词性标注(Part-of-Speech Tagging)
    - 命名实体类型(Named Entity Recognition)
    """

    def __init__(self):
        """
        初始化词汇权重计算器

        加载必要的资源文件：
        1. 停用词列表 - 用于过滤无意义词汇
        2. 命名实体词典(ner.json) - 用于实体识别和分类
        3. 词频统计文件(term.freq) - 用于计算IDF权重
        """
        # 中文停用词集合 - 包含常见的无意义词汇
        # 这些词汇在权重计算时会被过滤掉
        self.stop_words = set(["请问",    # 疑问词
                               "您",      # 敬语代词
                               "你",      # 人称代词
                               "我",      # 人称代词
                               "他",      # 人称代词
                               "是",      # 系动词
                               "的",      # 结构助词
                               "就",      # 副词
                               "有",      # 动词
                               "于",      # 介词
                               "及",      # 连词
                               "即",      # 副词
                               "在",      # 介词
                               "为",      # 介词
                               "最",      # 副词
                               "有",      # 动词（重复，但保持原样）
                               "从",      # 介词
                               "以",      # 介词
                               "了",      # 助词
                               "将",      # 副词
                               "与",      # 介词
                               "吗",      # 疑问助词
                               "吧",      # 语气助词
                               "中",      # 方位词
                               "#",       # 特殊符号
                               "什么",    # 疑问代词
                               "怎么",    # 疑问副词
                               "哪个",    # 疑问代词
                               "哪些",    # 疑问代词
                               "啥",      # 疑问代词（口语）
                               "相关"])   # 形容词

        def load_dict(fnm):
            """
            加载词频统计文件

            文件格式：每行包含 "词汇\t频次" 或 "词汇"

            Args:
                fnm (str): 词频文件路径

            Returns:
                dict or set: 如果有频次信息返回字典，否则返回词汇集合
            """
            res = {}
            f = open(fnm, "r")
            while True:
                line = f.readline()
                if not line:
                    break
                # 解析每行：词汇\t频次
                arr = line.replace("\n", "").split("\t")
                if len(arr) < 2:
                    res[arr[0]] = 0  # 没有频次信息，设为0
                else:
                    res[arr[0]] = int(arr[1])  # 有频次信息

            # 计算总频次
            c = 0
            for _, v in res.items():
                c += v

            # 如果总频次为0，返回词汇集合；否则返回频次字典
            if c == 0:
                return set(res.keys())
            return res

        # 获取资源文件目录路径
        fnm = os.path.join(get_project_base_directory(), "core/res")

        # 初始化命名实体词典和词频统计字典
        self.ne, self.df = {}, {}

        # 加载命名实体识别词典
        try:
            self.ne = json.load(open(os.path.join(fnm, "ner.json"), "r"))
        except Exception:
            logging.warning("Load ner.json FAIL!")  # 命名实体词典加载失败

        # 加载词频统计文件
        try:
            self.df = load_dict(os.path.join(fnm, "term.freq"))
        except Exception:
            logging.warning("Load term.freq FAIL!")  # 词频文件加载失败

    def pretoken(self, txt, num=False, stpwd=True):
        """
        文本预处理和分词

        对输入文本进行清洗、分词和过滤处理。

        Args:
            txt (str): 输入文本
            num (bool): 是否保留数字，默认False（过滤单个数字）
            stpwd (bool): 是否过滤停用词，默认True

        Returns:
            list: 处理后的词汇列表

        处理步骤：
        1. 使用正则表达式清理标点符号和特殊字符
        2. 使用分词器进行分词
        3. 过滤停用词和单个数字
        4. 过滤标点符号（替换为"#"）
        5. 返回清洗后的词汇列表
        """
        # 定义需要过滤的标点符号和特殊字符的正则表达式
        # 包含中英文标点符号、特殊符号、货币符号等
        patt = [
            r"[~—\t @#%!<>,\.\?\":;'\{\}\[\]_=\(\)\|，。？》•●○↓《；‘’：“”【¥ 】…￥！、·（）×`&\\/「」\\]"
        ]

        # 文本替换规则（当前为空，可根据需要添加）
        # 格式：[(正则表达式, 替换字符串), ...]
        rewt = [
        ]

        # 应用文本替换规则
        for p, r in rewt:
            txt = re.sub(p, r, txt)

        res = []  # 存储处理后的词汇列表

        # 使用RAG分词器进行分词
        for t in rag_tokenizer.tokenize(txt).split():
            tk = t  # 当前处理的词汇

            # 过滤条件检查：
            # 1. 如果启用停用词过滤且词汇在停用词列表中
            # 2. 如果不保留数字且词汇是单个数字
            if (stpwd and tk in self.stop_words) or (
                    re.match(r"[0-9]$", tk) and not num):
                continue  # 跳过该词汇

            # 检查是否为标点符号或特殊字符
            for p in patt:
                if re.match(p, t):
                    tk = "#"  # 标记为特殊符号
                    break

            # 注释掉的转义处理（保留原代码逻辑）
            # 原本可能用于转义正则表达式中的特殊字符
            #tk = re.sub(r"([\+\\-])", r"\\\1", tk)

            # 只保留非特殊符号且非空的词汇
            if tk != "#" and tk:
                res.append(tk)

        return res

    def tokenMerge(self, tks):
        """
        智能词汇合并

        将相邻的短词汇（单字符或短英文）合并成更有意义的词组。

        Args:
            tks (list): 分词后的词汇列表

        Returns:
            list: 合并后的词汇列表

        合并策略：
        1. 识别单字符词汇或1-2位英文数字组合
        2. 将连续的短词汇合并（最多5个）
        3. 避免合并停用词
        4. 特殊处理首词合并情况
        """
        # 判断是否为单字符词汇或短英文数字组合
        def oneTerm(t): return len(t) == 1 or re.match(r"[0-9a-z]{1,2}$", t)

        res, i = [], 0
        while i < len(tks):
            j = i
            # 特殊情况：首词是单字符且下一个词是多字符非英文数字
            # 例如："多 工位" -> "多 工位"
            if i == 0 and oneTerm(tks[i]) and len(
                    tks) > 1 and (len(tks[i + 1]) > 1 and not re.match(r"[0-9a-zA-Z]", tks[i + 1])):
                res.append(" ".join(tks[0:2]))  # 合并前两个词
                i = 2
                continue

            # 寻找连续的短词汇序列
            while j < len(
                    tks) and tks[j] and tks[j] not in self.stop_words and oneTerm(tks[j]):
                j += 1

            # 如果找到多个连续的短词汇
            if j - i > 1:
                if j - i < 5:  # 少于5个词，全部合并
                    res.append(" ".join(tks[i:j]))
                    i = j
                else:  # 5个或更多词，只合并前两个
                    res.append(" ".join(tks[i:i + 2]))
                    i = i + 2
            else:
                # 单个词汇，直接添加
                if len(tks[i]) > 0:
                    res.append(tks[i])
                i += 1

        # 过滤空字符串
        return [t for t in res if t]

    def ner(self, t):
        """
        获取词汇的命名实体类型

        查询词汇在命名实体词典中的类型标签。

        Args:
            t (str): 输入词汇

        Returns:
            str: 命名实体类型标签，如果不存在则返回空字符串

        命名实体类型包括：
        - toxic: 有害内容
        - func: 功能词
        - corp: 公司名
        - loca: 地点名
        - sch: 学校名
        - stock: 股票名
        - firstnm: 人名
        """
        # 检查命名实体词典是否已加载
        if not self.ne:
            return ""

        # 查找词汇的命名实体类型
        res = self.ne.get(t, "")
        if res:
            return res

    def split(self, txt):
        """
        智能文本分割

        将文本分割成词汇列表，并智能合并相邻的英文词汇。

        Args:
            txt (str): 输入文本

        Returns:
            list: 分割后的词汇列表

        合并规则：
        1. 相邻的两个词汇都以英文字母结尾
        2. 两个词汇都不是功能词(func类型)
        3. 将相邻的英文词汇合并为一个词组

        例如："machine learning" -> ["machine learning"]
        而不是 ["machine", "learning"]
        """
        tks = []  # 存储分割后的词汇列表

        # 标准化空白字符并分割
        for t in re.sub(r"[ \t]+", " ", txt).split():
            # 检查是否需要与前一个词汇合并
            if tks and re.match(r".*[a-zA-Z]$", tks[-1]) and \
               re.match(r".*[a-zA-Z]$", t) and tks and \
               self.ne.get(t, "") != "func" and self.ne.get(tks[-1], "") != "func":
                # 合并条件：
                # 1. 已有词汇列表且前一个词汇以英文字母结尾
                # 2. 当前词汇以英文字母结尾
                # 3. 两个词汇都不是功能词
                tks[-1] = tks[-1] + " " + t  # 合并到前一个词汇
            else:
                # 不满足合并条件，作为新词汇添加
                tks.append(t)

        return tks

    def weights(self, tks, preprocess=True):
        """
        计算词汇权重

        基于多种特征计算每个词汇的重要性权重，包括：
        1. 词频(TF) - 词汇在文档中的出现频率
        2. 逆文档频率(IDF) - 词汇的稀有程度
        3. 命名实体类型 - 不同实体类型有不同权重
        4. 词性标注 - 名词、动词等有不同权重
        5. 技能词汇 - 特殊技能词汇有更高权重

        Args:
            tks (list): 词汇列表
            preprocess (bool): 是否进行预处理（分词和合并），默认True

        Returns:
            dict: 词汇到权重的映射字典 {词汇: 权重}

        权重计算公式：
        权重 = TF * IDF * NER权重 * POS权重 * 技能权重
        """
        # 技能词汇权重计算函数
        def skill(t):
            """计算技能词汇权重"""
            if t not in self.sk:
                return 1  # 非技能词汇，权重为1
            return 6  # 技能词汇，权重为6

        def ner(t):
            """计算命名实体权重"""
            # 数字序列（如价格、日期等）权重为2
            if re.match(r"[0-9,.]{2,}$", t):
                return 2
            # 1-2位英文字母权重很低（通常是缩写或无意义）
            if re.match(r"[a-z]{1,2}$", t):
                return 0.01
            # 不在命名实体词典中的词汇，默认权重为1
            if not self.ne or t not in self.ne:
                return 1
            # 不同命名实体类型的权重映射
            m = {"toxic": 2,     # 有害内容
                 "func": 1,      # 功能词
                 "corp": 3,      # 公司名
                 "loca": 3,      # 地点名
                 "sch": 3,       # 学校名
                 "stock": 3,     # 股票名
                 "firstnm": 1}   # 人名
            return m[self.ne[t]]

        def postag(t):
            """计算词性标注权重"""
            # 获取词性标注
            t = rag_tokenizer.tag(t)
            # 代词、连词、副词权重较低
            if t in set(["r", "c", "d"]):
                return 0.3
            # 地名、时间名词权重较高
            if t in set(["ns", "nt"]):
                return 3
            # 普通名词权重中等
            if t in set(["n"]):
                return 2
            # 数字序列权重中等
            if re.match(r"[0-9-]+", t):
                return 2
            # 其他词性默认权重
            return 1

        def freq(t):
            """计算词频权重（TF - Term Frequency）"""
            # 数字序列的词频权重
            if re.match(r"[0-9. -]{2,}$", t):
                return 3

            # 获取词汇的频率统计
            s = rag_tokenizer.freq(t)

            # 纯英文词汇且没有频率统计，给予较高权重（可能是专业术语）
            if not s and re.match(r"[a-z. -]+$", t):
                return 300

            # 没有频率统计的词汇，频率设为0
            if not s:
                s = 0

            # 对于长词汇（>=4字符）且没有频率统计的情况
            # 尝试细粒度分词，取最小频率的1/6作为权重
            if not s and len(t) >= 4:
                s = [tt for tt in rag_tokenizer.fine_grained_tokenize(t).split() if len(tt) > 1]
                if len(s) > 1:
                    s = np.min([freq(tt) for tt in s]) / 6.
                else:
                    s = 0

            # 返回频率权重，最小值为10
            return max(s, 10)

        def df(t):
            """计算文档频率权重（DF - Document Frequency）"""
            # 数字序列的文档频率权重
            if re.match(r"[0-9. -]{2,}$", t):
                return 5

            # 如果词汇在文档频率词典中，返回频率+3
            if t in self.df:
                return self.df[t] + 3
            # 纯英文词汇，给予较高权重
            elif re.match(r"[a-z. -]+$", t):
                return 300
            # 长词汇进行细粒度分词处理
            elif len(t) >= 4:
                s = [tt for tt in rag_tokenizer.fine_grained_tokenize(t).split() if len(tt) > 1]
                if len(s) > 1:
                    return max(3, np.min([df(tt) for tt in s]) / 6.)

            # 默认文档频率权重
            return 3

        def idf(s, N):
            """计算逆文档频率（IDF - Inverse Document Frequency）"""
            return math.log10(10 + ((N - s + 0.5) / (s + 0.5)))

        tw = []  # 存储词汇权重对的列表

        if not preprocess:
            # 不进行预处理的情况：直接计算权重
            # 计算基于词频的IDF权重（权重占30%）
            idf1 = np.array([idf(freq(t), 10000000) for t in tks])
            # 计算基于文档频率的IDF权重（权重占70%）
            idf2 = np.array([idf(df(t), 1000000000) for t in tks])
            # 综合权重计算：(0.3*IDF1 + 0.7*IDF2) * NER权重 * POS权重
            wts = (0.3 * idf1 + 0.7 * idf2) * \
                np.array([ner(t) * postag(t) for t in tks])
            wts = [s for s in wts]
            tw = list(zip(tks, wts))
        else:
            # 进行预处理的情况：对每个词汇进行分词和合并后计算权重
            for tk in tks:
                # 对词汇进行预处理：分词 -> 合并
                tt = self.tokenMerge(self.pretoken(tk, True))
                # 计算预处理后词汇的权重
                idf1 = np.array([idf(freq(t), 10000000) for t in tt])
                idf2 = np.array([idf(df(t), 1000000000) for t in tt])
                wts = (0.3 * idf1 + 0.7 * idf2) * \
                    np.array([ner(t) * postag(t) for t in tt])
                wts = [s for s in wts]
                tw.extend(zip(tt, wts))

        # 权重归一化：计算总权重
        S = np.sum([s for _, s in tw])
        # 返回归一化后的权重列表：[(词汇, 归一化权重), ...]
        return [(t, s / S) for t, s in tw]
