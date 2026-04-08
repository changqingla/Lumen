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
RAG 分词器模块

这个模块实现了一个专门用于 RAG (检索增强生成) 系统的中文分词器。
主要功能包括：
1. 基于字典树 (Trie) 的高效中文分词
2. 支持自定义词典
3. 中英文混合文本处理
4. 词频统计和词性标注
5. 繁简体转换
6. 全角半角转换

主要算法：
- 使用前向最大匹配和后向最大匹配相结合的分词策略
- 通过动态规划优化分词结果
- 支持词典缓存以提高性能
"""

import logging
import copy
import datrie  # 字典树数据结构
import math
import os
import re
import string
import sys
from hanziconv import HanziConv  # 繁简体转换
from nltk import word_tokenize  # 英文分词
from nltk.stem import PorterStemmer, WordNetLemmatizer  # 英文词干提取和词形还原
from core.utils import get_project_base_directory


class RagTokenizer:
    """
    RAG 分词器主类

    这个类实现了一个高效的中文分词器，专门为 RAG 系统优化。
    使用字典树 (Trie) 数据结构存储词典，支持前向和后向匹配算法。
    """

    def key_(self, line):
        """
        生成正向查找的键

        Args:
            line (str): 输入文本

        Returns:
            str: 用于字典树查找的键（UTF-8编码后的字符串表示）
        """
        return str(line.lower().encode("utf-8"))[2:-1]
    
# 过程：
# 1. "hello world" (小写转换)
# 2. b'hello world' (UTF-8 编码为 bytes)
# 3. "b'hello world'" (转换为字符串表示)
# 4. "hello world" (去掉 b' 和 ')

    def rkey_(self, line):
        """
        生成反向查找的键

        Args:
            line (str): 输入文本

        Returns:
            str: 用于反向字典树查找的键（添加"DD"前缀并反转）
        """
        return str(("DD" + (line[::-1].lower())).encode("utf-8"))[2:-1]
    
# line = "Hello"
# # 执行步骤：
# 1. line[::-1] → "olleH"
# 2. .lower() → "olleh"
# 3. "DD" + "olleh" → "DDolleh"
# 4. encode → b'DDolleh'
# 5. str(b'DDolleh') → "b'DDolleh'"
# 6. [2:-1] → "DDolleh"

    def loadDict_(self, fnm, save_cache=True):
        """
        从词典文件加载词汇到字典树

        词典文件格式：每行包含 "词汇 频率 词性"，用空格或制表符分隔

        Args:
            fnm (str): 词典文件路径
            save_cache (bool): 是否保存字典树缓存文件
        """
        logging.info(f"[HUQIE]:Build trie from {fnm}")
        try:
            of = open(fnm, "r", encoding='utf-8')
            while True:
                line = of.readline()
                if not line:
                    break
                # 清理行尾换行符
                line = re.sub(r"[\r\n]+", "", line)
                # 跳过空行
                if not line.strip():
                    continue
                # 按一个或多个空格/制表符分割：[词汇, 频率, 词性]
                line = re.split(r"[ \t]+", line)
                # 检查格式是否正确（至少需要3个字段）
                if len(line) < 3:
                    logging.warning(f"[HUQIE]:Skip invalid line: {line}")
                    continue
                k = self.key_(line[0])  # 生成正向查找键
                # 将频率转换为对数值存储（节省空间）
                F = int(math.log(float(line[1]) / self.DENOMINATOR) + .5)
                # 如果词汇不存在或新频率更高，则更新
                if k not in self.trie_ or self.trie_[k][0] < F:
                    self.trie_[self.key_(line[0])] = (F, line[2])  # 存储 (频率, 词性)
                # 为反向匹配存储标记
                self.trie_[self.rkey_(line[0])] = 1

            # 保存字典树缓存文件以提高下次加载速度
            if save_cache:
                dict_file_cache = fnm + ".trie"
                logging.info(f"[HUQIE]:Build trie cache to {dict_file_cache}")
                self.trie_.save(dict_file_cache)
            of.close()
        except Exception:
            logging.exception(f"[HUQIE]:Build trie {fnm} failed")

    def __init__(self, debug=False):
        """
        初始化 RAG 分词器

        Args:
            debug (bool): 是否开启调试模式
        """
        self.DEBUG = debug
        self.DENOMINATOR = 1000000  # 词频归一化分母
        # 基础词典目录路径
        self.DIR_ = os.path.join(get_project_base_directory(), "core/res", "huqie")

        # 初始化英文处理工具
        self.stemmer = PorterStemmer()  # 英文词干提取器
        self.lemmatizer = WordNetLemmatizer()  # 英文词形还原器

        # 定义分割字符的正则表达式（标点符号和英文数字组合）
        self.SPLIT_CHAR = r"([ ,\.<>/?;:'\[\]\\`!@#$%^&*\(\)\{\}\|_+=《》，。？、；''：""【】~！￥%……（）——-]+|[a-zA-Z0-9,\.-]+)"

        # 设置词典文件路径
        trie_file_name = self.DIR_ + ".txt.trie"  # 字典树缓存文件
        base_dict_path = self.DIR_ + ".txt"  # 基础词典文件
        custom_dict_path = os.path.join(get_project_base_directory(), "core/res", "custom_dict.txt")  # 自定义词典

        # 检查是否需要重建字典树缓存
        need_rebuild = self._need_rebuild_trie(trie_file_name, base_dict_path, custom_dict_path)
        
        if need_rebuild:
            # 需要重建字典树缓存
            logging.info(f"[HUQIE]:Building trie cache (base dict updated or custom dict changed)")
            self.trie_ = datrie.Trie(string.printable)  # 创建新的字典树

            # 加载基础词典，但不单独保存缓存
            self.loadDict_(base_dict_path, save_cache=False)

            # 加载自定义词典（如果存在）
            if os.path.exists(custom_dict_path):
                logging.info(f"[HUQIE]:Loading custom dictionary from {custom_dict_path}")
                self.addUserDict(custom_dict_path)
                logging.info(f"[HUQIE]:Custom dictionary loaded successfully")

            # 保存包含基础词典和自定义词典的完整字典树缓存
            logging.info(f"[HUQIE]:Saving complete trie cache to {trie_file_name}")
            self.trie_.save(trie_file_name)
        else:
            try:
                # 从缓存文件加载字典树（更快）
                logging.info(f"[HUQIE]:Loading trie from cache {trie_file_name}")
                self.trie_ = datrie.Trie.load(trie_file_name)
                logging.info(f"[HUQIE]:Trie cache loaded successfully")
            except Exception:
                # 缓存加载失败，重新构建字典树
                logging.exception(f"[HUQIE]:Fail to load trie file {trie_file_name}, rebuilding trie")
                self.trie_ = datrie.Trie(string.printable)
                self.loadDict_(base_dict_path, save_cache=False)
                if os.path.exists(custom_dict_path):
                    logging.info(f"[HUQIE]:Loading custom dictionary from {custom_dict_path}")
                    self.addUserDict(custom_dict_path)
                    logging.info(f"[HUQIE]:Custom dictionary loaded successfully")
                # 保存重建的完整字典树缓存
                logging.info(f"[HUQIE]:Saving complete trie cache to {trie_file_name}")
                self.trie_.save(trie_file_name)

    def _need_rebuild_trie(self, trie_file_name, base_dict_path, custom_dict_path):
        """
        检查是否需要重建字典树缓存

        通过比较文件修改时间来判断是否需要重建缓存：
        - 如果缓存文件不存在，需要重建
        - 如果词典文件比缓存文件新，需要重建

        Args:
            trie_file_name (str): 字典树缓存文件路径
            base_dict_path (str): 基础词典文件路径
            custom_dict_path (str): 自定义词典文件路径

        Returns:
            bool: True 表示需要重建，False 表示可以使用现有缓存
        """
        # 如果字典树缓存文件不存在，需要重建
        if not os.path.exists(trie_file_name):
            logging.info(f"[HUQIE]:Trie file {trie_file_name} not found, need to build")
            return True

        try:
            trie_mtime = os.path.getmtime(trie_file_name)  # 获取缓存文件修改时间

            # 检查基础词典是否比缓存文件更新
            if os.path.exists(base_dict_path):
                base_dict_mtime = os.path.getmtime(base_dict_path)
                if base_dict_mtime > trie_mtime:
                    logging.info(f"[HUQIE]:Base dictionary {base_dict_path} updated, need to rebuild trie")
                    return True

            # 检查自定义词典是否比缓存文件更新
            if os.path.exists(custom_dict_path):
                custom_dict_mtime = os.path.getmtime(custom_dict_path)
                if custom_dict_mtime > trie_mtime:
                    logging.info(f"[HUQIE]:Custom dictionary {custom_dict_path} updated, need to rebuild trie")
                    return True

            # 所有词典文件都没有更新，可以使用现有缓存
            return False

        except OSError as e:
            # 文件时间检查失败，为安全起见重建缓存
            logging.warning(f"[HUQIE]:Failed to check file modification time: {e}, rebuilding trie")
            return True

    def loadUserDict(self, fnm):
        """
        加载用户自定义词典

        首先尝试加载缓存文件，如果失败则从原始词典文件加载

        Args:
            fnm (str): 词典文件路径（不包含.trie扩展名）
        """
        try:
            # 尝试加载预编译的字典树缓存
            self.trie_ = datrie.Trie.load(fnm + ".trie")
            return
        except Exception:
            # 缓存加载失败，创建新的字典树并从原始文件加载
            self.trie_ = datrie.Trie(string.printable)
        self.loadDict_(fnm)

    def addUserDict(self, fnm):
        """
        添加用户自定义词典到现有字典树

        这个方法将新词典合并到当前字典树中，不会覆盖现有词典

        Args:
            fnm (str): 词典文件路径
        """
        self.loadDict_(fnm, save_cache=False)  # 不保存单独的缓存

    def _strQ2B(self, ustring):
        """
        全角字符转半角字符

        将全角的英文字母、数字、标点符号转换为半角字符，
        这有助于统一文本格式，提高分词准确性。

        Args:
            ustring (str): 包含全角字符的字符串

        Returns:
            str: 转换后的半角字符串
        """
        rstring = ""
        for uchar in ustring:
            inside_code = ord(uchar)
            if inside_code == 0x3000:  # 全角空格
                inside_code = 0x0020  # 转换为半角空格
            else:
                inside_code -= 0xfee0  # 全角字符转半角的偏移量
            # 检查转换后是否为有效的半角字符
            if inside_code < 0x0020 or inside_code > 0x7e:
                rstring += uchar  # 保持原字符
            else:
                rstring += chr(inside_code)  # 使用转换后的半角字符
        return rstring

    def _tradi2simp(self, line):
        """
        繁体中文转简体中文

        Args:
            line (str): 包含繁体中文的字符串

        Returns:
            str: 转换后的简体中文字符串
        """
        return HanziConv.toSimplified(line)

    def dfs_(self, chars, s, preTks, tkslist):
        """
        深度优先搜索分词算法

        使用动态规划和深度优先搜索来找到所有可能的分词组合，
        然后通过评分机制选择最优的分词结果。

        Args:
            chars (str): 待分词的字符序列
            s (int): 当前搜索的起始位置
            preTks (list): 已经确定的分词结果列表
            tkslist (list): 存储所有可能分词结果的列表

        Returns:
            int: 搜索到达的最远位置
        """
        res = s
        # 如果已经搜索到字符串末尾，保存当前分词结果
        if s >= len(chars):
            tkslist.append(preTks)
            return res

        # 剪枝优化：预测下一个可能的分割点
        S = s + 1
        if s + 2 <= len(chars):
            t1, t2 = "".join(chars[s:s + 1]), "".join(chars[s:s + 2])
            # 如果单字符有前缀但双字符没有，跳过单字符
            if self.trie_.has_keys_with_prefix(self.key_(t1)) and not self.trie_.has_keys_with_prefix(
                    self.key_(t2)):
                S = s + 2
        # 避免连续的单字符分词（通常不是好的分词结果）
        if len(preTks) > 2 and len(
                preTks[-1][0]) == 1 and len(preTks[-2][0]) == 1 and len(preTks[-3][0]) == 1:
            t1 = preTks[-1][0] + "".join(chars[s:s + 1])
            if self.trie_.has_keys_with_prefix(self.key_(t1)):
                S = s + 2

        # 尝试所有可能的词汇长度
        for e in range(S, len(chars) + 1):
            t = "".join(chars[s:e])
            k = self.key_(t)

            # 如果当前字符串不是任何词汇的前缀，停止扩展
            if e > s + 1 and not self.trie_.has_keys_with_prefix(k):
                break

            # 如果找到了词典中的词汇，递归搜索剩余部分
            if k in self.trie_:
                pretks = copy.deepcopy(preTks)
                if k in self.trie_:
                    pretks.append((t, self.trie_[k]))  # 添加词汇和其频率、词性信息
                else:
                    pretks.append((t, (-12, '')))  # 未知词汇，给予低分
                res = max(res, self.dfs_(chars, e, pretks, tkslist))

        # 如果找到了更好的分词路径，返回
        if res > s:
            return res

        # 否则，将当前字符作为单独的词汇处理
        t = "".join(chars[s:s + 1])
        k = self.key_(t)
        if k in self.trie_:
            preTks.append((t, self.trie_[k]))
        else:
            preTks.append((t, (-12, '')))  # 单字符未知词

        return self.dfs_(chars, s + 1, preTks, tkslist)

# dfs_ 是深度优先搜索分词算法，用于找到所有可能的分词组合：
# 示例：对 "北京大学" 进行深度优先搜索分词
# text = "北京大学"
# tkslist = []

# 假设词典中有：
# "北京" -> (100, 'n')  # 地名，频率100
# "大学" -> (80, 'n')   # 名词，频率80  
# "北京大学" -> (150, 'n') # 专有名词，频率150
# "北" -> (20, 'n')     # 方位词，频率20
# "京" -> (15, 'n')     # 名词，频率15

# tokenizer.dfs_("北京大学", 0, [], tkslist)

# 可能的分词结果：
# 结果1: [("北京大学", (150, 'n'))]           # 作为整词
# 结果2: [("北京", (100, 'n')), ("大学", (80, 'n'))]  # 分为两词
# 结果3: [("北", (20, 'n')), ("京", (15, 'n')), ("大学", (80, 'n'))]  # 更细分割

# print(f"找到 {len(tkslist)} 种分词方案")


    def freq(self, tk):
        """
        获取词汇的频率

        Args:
            tk (str): 要查询的词汇

        Returns:
            int: 词汇频率，如果词汇不存在返回0
        """
        k = self.key_(tk)
        if k not in self.trie_:
            return 0
        # 将存储的对数值转换回原始频率
        return int(math.exp(self.trie_[k][0]) * self.DENOMINATOR + 0.5)

    def tag(self, tk):
        """
        获取词汇的词性标注

        Args:
            tk (str): 要查询的词汇

        Returns:
            str: 词性标注，如果词汇不存在返回空字符串
        """
        k = self.key_(tk)
        if k not in self.trie_:
            return ""
        return self.trie_[k][1]  # 返回存储的词性信息

    def score_(self, tfts):
        """
        分词结果评分函数

        根据词汇频率、词汇长度和分词数量来评估分词质量。
        评分越高表示分词质量越好。

        评分公式：B/词汇数量 + 长词比例 + 总频率
        其中：
        - B/词汇数量：鼓励较少的分词数量（避免过度切分）
        - 长词比例：鼓励使用长词（长词通常更有意义）
        - 总频率：鼓励使用高频词汇

        Args:
            tfts (list): 分词结果列表，每个元素为 (词汇, (频率, 词性))

        Returns:
            tuple: (词汇列表, 评分)
        """
        B = 30  # 基础分数，用于平衡词汇数量的影响
        F, L, tks = 0, 0, []
        for tk, (freq, tag) in tfts:
            F += freq  # 累加词频分数
            L += 0 if len(tk) < 2 else 1  # 统计长词（>=2字符）的数量
            tks.append(tk)
        # F /= len(tks)  # 可选：平均词频
        L /= len(tks)  # 长词比例
        logging.debug("[SC] {} {} {} {} {}".format(tks, len(tks), L, F, B / len(tks) + L + F))
        return tks, B / len(tks) + L + F

    def sortTks_(self, tkslist):
        """
        对多个分词结果按评分排序

        对所有可能的分词结果进行评分，然后按分数从高到低排序，
        分数最高的分词结果被认为是最优的。

        Args:
            tkslist (list): 多个分词结果的列表

        Returns:
            list: 按评分排序的分词结果列表，每个元素为 (词汇列表, 评分)
        """
        res = []
        for tfts in tkslist:
            tks, s = self.score_(tfts)  # 计算每个分词结果的评分
            res.append((tks, s))
        # 按评分从高到低排序，返回最优的分词结果
        return sorted(res, key=lambda x: x[1], reverse=True)

    def merge_(self, tks):
        """
        合并分词结果中的特殊情况

        处理分词过程中可能被错误分割的词汇，特别是包含标点符号的词汇。
        例如："www.example.com" 可能被分为 "www", ".", "example", ".", "com"，
        这个函数会将它们重新合并。

        Args:
            tks (str): 分词结果字符串（词汇间用空格分隔）

        Returns:
            str: 合并后的分词结果
        """
        # 如果分割字符是词汇的一部分，需要重新合并
        res = []
        tks = re.sub(r"[ ]+", " ", tks).split()  # 标准化空格并分割
        s = 0
        while True:
            if s >= len(tks):
                break
            E = s + 1
            # 尝试合并连续的词汇（最多5个），如果合并后的词汇在词典中存在
            for e in range(s + 2, min(len(tks) + 2, s + 6)):
                tk = "".join(tks[s:e])
                # 如果合并后的词汇包含分割字符但在词典中存在，则进行合并
                if re.search(self.SPLIT_CHAR, tk) and self.freq(tk):
                    E = e
            res.append("".join(tks[s:E]))
            s = E

        return " ".join(res)

    def maxForward_(self, line):
        """
        前向最大匹配分词算法

        从左到右扫描文本，每次尽可能匹配最长的词汇。
        这是一种贪心算法，优先选择长词汇。

        算法步骤：
        1. 从当前位置开始，尝试匹配尽可能长的词汇
        2. 如果找到词典中的词汇，添加到结果中
        3. 移动到下一个位置，重复步骤1-2

        Args:
            line (str): 待分词的文本

        Returns:
            tuple: (词汇列表, 评分)
        """
        res = []
        s = 0
        while s < len(line):
            e = s + 1
            t = line[s:e]
            # 向前扩展，寻找最长的可能匹配
            while e < len(line) and self.trie_.has_keys_with_prefix(
                    self.key_(t)):
                e += 1
                t = line[s:e]

            # 回退到最后一个在词典中存在的词汇
            while e - 1 > s and self.key_(t) not in self.trie_:
                e -= 1
                t = line[s:e]

            # 添加找到的词汇（或单字符）到结果中
            if self.key_(t) in self.trie_:
                res.append((t, self.trie_[self.key_(t)]))
            else:
                res.append((t, (0, '')))  # 未知词汇

            s = e

        return self.score_(res)

    def maxBackward_(self, line):
        """
        后向最大匹配分词算法

        从右到左扫描文本，每次尽可能匹配最长的词汇。
        这种方法可以处理前向匹配无法正确处理的歧义情况。

        算法步骤：
        1. 从文本末尾开始，向左尝试匹配尽可能长的词汇
        2. 使用反向字典树（rkey_）来加速匹配
        3. 如果找到词典中的词汇，添加到结果中
        4. 移动到前一个位置，重复步骤1-3

        Args:
            line (str): 待分词的文本

        Returns:
            tuple: (词汇列表, 评分)
        """
        res = []
        s = len(line) - 1
        while s >= 0:
            e = s + 1
            t = line[s:e]
            # 向左扩展，使用反向字典树寻找最长的可能匹配
            while s > 0 and self.trie_.has_keys_with_prefix(self.rkey_(t)):
                s -= 1
                t = line[s:e]

            # 回退到最后一个在词典中存在的词汇
            while s + 1 < e and self.key_(t) not in self.trie_:
                s += 1
                t = line[s:e]

            # 添加找到的词汇（或单字符）到结果中
            if self.key_(t) in self.trie_:
                res.append((t, self.trie_[self.key_(t)]))
            else:
                res.append((t, (0, '')))  # 未知词汇

            s -= 1

        # 反转结果列表，因为是从右到左构建的
        return self.score_(res[::-1])

    def english_normalize_(self, tks):
        """
        英文词汇标准化处理

        对英文词汇进行词形还原和词干提取，以提高检索的准确性。
        例如："running" -> "run", "better" -> "good"

        Args:
            tks (list): 词汇列表

        Returns:
            list: 标准化后的词汇列表
        """
        return [self.stemmer.stem(self.lemmatizer.lemmatize(t)) if re.match(r"[a-zA-Z_-]+$", t) else t for t in tks]

    def _split_by_lang(self, line):
        """
        按语言类型分割文本

        将混合的中英文文本按语言类型分割成不同的片段，
        以便对中文和英文采用不同的分词策略。

        Args:
            line (str): 混合语言的文本

        Returns:
            list: 语言片段列表，每个元素为 (文本片段, 是否为中文)
        """
        txt_lang_pairs = []
        # 首先按分割字符（标点符号等）分割文本
        arr = re.split(self.SPLIT_CHAR, line)
        for a in arr:
            if not a:
                continue
            s = 0
            e = s + 1
            zh = is_chinese(a[s])  # 判断起始字符是否为中文
            while e < len(a):
                _zh = is_chinese(a[e])  # 判断当前字符是否为中文
                if _zh == zh:  # 如果语言类型相同，继续扩展
                    e += 1
                    continue
                # 语言类型发生变化，保存当前片段
                txt_lang_pairs.append((a[s: e], zh))
                s = e
                e = s + 1
                zh = _zh
            if s >= len(a):
                continue
            # 保存最后一个片段
            txt_lang_pairs.append((a[s: e], zh))
        return txt_lang_pairs

    def tokenize(self, line):
        """
        主要的分词方法

        这是分词器的核心方法，使用前向最大匹配和后向最大匹配相结合的策略，
        通过动态规划算法找到最优的分词结果。

        处理流程：
        1. 文本预处理（标点符号处理、全角转半角、繁简转换）
        2. 按语言类型分割文本（中文/英文）
        3. 对中文部分使用双向匹配算法
        4. 对英文部分使用NLTK分词
        5. 合并和优化分词结果

        Args:
            line (str): 待分词的文本

        Returns:
            str: 分词结果，词汇之间用空格分隔
        """
        # 预处理：将非字母数字字符替换为空格
        line = re.sub(r"\W+", " ", line)
        # 全角转半角并转为小写
        line = self._strQ2B(line).lower()
        # 繁体转简体
        line = self._tradi2simp(line)

        # 按语言类型分割文本，分别处理中英文
        arr = self._split_by_lang(line)
        res = []
        for L, lang in arr:
            # 处理英文文本
            if not lang:
                # 使用NLTK进行英文分词，并进行词形还原和词干提取
                res.extend([self.stemmer.stem(self.lemmatizer.lemmatize(t)) for t in word_tokenize(L)])
                continue

            # 处理短文本、纯英文或纯数字，直接添加
            if len(L) < 2 or re.match(
                    r"[a-z\.-]+$", L) or re.match(r"[0-9\.-]+$", L):
                res.append(L)
                continue

            # 对中文文本使用双向匹配算法
            # 1. 前向最大匹配
            tks, s = self.maxForward_(L)
            # 2. 后向最大匹配
            tks1, s1 = self.maxBackward_(L)
            if self.DEBUG:
                logging.debug("[FW] {} {}".format(tks, s))
                logging.debug("[BW] {} {}".format(tks1, s1))

            # 3. 合并前向和后向匹配的结果
            i, j, _i, _j = 0, 0, 0, 0
            # 找到前向和后向匹配结果的相同前缀
            same = 0
            while i + same < len(tks1) and j + same < len(tks) and tks1[i + same] == tks[j + same]:
                same += 1
            if same > 0:
                res.append(" ".join(tks[j: j + same]))
            _i = i + same
            _j = j + same
            j = _j + 1
            i = _i + 1

            # 处理前向和后向匹配结果的差异部分
            while i < len(tks1) and j < len(tks):
                tk1, tk = "".join(tks1[_i:i]), "".join(tks[_j:j])
                # 如果累积的字符串不相等，继续扩展较短的一边
                if tk1 != tk:
                    if len(tk1) > len(tk):
                        j += 1
                    else:
                        i += 1
                    continue

                # 如果当前词汇不同，跳过
                if tks1[i] != tks[j]:
                    i += 1
                    j += 1
                    continue

                # 对有歧义的部分使用深度优先搜索找到最优分词
                tkslist = []
                self.dfs_("".join(tks[_j:j]), 0, [], tkslist)
                res.append(" ".join(self.sortTks_(tkslist)[0][0]))

                # 继续寻找下一个相同的部分
                same = 1
                while i + same < len(tks1) and j + same < len(tks) and tks1[i + same] == tks[j + same]:
                    same += 1
                res.append(" ".join(tks[j: j + same]))
                _i = i + same
                _j = j + same
                j = _j + 1
                i = _i + 1

            # 处理剩余的部分
            if _i < len(tks1):
                assert _j < len(tks)
                assert "".join(tks1[_i:]) == "".join(tks[_j:])
                tkslist = []
                self.dfs_("".join(tks[_j:]), 0, [], tkslist)
                res.append(" ".join(self.sortTks_(tkslist)[0][0]))

        res = " ".join(res)
        logging.debug("[TKS] {}".format(self.merge_(res)))
        return self.merge_(res)

#tokenize 是主分词函数，整合了所有分词策略
# 示例1：中文文本分词
# tokenizer = RagTokenizer()

# text1 = "北京大学的机器学习研究很有名。"
# result1 = tokenizer.tokenize(text1)
# print(f"输入: {text1}")
# print(f"分词结果: {result1}")
# 输出: "北京大学 的 机器学习 研究 很 有名"

# 示例2：中英混合文本分词
# text2 = "我在学习Python编程和machine learning算法。"
# result2 = tokenizer.tokenize(text2)
# print(f"输入: {text2}")
# print(f"分词结果: {result2}")
# 输出: "我 在 学习 Python 编程 和 machin learn 算法"

# 示例3：包含标点符号的文本
# text3 = "访问www.example.com获取更多信息！"
# result3 = tokenizer.tokenize(text3)
# print(f"输入: {text3}")
# print(f"分词结果: {result3}")
# 输出: "访问 www.example.com 获取 更多 信息"

# 示例4：复杂学术文本
# text4 = "深度学习(Deep Learning)是机器学习的一个分支。"
# result4 = tokenizer.tokenize(text4)
# print(f"输入: {text4}")
# print(f"分词结果: {result4}")
# 输出: "深度学习 Deep Learn 是 机器学习 的 一个 分支"

    def fine_grained_tokenize(self, tks):
        """
        细粒度分词

        对已经分词的结果进行进一步的细分，特别适用于：
        1. 复合词的拆分
        2. 长词的细分
        3. 提高检索的召回率

        Args:
            tks (str): 已分词的文本（词汇间用空格分隔）

        Returns:
            str: 细粒度分词结果
        """
        tks = tks.split()
        # 统计中文词汇的数量
        zh_num = len([1 for c in tks if c and is_chinese(c[0])])

        # 如果中文词汇比例较低（<20%），使用简单的斜杠分割
        if zh_num < len(tks) * 0.2:
            res = []
            for tk in tks:
                res.extend(tk.split("/"))  # 按斜杠分割
            return " ".join(res)

        res = []
        for tk in tks:
            if len(tk) < 3 or re.match(r"[0-9,\.-]+$", tk):
                res.append(tk)
                continue
            tkslist = []
            if len(tk) > 10:
                tkslist.append(tk)
            else:
                self.dfs_(tk, 0, [], tkslist)
            if len(tkslist) < 2:
                res.append(tk)
                continue
            stk = self.sortTks_(tkslist)[1][0]
            if len(stk) == len(tk):
                stk = tk
            else:
                if re.match(r"[a-z\.-]+$", tk):
                    for t in stk:
                        if len(t) < 3:
                            stk = tk
                            break
                    else:
                        stk = " ".join(stk)
                else:
                    stk = " ".join(stk)

            res.append(stk)

        return " ".join(self.english_normalize_(res))

# 示例：细粒度分词处理
# tokenizer = RagTokenizer()

# 输入：粗分词结果
# coarse_tokens = ["机器学习", "machine", "learning", "算法", "running", "better"]

# 调用细粒度分词
# fine_tokens = tokenizer.fine_grained_tokenize(coarse_tokens)

# print("粗分词结果:", coarse_tokens)
# print("细分词结果:", fine_tokens)

# 输出结果：
# 粗分词结果: ['机器学习', 'machine', 'learning', '算法', 'running', 'better']
# 细分词结果: ['机器学习', 'machin', 'learn', '算法', 'run', 'good']
# 
# 解释：
# - "机器学习", "算法" 保持不变（中文词汇）
# - "machine" -> "machin" (词干提取)
# - "learning" -> "learn" (词干提取) 
# - "running" -> "run" (词干提取)
# - "better" -> "good" (词形还原)

def is_chinese(s):
    """
    判断字符是否为中文字符

    Args:
        s (str): 单个字符

    Returns:
        bool: True 表示是中文字符，False 表示不是
    """
    if s >= u'\u4e00' and s <= u'\u9fa5':  # 中文字符的Unicode范围
        return True
    else:
        return False


def is_number(s):
    """
    判断字符是否为数字字符

    Args:
        s (str): 单个字符

    Returns:
        bool: True 表示是数字字符，False 表示不是
    """
    if s >= u'\u0030' and s <= u'\u0039':  # 数字字符的Unicode范围 (0-9)
        return True
    else:
        return False


def is_alphabet(s):
    """
    判断字符是否为英文字母

    Args:
        s (str): 单个字符

    Returns:
        bool: True 表示是英文字母，False 表示不是
    """
    if (s >= u'\u0041' and s <= u'\u005a') or (s >= u'\u0061' and s <= u'\u007a'):
        # 大写字母 A-Z 或小写字母 a-z 的Unicode范围
        return True
    else:
        return False


def naiveQie(txt):
    """
    简单的文本分割函数

    在英文词汇之间添加空格分隔符，用于处理连续的英文文本。

    Args:
        txt (str): 输入文本

    Returns:
        list: 分割后的词汇列表
    """
    tks = []
    for t in txt.split():
        # 如果前一个词和当前词都以英文字母结尾，在它们之间添加空格
        if tks and re.match(r".*[a-zA-Z]$", tks[-1]) and re.match(r".*[a-zA-Z]$", t):
            tks.append(" ")
        tks.append(t)
    return tks


# 创建全局分词器实例
tokenizer = RagTokenizer()

# 导出主要的分词函数，方便外部调用
tokenize = tokenizer.tokenize  # 主分词函数
fine_grained_tokenize = tokenizer.fine_grained_tokenize  # 细粒度分词函数
tag = tokenizer.tag  # 词性标注函数
freq = tokenizer.freq  # 词频查询函数
loadUserDict = tokenizer.loadUserDict  # 加载用户词典函数
addUserDict = tokenizer.addUserDict  # 添加用户词典函数
tradi2simp = tokenizer._tradi2simp  # 繁简转换函数
strQ2B = tokenizer._strQ2B  # 全角半角转换函数

if __name__ == '__main__':
    """
    分词器测试和演示代码

    这部分代码展示了分词器的各种使用场景和测试用例，
    包括重复字符、金融文本、教育文本、中英混合文本等。
    """
    # 创建调试模式的分词器实例
    tknzr = RagTokenizer(debug=True)

    # 测试用例1：重复字符处理
    tks = tknzr.tokenize(
        "哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈哈")
    logging.info(tknzr.fine_grained_tokenize(tks))

    # 测试用例2：金融领域文本
    tks = tknzr.tokenize(
        "公开征求意见稿提出，境外投资者可使用自有人民币或外汇投资。使用外汇投资的，可通过债券持有人在香港人民币业务清算行及香港地区经批准可进入境内银行间外汇市场进行交易的境外人民币业务参加行（以下统称香港结算行）办理外汇资金兑换。香港结算行由此所产生的头寸可到境内银行间外汇市场平盘。使用外汇投资的，在其投资的债券到期或卖出后，原则上应兑换回外汇。")
    logging.info(tknzr.fine_grained_tokenize(tks))

    # 测试用例3：教育领域文本
    tks = tknzr.tokenize(
        "多校划片就是一个小区对应多个小学初中，让买了学区房的家庭也不确定到底能上哪个学校。目的是通过这种方式为学区房降温，把就近入学落到实处。南京市长江大桥")
    logging.info(tknzr.fine_grained_tokenize(tks))

    # 测试用例4：中英混合文本
    tks = tknzr.tokenize(
        "实际上当时他们已经将业务中心偏移到安全部门和针对政府企业的部门 Scripts are compiled and cached aaaaaaaaa")
    logging.info(tknzr.fine_grained_tokenize(tks))

    # 测试用例5：简短口语化文本
    tks = tknzr.tokenize("虽然我不怎么玩")
    logging.info(tknzr.fine_grained_tokenize(tks))

    # 测试用例6：商业文本
    tks = tknzr.tokenize("蓝月亮如何在外资夹击中生存,那是全宇宙最有意思的")
    logging.info(tknzr.fine_grained_tokenize(tks))

    # 测试用例7：技术和生活混合文本
    tks = tknzr.tokenize(
        "涡轮增压发动机num最大功率,不像别的共享买车锁电子化的手段,我们接过来是否有意义,黄黄爱美食,不过，今天阿奇要讲到的这家农贸市场，说实话，还真蛮有特色的！不仅环境好，还打出了")
    logging.info(tknzr.fine_grained_tokenize(tks))

    # 测试用例8：日常对话
    tks = tknzr.tokenize("这周日你去吗？这周日你有空吗？")
    logging.info(tknzr.fine_grained_tokenize(tks))

    # 测试用例9：技术招聘文本
    tks = tknzr.tokenize("Unity3D开发经验 测试开发工程师 c++双11双11 985 211 ")
    logging.info(tknzr.fine_grained_tokenize(tks))

    # 测试用例10：数据分析相关文本
    tks = tknzr.tokenize(
        "数据分析项目经理|数据分析挖掘|数据分析方向|商品数据分析|搜索数据分析 sql python hive tableau Cocos2d-")
    logging.info(tknzr.fine_grained_tokenize(tks))

    # 命令行参数处理：支持自定义词典和文件分词
    if len(sys.argv) < 2:
        sys.exit()

    # 关闭调试模式，加载用户自定义词典
    tknzr.DEBUG = False
    tknzr.loadUserDict(sys.argv[1])  # 第一个参数：用户词典文件

    # 对指定文件进行分词处理
    of = open(sys.argv[2], "r")  # 第二个参数：待分词的文本文件
    while True:
        line = of.readline()
        if not line:
            break
        logging.info(tknzr.tokenize(line))
    of.close()
