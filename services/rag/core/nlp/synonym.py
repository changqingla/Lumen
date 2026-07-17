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
同义词处理模块

该模块提供同义词查找功能，支持：
1. 从本地JSON文件加载同义词词典
2. 从Redis实时加载同义词词典
3. 使用NLTK WordNet进行英文同义词查找
4. 支持中文同义词查找

主要用于RAG系统中的查询扩展，通过同义词提高检索召回率。
"""

import logging
import json
import os
import time
import re
import sys
from pathlib import Path

# 添加项目根目录到Python路径
current_dir = Path(__file__).parent.absolute()
project_root = current_dir.parent.parent  # 回到DeepRAG_SRC目录
sys.path.insert(0, str(project_root))

# 导入项目模块
from core.utils import get_project_base_directory  # noqa: E402

# 导入外部模块
from nltk.corpus import wordnet  # noqa: E402


class Dealer:
    """
    同义词处理器类

    负责管理和查找同义词，支持多种数据源：
    1. 本地JSON文件（静态同义词词典）
    2. Redis缓存（动态同义词词典）
    3. NLTK WordNet（英文同义词）

    特性：
    - 自动加载和刷新同义词词典
    - 支持实时更新（通过Redis）
    - 英文和中文同义词查找
    - 查找频率控制和缓存机制
    """

    def __init__(self, redis=None):
        """
        初始化同义词处理器

        Args:
            redis: Redis连接对象，用于实时同义词更新。如果为None，则禁用实时更新功能
        """
        # 查找次数计数器，用于控制Redis刷新频率
        self.lookup_num = 100000000

        # 上次加载时间戳，用于控制刷新间隔
        self.load_tm = time.time() - 1000000

        # 同义词词典，存储词汇到同义词列表的映射
        self.dictionary = None

        # 构建同义词文件路径
        path = os.path.join(get_project_base_directory(), "core/res", "synonym.json")

        # 尝试加载本地同义词文件
        try:
            self.dictionary = json.load(open(path, 'r'))
        except Exception:
            logging.warning("Missing synonym.json")  # 缺少同义词文件
            self.dictionary = {}

        # 检查Redis连接状态
        if not redis:
            logging.warning(
                "Realtime synonym is disabled, since no redis connection.")  # 实时同义词功能已禁用，因为没有Redis连接

        # 检查词典加载状态
        if not len(self.dictionary.keys()):
            logging.warning("Fail to load synonym")  # 同义词加载失败

        # 保存Redis连接
        self.redis = redis

        # 初始加载同义词
        self.load()

    def load(self):
        """
        从Redis加载同义词词典

        实现智能刷新策略：
        1. 如果没有Redis连接，直接返回
        2. 如果查找次数少于100次，不刷新（避免频繁刷新）
        3. 如果距离上次刷新不足1小时，不刷新（时间间隔控制）
        4. 满足条件时从Redis加载最新的同义词词典

        Redis键名: "kevin_synonyms"
        数据格式: JSON字符串，包含词汇到同义词列表的映射
        """
        # 检查Redis连接
        if not self.redis:
            return

        # 查找次数不足，跳过刷新
        if self.lookup_num < 100:
            return

        # 检查时间间隔（1小时 = 3600秒）
        tm = time.time()
        if tm - self.load_tm < 3600:
            return

        # 更新加载时间和重置计数器
        self.load_tm = time.time()
        self.lookup_num = 0

        # 从Redis获取同义词数据
        d = self.redis.get("kevin_synonyms")
        if not d:
            return

        # 解析JSON数据并更新词典
        try:
            d = json.loads(d)
            self.dictionary = d
        except Exception as error:
            logging.error(
                "Fail to load realtime synonyms: error_type=%s",
                type(error).__name__,
            )

    def lookup(self, tk, topn=8):
        """
        查找指定词汇的同义词

        支持两种查找模式：
        1. 英文词汇：使用NLTK WordNet进行同义词查找
        2. 中文词汇：使用本地/Redis词典进行查找

        Args:
            tk (str): 要查找同义词的词汇
            topn (int): 返回的最大同义词数量，默认为8

        Returns:
            list: 同义词列表，按相关性排序

        处理流程：
        1. 检查是否为纯英文小写词汇
        2. 如果是英文，使用WordNet查找同义词
        3. 如果不是英文，使用词典查找
        4. 返回指定数量的同义词
        """
        # 检查是否为纯英文小写词汇（只包含a-z字母）
        if re.match(r"[a-z]+$", tk):
            # 使用NLTK WordNet查找英文同义词
            # 1. 获取所有同义词集合
            # 2. 提取同义词名称并去除下划线
            # 3. 去除原词本身
            # 4. 过滤空字符串
            res = list(set([re.sub("_", " ", syn.name().split(".")[0]) for syn in wordnet.synsets(tk)]) - set([tk]))
            return [t for t in res if t]

        # 中文或其他语言词汇处理
        # 增加查找计数器
        self.lookup_num += 1

        # 尝试刷新词典（如果满足条件）
        self.load()

        # 标准化输入词汇：转小写，合并多个空白字符为单个空格
        normalized_tk = re.sub(r"[ \t]+", " ", tk.lower())

        # 从词典中查找同义词
        res = self.dictionary.get(normalized_tk, [])

        # 确保返回值为列表格式
        if isinstance(res, str):
            res = [res]

        # 返回指定数量的同义词
        return res[:topn]
