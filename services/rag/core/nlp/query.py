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

import logging
import re
from core.utils.doc_store_conn import MatchTextExpr
from core.nlp import rag_tokenizer, term_weight, synonym
from sklearn.metrics.pairwise import cosine_similarity as CosineSimilarity
import numpy as np

class FulltextQueryer:
    """
    全文检索查询器
    
    负责将用户的自然语言问题转换为优化的Elasticsearch查询表达式，
    支持中英文混合查询、同义词扩展、细粒度分词等高级功能。
    """
    
    def __init__(self):
        """初始化查询器"""
        # 词汇权重计算器
        self.tw = term_weight.Dealer()
        # 同义词处理器
        self.syn = synonym.Dealer()
        # 查询字段配置，数字表示权重倍数
        self.query_fields = [
            "title_tks^10",        # 标题词汇，权重10倍
            "title_sm_tks^5",      # 标题细分词汇，权重5倍
            "important_kwd^30",    # 重要关键词，权重30倍
            "important_tks^20",    # 重要词汇，权重20倍
            "question_tks^20",     # 问题词汇，权重20倍
            "content_ltks^2",      # 内容词汇，权重2倍
            "content_sm_ltks",     # 内容细分词汇，权重1倍
        ]

    @staticmethod
    def subSpecialChar(line):
        """
        转义Elasticsearch查询中的特殊字符
        
        Args:
            line (str): 需要转义的文本
            
        Returns:
            str: 转义后的文本
        """
        return re.sub(r"([:\{\}/\[\]\-\*\"\(\)\|\+~\^])", r"\\\1", line).strip()

    @staticmethod
    def isChinese(line):
        """
        判断文本是否为中文
        
        Args:
            line (str): 待判断的文本
            
        Returns:
            bool: True表示中文，False表示英文
            
        判断逻辑：
        - 如果词汇数量<=3，认为是中文
        - 否则计算非英文词汇的比例，>=70%认为是中文
        """
        words = re.split(r"[ \t]+", line)
        if len(words) <= 3:
            return True
            
        non_english_count = 0
        for word in words:
            if not re.match(r"[a-zA-Z]+$", word):
                non_english_count += 1
                
        return non_english_count / len(words) >= 0.7

    @staticmethod
    def rmWWW(txt):
        """
        移除查询文本中的无意义词汇（疑问词、助词等）
        
        Args:
            txt (str): 原始查询文本
            
        Returns:
            str: 清理后的文本
        """
        # 定义需要移除的词汇模式
        patterns = [
            # 中文疑问词和助词
            (
                r"是*(什么样的|哪家|一下|那家|请问|啥样|咋样了|什么时候|何时|何地|何人|是否|是不是|多少|哪里|怎么|哪儿|怎么样|如何|哪些|是啥|啥是|啊|吗|呢|吧|咋|什么|有没有|呀|谁|哪位|哪个)是*",
                "",
            ),
            # 英文疑问词
            (r"(^| )(what|who|how|which|where|why)('re|'s)? ", " "),
            # 英文助词和常用词
            (
                r"(^| )('s|'re|is|are|were|was|do|does|did|don't|doesn't|didn't|has|have|be|there|you|me|your|my|mine|just|please|may|i|should|would|wouldn't|will|won't|done|go|for|with|so|the|a|an|by|i'm|it's|he's|she's|they|they're|you're|as|by|on|in|at|up|out|down|of|to|or|and|if) ",
                " "
            )
        ]
        
        original_txt = txt
        # 应用所有清理模式
        for pattern, replacement in patterns:
            txt = re.sub(pattern, replacement, txt, flags=re.IGNORECASE)
            
        # 如果清理后为空，返回原文本
        if not txt:
            txt = original_txt
            
        return txt

    def question(self, txt, min_match: float = 0.6):
        """
        构建智能问答查询表达式
        
        将用户的自然语言问题转换为优化的Elasticsearch查询，支持：
        1. 文本标准化和清理
        2. 语言检测和分支处理  
        3. 同义词扩展和权重计算
        4. 细粒度分词和短语匹配
        5. 多字段权重查询构建
        
        Args:
            txt (str): 用户输入的问题文本
            min_match (float): 最小匹配度，默认0.6（60%的词汇需要匹配）
            
        Returns:
            tuple: (MatchTextExpr对象, keywords列表) 或 (None, keywords列表)
        """
        # 第一步：文本预处理
        txt = self._preprocess_text(txt)
        
        # 第二步：语言检测和分支处理
        if not self.isChinese(txt):
            return self._process_english_query(txt)
        else:
            return self._process_chinese_query(txt, min_match)
    
    def _preprocess_text(self, txt):
        """
        文本预处理：标准化和清理
        
        Args:
            txt (str): 原始文本
            
        Returns:
            str: 预处理后的文本
        """
        # 繁简转换、全半角转换、小写化、标点清理
        txt = re.sub(
            r"[ :|\r\n\t,，。？?/`!！&^%%()\[\]{}<>]+",
            " ",
            rag_tokenizer.tradi2simp(rag_tokenizer.strQ2B(txt.lower())),
        ).strip()
        
        # 移除无意义词汇
        return FulltextQueryer.rmWWW(txt)
    
    def _process_english_query(self, txt):
        """
        处理英文查询
        
        Args:
            txt (str): 预处理后的英文文本
            
        Returns:
            tuple: (MatchTextExpr对象, keywords列表)
        """
        # 再次清理无意义词汇（英文查询需要更彻底的清理）
        txt = FulltextQueryer.rmWWW(txt)
        
        # 英文分词处理
        tokens = rag_tokenizer.tokenize(txt).split()
        keywords = [t for t in tokens if t]  # 保存原始关键词
        
        # 计算词汇权重并清理
        tokens_with_weights = self._clean_english_tokens(tokens)
        
        # 同义词扩展处理
        synonyms_list = self._expand_english_synonyms(tokens_with_weights, keywords)
        
        # 构建查询表达式
        query_parts = self._build_english_query(tokens_with_weights, synonyms_list, txt)
        
        query = " ".join(query_parts)
        return MatchTextExpr(self.query_fields, query, 100), keywords
    
    def _clean_english_tokens(self, tokens):
        """
        清理英文词汇：移除特殊字符和无效词汇
        
        Args:
            tokens (list): 原始词汇列表
            
        Returns:
            list: 清理后的(词汇, 权重)元组列表
        """
        # 计算词汇权重
        tokens_with_weights = self.tw.weights(tokens, preprocess=False)
        
        # 清理词汇：移除特殊字符和无效词汇
        tokens_with_weights = [(re.sub(r"[ \\\"'^]", "", tk), w) for tk, w in tokens_with_weights]
        tokens_with_weights = [(re.sub(r"^[a-z0-9]$", "", tk), w) for tk, w in tokens_with_weights if tk]
        tokens_with_weights = [(re.sub(r"^[\+-]", "", tk), w) for tk, w in tokens_with_weights if tk]
        
        return [(tk.strip(), w) for tk, w in tokens_with_weights if tk.strip()]
    
    def _expand_english_synonyms(self, tokens_with_weights, keywords):
        """
        英文同义词扩展处理
        
        Args:
            tokens_with_weights (list): (词汇, 权重)元组列表
            keywords (list): 关键词列表（会被修改）
            
        Returns:
            list: 同义词查询片段列表
        """
        synonyms_list = []
        for tk, w in tokens_with_weights[:256]:  # 限制处理词汇数量
            # 查找同义词
            synonyms = self.syn.lookup(tk)
            synonyms = rag_tokenizer.tokenize(" ".join(synonyms)).split()
            keywords.extend(synonyms)  # 添加到关键词列表
            
            # 构建同义词查询片段（权重降低为1/4）
            syn_queries = [f'"{s}"^{w/4:.4f}' for s in synonyms if s.strip()]
            synonyms_list.append(" ".join(syn_queries))
            
        return synonyms_list
    
    def _build_english_query(self, tokens_with_weights, synonyms_list, txt):
        """
        构建英文查询表达式
        
        Args:
            tokens_with_weights (list): (词汇, 权重)元组列表
            synonyms_list (list): 同义词查询片段列表
            txt (str): 原始文本（兜底使用）
            
        Returns:
            list: 查询片段列表
        """
        query_parts = []
        
        # 1. 单词查询：原词 + 同义词组合
        for (tk, w), syn in zip(tokens_with_weights, synonyms_list):
            if tk and not re.match(r"[.^+\(\)-]", tk):  # 过滤特殊字符开头的词
                query_parts.append(f"({tk}^{w:.4f} {syn})")
        
        # 2. 短语查询：相邻词汇组合（权重加倍）
        for i in range(1, len(tokens_with_weights)):
            left, right = tokens_with_weights[i-1][0].strip(), tokens_with_weights[i][0].strip()
            if left and right:
                phrase_weight = max(tokens_with_weights[i-1][1], tokens_with_weights[i][1]) * 2
                query_parts.append(f'"{left} {right}"^{phrase_weight:.4f}')
        
        # 3. 兜底查询：如果没有构建出查询，使用原始文本
        if not query_parts:
            query_parts.append(txt)
            
        return query_parts

    def _process_chinese_query(self, txt, min_match):
        """
        处理中文查询（按照原先算法逻辑）

        Args:
            txt (str): 预处理后的中文文本
            min_match (float): 最小匹配度

        Returns:
            tuple: (MatchTextExpr对象, keywords列表) 或 (None, keywords列表)
        """
        # 再次清理文本
        txt = FulltextQueryer.rmWWW(txt)
        query_segments, keywords = [], []

        # 按词汇分割处理（限制256个词汇）
        for term in self.tw.split(txt)[:256]:
            if not term:
                continue

            keywords.append(term)
            term_weights = self.tw.weights([term])

            # 同义词扩展（对整个term的同义词）
            synonyms = self.syn.lookup(term)
            if synonyms and len(keywords) < 32:
                keywords.extend(synonyms)

            logging.debug(
                "Chinese query term weights computed: count=%s",
                len(term_weights),
            )

            # 构建词汇查询片段（这里会处理每个子词汇的同义词和关键词添加）
            term_query = self._build_chinese_term_query(term_weights, keywords)

            # 如果有多个子词汇，添加邻近查询（权重1.5）
            if len(term_weights) > 1:
                proximity_query = f'("{rag_tokenizer.tokenize(term)}"~2)^1.5'
                term_query = f"{term_query} {proximity_query}"

            # 构建同义词查询片段
            synonym_query = self._build_chinese_synonym_query(synonyms)

            # 组合原词查询和同义词查询
            if synonym_query and term_query:
                combined_query = f"({term_query})^5 OR ({synonym_query})^0.7"
            elif synonym_query:
                combined_query = f"({synonym_query})^0.7"
            else:
                combined_query = term_query

            if combined_query:
                query_segments.append(combined_query)

        # 构建最终查询表达式
        if query_segments:
            query = " OR ".join([f"({segment})" for segment in query_segments if segment])
            return MatchTextExpr(
                self.query_fields, query, 100, {"minimum_should_match": min_match}
            ), keywords

        return None, keywords

    def _need_fine_grained_tokenize(self, tk):
        """
        判断词汇是否需要细粒度分词

        Args:
            tk (str): 词汇

        Returns:
            bool: True表示需要细粒度分词

        判断条件：
        1. 长度>=3个字符
        2. 不是纯数字、英文、符号组合
        """
        if len(tk) < 3:
            return False
        if re.match(r"[0-9a-z\.\+#_\*-]+$", tk):
            return False
        return True

    def _build_chinese_term_query(self, term_weights, keywords):
        """
        构建中文词汇查询片段（按照原先算法逻辑）

        Args:
            term_weights (list): (词汇, 权重)元组列表
            keywords (list): 关键词列表（会被修改）

        Returns:
            str: 词汇查询片段
        """
        term_queries = []
        for tk, w in sorted(term_weights, key=lambda x: x[1] * -1):
            # 细粒度分词处理
            fine_grained_tokens = []
            if self._need_fine_grained_tokenize(tk):
                fine_grained_tokens = self._process_fine_grained_tokens(tk)

            # 按照原先逻辑添加关键词：无论是否需要细粒度分词都要添加
            if len(keywords) < 32:
                keywords.append(re.sub(r"[ \\\"']+", "", tk))
                keywords.extend(fine_grained_tokens)

            # 查找词汇的同义词（这是原先代码的关键逻辑）
            tk_syns = self.syn.lookup(tk)
            tk_syns = [FulltextQueryer.subSpecialChar(s) for s in tk_syns]
            if len(keywords) < 32:
                keywords.extend([s for s in tk_syns if s])
            tk_syns = [rag_tokenizer.fine_grained_tokenize(s) for s in tk_syns if s]
            tk_syns = [f'"{s}"' if s.find(" ") > 0 else s for s in tk_syns]

            # 如果关键词已达到上限，停止处理
            if len(keywords) >= 32:
                break

            # 构建查询片段
            tk_clean = FulltextQueryer.subSpecialChar(tk)
            if tk_clean.find(" ") > 0:
                tk_clean = f'"{tk_clean}"'

            # 添加同义词查询
            if tk_syns:
                tk_clean = f"({tk_clean} OR ({' '.join(tk_syns)})^0.2)"

            # 添加细粒度分词查询
            if fine_grained_tokens:
                fine_tokens_str = " ".join(fine_grained_tokens)
                tk_clean = f'{tk_clean} OR "{fine_tokens_str}" OR ("{fine_tokens_str}"~2)^0.5'

            if tk_clean.strip():
                term_queries.append((tk_clean, w))

        return " ".join([f"({t})^{w}" for t, w in term_queries])

    def _process_fine_grained_tokens(self, tk):
        """
        处理细粒度分词

        Args:
            tk (str): 原始词汇

        Returns:
            list: 细粒度分词结果列表
        """
        fine_tokens = rag_tokenizer.fine_grained_tokenize(tk).split()
        # 清理特殊字符
        fine_tokens = [
            re.sub(
                r"[ ,\./;'\[\]\\`~!@#$%\^&\*\(\)=\+_<>\?:\"\{\}\|，。；''【】、！￥……（）——《》？：""-]+",
                "",
                token,
            )
            for token in fine_tokens
        ]
        # 转义特殊字符并过滤短词
        fine_tokens = [FulltextQueryer.subSpecialChar(token) for token in fine_tokens if len(token) > 1]
        return [token for token in fine_tokens if len(token) > 1]

    def _build_chinese_synonym_query(self, synonyms):
        """
        构建中文同义词查询片段

        Args:
            synonyms (list): 同义词列表

        Returns:
            str: 同义词查询片段
        """
        if not synonyms:
            return ""

        synonym_queries = []
        for synonym_term in synonyms:
            # 转义特殊字符并进行细粒度分词
            syn_clean = FulltextQueryer.subSpecialChar(synonym_term)
            syn_tokenized = rag_tokenizer.fine_grained_tokenize(syn_clean)
            # 包含空格的同义词用引号包围
            if syn_tokenized.find(" ") > 0:
                synonym_queries.append(f'"{syn_tokenized}"')
            else:
                synonym_queries.append(syn_tokenized)

        return " OR ".join(synonym_queries)

    def hybrid_similarity(self, avec, bvecs, atks, btkss, tkweight=0.3, vtweight=0.7):
        """
        计算混合相似度（向量相似度 + 词汇相似度）

        Args:
            avec (list): 查询向量
            bvecs (list): 文档向量列表
            atks (list): 查询词汇列表
            btkss (list): 文档词汇列表的列表
            tkweight (float): 词汇相似度权重，默认0.3
            vtweight (float): 向量相似度权重，默认0.7

        Returns:
            tuple: (混合相似度数组, 词汇相似度数组, 向量相似度数组)
        """

        # 计算向量相似度
        vector_similarities = CosineSimilarity([avec], bvecs)[0]

        # 计算词汇相似度
        token_similarities = self.token_similarity(atks, btkss)

        # 如果向量相似度全为0，只返回词汇相似度
        if np.sum(vector_similarities) == 0:
            return np.array(token_similarities), token_similarities, vector_similarities

        # 计算混合相似度
        hybrid_similarities = vector_similarities * vtweight + np.array(token_similarities) * tkweight

        return hybrid_similarities, token_similarities, vector_similarities

    def token_similarity(self, query_tokens, doc_tokens_list):
        """
        计算词汇相似度

        Args:
            query_tokens (list): 查询词汇列表
            doc_tokens_list (list): 文档词汇列表的列表

        Returns:
            list: 相似度列表
        """
        def tokens_to_dict(tokens):
            """将词汇列表转换为权重字典"""
            token_dict = {}
            if isinstance(tokens, str):
                tokens = tokens.split()
            for token, weight in self.tw.weights(tokens, preprocess=False):
                if token not in token_dict:
                    token_dict[token] = 0
                token_dict[token] += weight
            return token_dict

        query_dict = tokens_to_dict(query_tokens)
        doc_dicts = [tokens_to_dict(tokens) for tokens in doc_tokens_list]

        return [self.similarity(query_dict, doc_dict) for doc_dict in doc_dicts]

    def similarity(self, query_weights, doc_weights):
        """
        计算两个词汇权重字典之间的相似度

        Args:
            query_weights (dict): 查询词汇权重字典
            doc_weights (dict): 文档词汇权重字典

        Returns:
            float: 相似度分数
        """
        # 处理字符串输入
        if isinstance(doc_weights, str):
            doc_weights = {t: w for t, w in self.tw.weights(self.tw.split(doc_weights), preprocess=False)}
        if isinstance(query_weights, str):
            query_weights = {t: w for t, w in self.tw.weights(self.tw.split(query_weights), preprocess=False)}

        # 计算共同词汇的权重和
        common_weight_sum = 1e-9  # 避免除零
        for token, weight in query_weights.items():
            if token in doc_weights:
                common_weight_sum += weight

        # 计算查询词汇的总权重
        query_total_weight = 1e-9
        for weight in query_weights.values():
            query_total_weight += weight

        return common_weight_sum / query_total_weight

    def paragraph(self, content_tokens, keywords=None, keywords_topn=30):
        """
        构建段落查询表达式

        Args:
            content_tokens (str or list): 内容词汇
            keywords (list): 已有关键词列表，默认为空
            keywords_topn (int): 提取关键词的数量，默认30

        Returns:
            MatchTextExpr: 段落查询表达式
        """
        if keywords is None:
            keywords = []

        # 处理输入格式
        if isinstance(content_tokens, str):
            content_tokens = [c.strip() for c in content_tokens.strip() if c.strip()]

        # 计算词汇权重
        tokens_with_weights = self.tw.weights(content_tokens, preprocess=False)

        # 处理已有关键词
        keywords = [f'"{k.strip()}"' for k in keywords]

        # 提取top-N关键词
        for token, weight in sorted(tokens_with_weights, key=lambda x: x[1] * -1)[:keywords_topn]:
            # 查找同义词
            token_synonyms = self.syn.lookup(token)
            token_synonyms = [FulltextQueryer.subSpecialChar(s) for s in token_synonyms]
            token_synonyms = [rag_tokenizer.fine_grained_tokenize(s) for s in token_synonyms if s]
            token_synonyms = [f'"{s}"' if " " in s else s for s in token_synonyms]

            # 处理原词
            token_clean = FulltextQueryer.subSpecialChar(token)
            if " " in token_clean:
                token_clean = f'"{token_clean}"'

            # 添加同义词查询
            if token_synonyms:
                token_clean = f"({token_clean} OR ({' '.join(token_synonyms)})^0.2)"

            # 添加到关键词列表
            if token_clean:
                keywords.append(f"{token_clean}^{weight}")

        return MatchTextExpr(
            self.query_fields,
            " ".join(keywords),
            100,
            {"minimum_should_match": min(3, len(keywords) / 10)}
        )

    def insert_citations(self, answer, chunks, chunk_v, embd_mdl, tkweight=0.1, vtweight=0.9):
        """
        在生成的答案中插入引用标记，标识内容来源
        
        该方法通过语义相似度匹配，在答案的相关句子后添加引用标记，
        帮助用户了解答案内容的具体来源文档。
        
        参数:
            answer (str): 生成的答案文本
            chunks (list): 参考的文档块列表
            chunk_v (list): 文档块的向量表示列表
            embd_mdl: 嵌入模型，用于将文本编码为向量
            tkweight (float): token相似度权重，默认0.1
            vtweight (float): 向量相似度权重，默认0.9
        
        返回:
            tuple: (包含引用标记的答案文本, 被引用的块索引集合)
        """
        # 数据验证：确保文档块数量与向量数量一致
        assert len(chunks) == len(chunk_v), "文档块数量与向量数量不匹配"
        
        # 边界条件处理：如果没有文档块，直接返回原答案
        if not chunks:
            return answer, set([])
        
        # 第一步：智能分割答案文本
        # 首先按代码块（```）分割，保护代码块不被进一步分割
        pieces = re.split(r"(```)", answer)
        
        if len(pieces) >= 3:  # 包含代码块的情况
            i = 0
            pieces_ = []
            while i < len(pieces):
                if pieces[i] == "```":  # 遇到代码块开始标记
                    st = i  # 记录代码块开始位置
                    i += 1
                    # 寻找代码块结束标记
                    while i < len(pieces) and pieces[i] != "```":
                        i += 1
                    if i < len(pieces):  # 找到结束标记
                        i += 1
                    # 将整个代码块作为一个片段保存
                    pieces_.append("".join(pieces[st: i]) + "\n")
                else:
                    # 非代码块部分，按句子分割
                    # 正则表达式匹配中英文句号、问号、感叹号等句末标点
                    pieces_.extend(
                        re.split(
                            r"([^\|][；。？!！\n]|[a-z][.?;!][ \n])",
                            pieces[i]))
                    i += 1
            pieces = pieces_
        else:
            # 没有代码块的情况，直接按句子分割
            pieces = re.split(r"([^\|][；。？!！\n]|[a-z][.?;!][ \n])", answer)
        
        # 第二步：处理分割后的片段，将标点符号合并到前一个片段
        for i in range(1, len(pieces)):
            if re.match(r"([^\|][；。？!！\n]|[a-z][.?;!][ \n])", pieces[i]):
                pieces[i - 1] += pieces[i][0]  # 将标点加到前一片段
                pieces[i] = pieces[i][1:]      # 移除已合并的标点
        
        # 第三步：过滤有效片段（长度大于等于5的片段）
        idx = []      # 有效片段在原始pieces中的索引
        pieces_ = []  # 有效片段内容
        for i, t in enumerate(pieces):
            if len(t) < 5:  # 跳过过短的片段
                continue
            idx.append(i)
            pieces_.append(t)
        
        logging.debug("Answer split completed: piece_count=%s", len(pieces_))
        
        # 如果没有有效片段，返回原答案
        if not pieces_:
            return answer, set([])

        # 第四步：对答案片段进行向量编码
        ans_v, _ = embd_mdl.encode(pieces_)
        
        # 第五步：向量维度对齐处理
        # 确保所有文档块向量与答案向量维度一致
        for i in range(len(chunk_v)):
            if len(ans_v[0]) != len(chunk_v[i]):
                # 维度不匹配时用零向量填充
                chunk_v[i] = [0.0] * len(ans_v[0])
                logging.warning("查询向量与文档块向量维度不匹配: {} vs. {}".format(
                    len(ans_v[0]), len(chunk_v[i])))

        # 最终验证向量维度一致性
        assert len(ans_v[0]) == len(chunk_v[0]), "查询向量与文档块向量维度不匹配: {} vs. {}".format(
            len(ans_v[0]), len(chunk_v[0]))

        # 第六步：对文档块进行分词处理
        # 移除WWW等无关内容并进行分词
        chunks_tks = [rag_tokenizer.tokenize(self.rmWWW(ck)).split()
                      for ck in chunks]
        
        # 第七步：基于混合相似度进行引用匹配
        cites = {}        # 存储引用关系：{片段索引: [引用的块索引列表]}
        thr = 0.63        # 初始相似度阈值
        
        # 动态调整阈值直到找到引用或阈值过低
        while thr > 0.3 and len(cites.keys()) == 0 and pieces_ and chunks_tks:
            for i, a in enumerate(pieces_):
                # 计算当前答案片段与所有文档块的混合相似度
                sim, tksim, vtsim = self.hybrid_similarity(
                    ans_v[i],           # 当前答案片段向量
                    chunk_v,            # 所有文档块向量
                    rag_tokenizer.tokenize(self.rmWWW(pieces_[i])).split(),  # 当前片段tokens
                    chunks_tks,         # 所有文档块tokens
                    tkweight,           # token权重
                    vtweight            # 向量权重
                )
                
                # 设置动态阈值（略低于最大相似度）
                mx = np.max(sim) * 0.99
                logging.debug(
                    "Citation similarity computed: piece_index=%s max_similarity=%s",
                    i,
                    mx,
                )
                
                if mx < thr:  # 相似度不够高，跳过
                    continue
                
                # 找出相似度超过阈值的文档块（最多4个）
                cites[idx[i]] = list(
                    set([str(ii) for ii in range(len(chunk_v)) if sim[ii] > mx]))[:4]
            
            # 降低阈值继续尝试
            thr *= 0.8

        # 第八步：构建带引用标记的最终答案
        res = ""           # 最终结果字符串
        seted = set([])    # 已使用的引用标记集合（避免重复）
        
        for i, p in enumerate(pieces):
            res += p  # 添加原始片段内容
            
            # 检查当前片段是否在有效片段列表中
            if i not in idx:
                continue
            
            # 检查当前片段是否有引用
            if i not in cites:
                continue
            
            # 验证引用索引的有效性
            for c in cites[i]:
                assert int(c) < len(chunk_v), f"引用索引 {c} 超出范围"
            
            # 添加引用标记（格式：##索引$$）
            for c in cites[i]:
                if c in seted:  # 避免重复引用
                    continue
                res += f" ##{c}$$"  # 添加引用标记
                seted.add(c)        # 记录已使用的引用

        return res, seted
