# Lumen 项目 RAG 检索算法技术文档

更新时间：2026-07-15

## 1. 文档目标

本文档基于当前仓库实现，说明 Lumen 项目里真正参与 RAG 召回的检索算法、执行链路和关键参数，重点回答三个问题：

1. 项目里的 RAG 检索到底经过了哪些阶段。
2. “混合检索”在这套实现里具体是怎么做的。
3. 现有实现有哪些工程取舍、边界和容易误解的点。

本文档只讨论检索与重排，不展开答案生成、Prompt 编排和会话管理。

## 2. 一句话总结

当前项目的主 RAG 检索链路可以概括为：

“文档分块后写入 Elasticsearch，同时保存高权重词字段和 dense vector；查询时先做查询改写与向量化，再在 ES 中做受 `doc_ids` 白名单约束的候选召回，随后对前几页候选进行词项相似度与向量相似度加权重排，必要时再接入独立 rerank 模型完成二次重排。”

需要特别说明的是：

1. 项目对外称为“hybrid recall”。
2. 但从代码实现看，真正稳定发生的“混合”主要体现在查询构造和重排阶段。
3. 当向量字段存在时，ES 候选召回阶段实际会优先构造顶层 `knn` 查询，而不是把 `query_string` 和 `knn` 一起作为同层打分查询下发。

## 3. 相关代码入口

当前显式语义检索入口是受内部 token 保护的 RAG HTTP 路由：
`services/rag/api/routes/recall.py`。

Runtime Agent 当前不再持有独立的 `kb_hybrid_recall` 工具。Backend 会先校验
知识库与文档 scope，再把允许读取的 Markdown 物化到线程工作区；Agent 通过
受沙箱约束的文件工具读取这些材料。这条 Runtime 文件链路与本章描述的语义
检索实现是两个不同入口，不能再引用已经删除的旧 Agent 工具作为当前架构。

该服务入口落到以下底层实现：

1. `shared/python/recall_lib/retriever.py`
2. `shared/python/recall_lib/es_adapter.py`
3. `services/rag/core/nlp/query.py`
4. `services/rag/embedding/chunk_embedder.py`
5. `shared/python/recall_lib/es_connection.py`

## 4. 总体检索链路

### 4.1 索引阶段

文档进入知识库后，会先被切块，再由 `ChunkEmbedder` 为每个 chunk 生成向量，最终写入 Elasticsearch。这里同时保存了两类检索特征：

1. 词法检索字段：`content_ltks`、`title_tks`、`important_kwd`、`question_tks` 等。
2. 语义检索字段：`q_{dim}_vec`，例如 `q_1024_vec`。

这里有一个特别容易误解的点，需要单独说明：

1. `chunk` 在索引阶段就会被分词，不是只有用户问题在查询时才分词。
2. 具体来说，chunk 正文会先生成 `content_ltks`，再基于它生成 `content_sm_ltks`；标题也会生成 `title_tks` 和 `title_sm_tks`。
3. 这些字段写入 ES 时使用的是 `whitespace_analyzer`，说明应用层会先把 token 处理成空格分隔串再入库，ES 主要负责建立倒排和执行匹配，而不是等到查询时才对 chunk 原文做一次中文分词。

换句话说，这套实现里的全文检索不是“只把原始 chunk 文本扔进 ES，等用户提问时再现场分词匹配”，而是“索引阶段先把 chunk 预分词并落成多个词法字段，查询阶段再把用户问题分词/改写后去匹配这些字段”。

对应实现：

1. `services/rag/embedding/chunk_embedder.py:288-340`
2. `shared/python/recall_lib/es_connection.py:100-190`
3. `services/rag/api/common_utils.py:185-197`
4. `services/rag/core/nlp/__init__.py:252-255`
5. `shared/python/recall_lib/es_connection.py:131-164`

### 4.2 查询阶段

查询进入系统后会经过以下步骤：

1. 校验可检索范围，只允许在本次知识库白名单文档内召回。
2. 构造全文检索表达式，包含清洗、分词、同义词扩展、字段加权。
3. 生成 query embedding。
4. 在 ES 中按 `doc_ids` 和 `available_int=1` 过滤召回候选。
5. 对前几页候选做重排。
6. 过滤低于阈值的 chunk，返回最终结果。

这里第 2 步里的“分词”指的是查询侧分词和查询改写，用来生成 `query_string` 风格的检索表达式；它匹配的是索引阶段已经准备好的 `content_ltks`、`title_tks`、`content_sm_ltks` 等字段，而不是在召回时再临时对每个 chunk 原文重新做一次分词。

对应实现：

1. `services/rag/api/routes/recall.py:23-125`
2. `shared/python/recall_lib/retriever.py:353-547`
3. `shared/python/recall_lib/retriever.py:194-195`
4. `services/rag/core/nlp/query.py:127-201`

## 5. 索引与向量化算法

### 5.1 分块向量不是只看正文

`ChunkEmbedder` 会分别编码标题和内容，然后按权重线性组合：

`final_embedding = title_weight * title_embedding + (1 - title_weight) * content_embedding`

其中：

1. 标题来自 `docnm_kwd`
2. 内容优先取 `question_kwd`，没有时才退回 `content_with_weight`
3. 默认 `filename_embd_weight = 0.1`

对应实现：

1. `services/rag/embedding/chunk_embedder.py:288-329`

这意味着项目认为“文件名/标题信息”对 chunk 语义有辅助价值，但不会压过正文主体。

### 5.2 向量字段名是动态维度绑定

向量会被写入 `q_{维度}_vec`，例如 `q_1024_vec`。索引映射也会按维度动态创建或补齐，因此项目可以兼容不同 embedding 模型输出维度。

对应实现：

1. `services/rag/embedding/chunk_embedder.py:337-340`
2. `shared/python/recall_lib/es_connection.py:183-189`
3. `shared/python/recall_lib/es_connection.py:266-342`

### 5.3 ES 向量索引使用 cosine

Elasticsearch 里的 dense vector 映射明确配置了：

1. `index: true`
2. `similarity: "cosine"`

因此向量召回底层是基于余弦相似度的近邻搜索。

对应实现：

1. `shared/python/recall_lib/es_connection.py:183-189`

## 6. 全文检索算法

全文检索由 `FulltextQueryer` 负责，它不是简单把用户原问题丢给 ES，而是做了一层较重的查询改写。

### 6.1 查询预处理

预处理包括：

1. 繁简转换、全半角转换、小写化。
2. 清理标点和停用式疑问词。
3. 中英文查询分支处理。

对应实现：

1. `services/rag/core/nlp/query.py:89-172`

### 6.2 字段加权策略

全文检索字段的默认权重是：

1. `title_tks^10`
2. `title_sm_tks^5`
3. `important_kwd^30`
4. `important_tks^20`
5. `question_tks^20`
6. `content_ltks^2`
7. `content_sm_ltks^1`

这 7 个字段不是同一类数据，按用途可以分成三组：

1. 标题字段：`title_tks`、`title_sm_tks`
2. 重要词字段：`important_kwd`、`important_tks`、`question_tks`
3. 正文字段：`content_ltks`、`content_sm_ltks`

它们分别表示：

1. `title_tks`
   标题的常规分词结果。通常来自文件名或文档标题，去掉扩展名后再做 `rag_tokenizer.tokenize()`。因为标题往往高度概括主题，所以给了较高权重。
2. `title_sm_tks`
   标题的细粒度分词结果。它是 `title_tks` 再经过 `fine_grained_tokenize()` 得到的，更适合补召回标题里的短词、子词和更细的切分片段，所以权重低于 `title_tks`。
3. `important_kwd`
   重要关键词字段，`keyword` 类型，不走常规分词，更偏精确值匹配。它适合承载公司名、术语、产品名、法规名这类“命中即强信号”的词，所以权重最高。
4. `important_tks`
   重要词的分词版本，`text` 类型。它和 `important_kwd` 的区别是：前者偏精确关键词命中，后者偏分词后的全文匹配，适合重要词需要拆词召回的场景。当前 Reader 默认入库流程里它经常为空，更像是为上游补充重要词后的增强字段预留的位置。
5. `question_tks`
   问题导向词字段，`text` 类型。它用于存放更像“用户会怎么问”的词项或问句分词，适合 FAQ、问答库、标题即问题这类语料。当前 Reader 默认入库流程里这个字段也常常为空，但底层检索和重排逻辑都会识别它，所以它是一个保留的增强信号位。
6. `content_ltks`
   chunk 正文的常规分词结果。正文在入库时会先清理一部分表格 HTML 标签，再做 `rag_tokenizer.tokenize()`，这是最基础、覆盖面最大的全文检索字段。
7. `content_sm_ltks`
   chunk 正文的细粒度分词结果。它是 `content_ltks` 再做一次细分后的结果，用来补足常规分词对缩写、短语片段和局部词片的召回能力，所以权重最低，更像召回补充信号。

这里其实对应了两套不同层次的分词逻辑：

1. 粗粒度分词：`rag_tokenizer.tokenize()`
2. 细粒度分词：`rag_tokenizer.fine_grained_tokenize()`

它们的实现方式和目标并不一样。

先看粗粒度分词，也就是 `title_tks`、`content_ltks` 这类字段使用的 `tokenize()`。

它处理的是原始文本，整体流程是：

1. 先做预处理，把非字母数字字符替换为空格，再做全角转半角、小写化、繁体转简体。
2. 再按语言切片，把文本拆成中文片段和英文片段分别处理。
3. 英文片段走 `word_tokenize()`，随后再做词形还原和词干提取。
4. 中文片段走词典驱动的双向最大匹配：先跑前向最大匹配，再跑后向最大匹配。
5. 如果前后向结果有歧义，就对歧义片段做 DFS 枚举，再按词频、长词比例和切分数量评分，选择得分最高的切法。
6. 最后再做一次 `merge_()`，把可能被拆坏的特殊串重新并回去，比如 URL、带分隔符但在词典中的串。

所以粗粒度分词的核心目标是：

“把一段原始文本切成一组语义上尽量稳定、尽量像正常词的 token，优先保证主表达和主语义不要被拆得太碎。”

再看细粒度分词，也就是 `title_sm_tks`、`content_sm_ltks` 这类字段使用的 `fine_grained_tokenize()`。

它的输入已经不是原始文本了，而是粗分词之后得到的 token 串。也就是说，它做的是“二次拆分”，不是重新从头分词。

它的大致逻辑是：

1. 先把粗分词结果按空格拆开，逐个 token 处理。
2. 如果中文 token 占比很低，就走一个更简单的分支，主要按 `/` 再拆一遍，适合英文或符号型 token。
3. 对长度小于 3 的 token、纯数字 token，直接保留，不再细拆。
4. 对长度适中的 token，会再次调用 `dfs_()` 去枚举这个 token 的多种拆分可能。
5. 然后不是取主分词里偏“整词优先”的最佳结果，而是刻意选一套更细的候选切法，用来生成补充召回字段。
6. 但它也不会无脑越细越好。如果拆出来的英文子词过短、候选切分太差，或者 token 本身太长，它就直接保留原 token。

这意味着细粒度分词的核心目标不是“替代主分词”，而是：

“在不明显破坏主语义的前提下，把一部分复合词、长词、可拆词再拆开一点，用来补召回。”

两者最重要的区别可以直接概括成下面三点：

1. 输入不同。粗粒度分词处理原始文本；细粒度分词处理粗分词后的 token 串。
2. 目标不同。粗粒度分词追求主语义稳定和主 token 质量；细粒度分词追求补充召回和降低漏召。
3. 结果不同。粗粒度分词更偏“整词”；细粒度分词更偏“可控地再拆一层”，但不是越碎越好。

举个直观例子，如果正文里有一个词是“机器学习”：

1. 粗粒度分词更可能保留成 `机器学习`
2. 细粒度分词则可能进一步提供 `机器 学习`

再比如标题里有“知识图谱问答系统”：

1. `title_tks` 更可能保留 `知识图谱 问答系统` 这类主词
2. `title_sm_tks` 则可能补出 `知识 图谱 问答 系统`

这也是为什么在字段权重上，粗粒度字段通常高于细粒度字段：

1. 粗粒度字段更适合做主相关性判断
2. 细粒度字段更适合做补漏，不适合压过主字段

如果用一句话概括这 7 个字段的设计思路，就是：

“标题和重要词负责高精度命中，正文字段负责广覆盖召回，细粒度字段负责补漏，问答型字段负责贴近用户提问方式。”

对应实现：

1. `services/rag/core/nlp/query.py:39-48`
2. `services/rag/api/common_utils.py:185-197`
3. `shared/python/recall_lib/es_connection.py:125-166`
4. `services/rag/embed_store/chunk_store.py:182-190`
5. `services/rag/core/nlp/rag_tokenizer.py:666-773`
6. `services/rag/core/nlp/rag_tokenizer.py:806-861`

这个配置说明项目显式偏向：

1. 重要关键词
2. 标题
3. 问答型关键词

而不是把正文每个词一视同仁。

如果要单独看 `RagTokenizer` 的中文主分词算法，而不是从检索视角理解它，可以进一步参考：

`docs/RagTokenizer分词算法技术文档.md`

### 6.3 英文查询算法

英文查询会做三件事：

1. 对 token 计算权重。
2. 为 token 扩展同义词，并降低同义词权重到原词的 1/4。
3. 构造相邻词短语查询，并把短语权重放大到相邻词最大权重的 2 倍。

对应实现：

1. `services/rag/core/nlp/query.py:174-277`
2. `services/rag/core/nlp/term_weight.py:348-499`

这里的“对 token 计算权重”不是简单地数这个词出现了几次，而是给查询中的每个英文词分配一个“重要性分数”，后续会直接写进 ES 查询串里，形成类似下面这样的查询片段：

`(revenue^0.42 "income"^0.1050)`

其中：

1. `revenue` 是原词。
2. `0.42` 是原词权重。
3. `"income"^0.1050` 是同义词查询，权重大约是原词的 1/4。

也就是说，这里的 token 权重本质上是在回答一个问题：

“英文查询里的哪些词更值得被重点检索，哪些词应该弱化处理？”

#### 6.3.1 token 权重是怎么算出来的

英文查询进入 `_process_english_query()` 后，会先分词，再调用：

`self.tw.weights(tokens, preprocess=False)`

对应实现：

1. `services/rag/core/nlp/query.py:187-215`

`weights()` 会为每个 token 生成一个归一化后的 `(token, weight)` 二元组。这个 weight 不是单一规则，而是由多类特征混合得到：

1. 词频相关特征。
2. 文档频率相关特征。
3. 命名实体类型。
4. 词性。

对应实现：

1. `services/rag/core/nlp/term_weight.py:352-367`
2. `services/rag/core/nlp/term_weight.py:376-468`
3. `services/rag/core/nlp/term_weight.py:472-499`

从实现上看，可以近似理解为：

`weight ~= IDF-like_score * NER_weight * POS_weight`

然后再做一次归一化。

这几部分含义分别是：

1. `IDF-like_score`
   词越稀有、越不像常见泛词，权重通常越高。
2. `NER_weight`
   如果词被识别为公司名、地名、学校名、股票名等实体，权重会被抬高。
3. `POS_weight`
   名词通常比代词、连词、副词更重要。

这个项目里还有几个很具体的英文规则：

1. 1 到 2 个字符的纯英文 token 权重会被显著压低。
2. 没有频率统计的纯英文词，会被当成潜在专业词，给予较高频率权重。
3. 最后所有 token 权重会归一化，所以它们更像“相对重要性分布”，不是绝对分值。

#### 6.3.2 这个权重最后用在什么地方

token 权重最终会进入查询字符串本身。

在 `_build_english_query()` 中，每个词都会被写成：

`(token^w synonym_part)`

对应实现：

1. `services/rag/core/nlp/query.py:247-277`

所以“对 token 计算权重”在工程上真正的意义是：

1. 更重要的词会在 ES 查询中获得更高 boost。
2. 不那么重要的词不会完全删除，但影响力会变小。
3. 同义词也能继承原词的重要性，只是权重更低。

例如用户问：

`apple quarterly revenue growth`

系统并不是把这 4 个词平铺丢给 ES，而是更可能形成这种偏好：

1. `apple` 这种实体词更重要。
2. `revenue` 这种核心业务词更重要。
3. `growth` 这种修饰性词有用，但未必和核心实体、指标一样重要。

#### 6.3.3 “构造相邻词短语查询”是什么意思

这里的“相邻词短语查询”指的是：

系统不会只搜索单个词，还会把相邻两个 token 组合成 phrase query，再额外放进查询串里。

例如输入：

`annual revenue growth`

除了单词级查询，还会额外生成：

1. `"annual revenue"^...`
2. `"revenue growth"^...`

这里不是“从所有 token 里任意挑两个词做组合”，而是严格按 token 在查询里的原始顺序，使用长度为 2 的滑动窗口逐个构造：

1. 先取第 1 个和第 2 个 token
2. 再取第 2 个和第 3 个 token
3. 以此类推

所以：

1. 会生成 `"annual revenue"`、`"revenue growth"`
2. 不会生成 `"annual growth"`
3. 也不会把词序反过来生成 `"growth revenue"`

对应实现：

1. `services/rag/core/nlp/query.py:266-271`

短语权重计算方式是：

`phrase_weight = max(left_weight, right_weight) * 2`

也就是说，只要两个相邻词里有一个本来就很重要，那么这个短语会被赋予更强的检索信号。

#### 6.3.4 为什么要额外加短语查询

因为单词匹配只能表达“这些词都出现过”，但表达不了“这些词在语义上是否紧邻、是否共同构成一个概念”。

短语查询的作用主要有三点：

1. 强化固定搭配。
   例如 `cash flow`、`operating margin`、`market share`。
2. 强化局部语义完整性。
   命中 `"revenue growth"` 的 chunk，通常比只分别命中 `revenue` 和 `growth` 的 chunk 更相关。
3. 降低词序被打散带来的噪声。
   如果两个词分别出现在很远位置，单词匹配可能仍会命中，但短语查询不会给它额外加分。

所以“构造相邻词短语查询”不是简单多查一遍，而是在告诉 ES：

“如果这两个词不仅都出现，而且是连在一起出现的，那么它更可能是我真正要找的概念。”

#### 6.3.5 英文查询最终长什么样

综合起来，英文查询最终不是一串裸词，而是由三类片段拼出来的：

1. 原词查询：`token^weight`
2. 同义词查询：`"synonym"^(weight/4)`
3. 相邻短语查询：`"left right"^(max_weight*2)`

对应实现：

1. `services/rag/core/nlp/query.py:223-277`

因此，英文查询算法的本质可以概括成一句话：

“先判断每个英文词的重要性，再把原词、同义词和相邻短语一起编码成带权查询串，交给 ES 做词法召回。”

### 6.4 中文查询算法

中文查询更复杂，主要包含：

1. 先按 term 拆分，再给每个 term 计算权重。
2. 对 term 做同义词扩展。
3. 对较长中文词做细粒度分词。
4. 把原词、细粒度词、邻近短语、同义词组合成一个带权查询片段。
5. 整体使用 `minimum_should_match` 控制最低匹配比例。

对应实现：

1. `services/rag/core/nlp/query.py:279-462`

这里的 `term` 不是最终检索时使用的最小 token，而是 `self.tw.split(txt)` 切出来的第一层查询单元。

对应实现：

1. `services/rag/core/nlp/query.py:294-300`
2. `services/rag/core/nlp/term_weight.py:309-346`

`split()` 的行为比较像“粗粒度切分”：

1. 默认按空白切开输入文本。
2. 如果相邻两段都以英文字母结尾，且都不是功能词，还会把它们合并成一个整体。

例如：

1. `机器学习 框架` 里的 `机器学习`、`框架` 可以分别作为 term。
2. `machine learning` 会被合并成一个 term，而不是两个独立 term。

所以这里的层级关系更准确地说是：

1. `term` 是第一层查询单元。
2. `term_weights = self.tw.weights([term])` 之后，才会把这个 term 进一步拆成更细的子词并计算每个子词权重。

也就是说，中文查询这一步不是“整句 -> token”，而更像：

`整句 -> term -> term 内部更细的 token/子词 -> 带权查询片段`

举个例子，如果用户问题里有：

`新能源行业 发展趋势`

那么一个可能的理解方式是：

1. 第一层 term 可能是 `新能源行业` 和 `发展趋势`
2. 进入 `weights([term])` 之后，`新能源行业` 还可能继续被拆成更细的子词，再分别赋权

所以文档里这句“先按 term 拆分，再给每个 term 计算权重”，更准确的理解应该是：

“先把中文查询切成若干较粗的语义单元，再在每个语义单元内部继续做细化和加权。”

#### 6.4.1 一个完整示例

假设用户问题是：

`Transformer 的位置编码有什么作用`

经过预处理后，系统会先做第一层 `term` 切分。为了便于说明，这里只关注其中一个 `term`：

`位置编码`

接下来进入 `self.tw.weights([term])`。这一层不是去 ES 为每个 token 单独发起一次检索请求，而是在应用内部基于本地统计信息计算权重：

1. `rag_tokenizer.freq()` 提供词频统计。
2. `term.freq` 提供文档频率统计。
3. 再结合 NER 和词性权重做归一化。

对应实现：

1. `services/rag/core/nlp/rag_tokenizer.py:410-424`
2. `services/rag/core/nlp/term_weight.py:154-158`
3. `services/rag/core/nlp/term_weight.py:472-499`

为了便于理解，下面假设这一轮算出的结果是：

```text
term_weights = [("位置编码", 0.78)]
```

这里的 `0.78` 只是示意，真实值不是固定常数，而是 `weights()` 根据局部词频、文档频率、NER、词性等特征动态算出来的归一化结果。

然后 `_build_chinese_term_query()` 会继续做两类扩展：

1. 同义词扩展：假设 `self.syn.lookup("位置编码")` 命中了 `positional encoding`
2. 细粒度分词：假设 `rag_tokenizer.fine_grained_tokenize("位置编码")` 得到 `位置 编码`

于是这个 token 会被扩写成下面这样的查询片段：

```text
((位置编码 OR ("positional encoding")^0.2) OR "位置 编码" OR ("位置 编码"~2)^0.5)^0.78
```

可以按层理解：

1. `位置编码`
   原词直接匹配，语义最精准，是主体部分。
2. `("positional encoding")^0.2`
   同义词补召回，但只给原词较低权重，因此乘了固定系数 `0.2`。
3. `"位置 编码"`
   允许文档按更细粒度切词后仍然能命中。
4. `("位置 编码"~2)^0.5`
   这是细粒度词的邻近匹配，要求 `位置` 和 `编码` 在文档里彼此接近；由于它比原词直匹配更宽松，所以只给固定系数 `0.5`。
5. 最外层 `^0.78`
   这是当前 token 在这个 term 内部的动态权重，不是写死的常数。

上面这个片段正对应代码里的两步：

1. 同义词降权：`^0.2`
2. 细粒度邻近短语降权：`^0.5`

对应实现：

1. `services/rag/core/nlp/query.py:400-402`
2. `services/rag/core/nlp/query.py:404-407`

如果一个 `term` 在 `weights([term])` 之后得到的不止一个子词，`_process_chinese_query()` 还会额外补一个 term 级邻近短语：

```text
("位置 编码"~2)^1.5
```

这个 `1.5` 也是代码里写死的启发式增强系数，用来鼓励“这些词不仅都出现，而且在文本中彼此靠近”的情况。

这里同样不是“随便从 term 里取两个词拼一下”，而是按 `rag_tokenizer.tokenize(term)` 的输出顺序，把这个 term 的分词结果整体拼成一个邻近短语。

例如如果：

1. `tokenize("位置编码") -> 位置 编码`
2. `tokenize("知识图谱问答") -> 知识图谱 问答`

那么补出来的会分别是：

1. `("位置 编码"~2)^1.5`
2. `("知识图谱 问答"~2)^1.5`

而不会跨位置、跨 term 去任意组合成别的词对。

对应实现：

1. `services/rag/core/nlp/query.py:312-315`

再往下看 `_build_chinese_term_query()` 里的细粒度补召回，也遵循同样的规则：

1. 先对单个词 `tk` 调用 `fine_grained_tokenize(tk)`
2. 按得到的 token 顺序拼出 `"token1 token2 ..."`
3. 再生成普通短语和邻近短语两个版本

例如：

1. `fine_grained_tokenize("位置编码") -> 位置 编码`

则补出来的是：

1. `"位置 编码"`
2. `("位置 编码"~2)^0.5`

这里也不是任意抽两个字词做排列组合，而是严格沿用细粒度分词结果的原始顺序。

对应实现：

1. `services/rag/core/nlp/query.py:404-407`

最后，这些片段会被拼进一个更大的 `MatchTextExpr.matching_text` 查询串，再统一交给 ES 执行全文检索。也就是说：

1. token 权重计算阶段不访问 ES。
2. 构造出整条带权查询串之后，才会向 ES 发起正式查询。

在主检索实现里，`question()` 调用时传入的 `min_match` 是 `0.3`，也就是把最小匹配门槛放宽到了 30%。

对应实现：

1. `shared/python/recall_lib/retriever.py:196-199`

#### 6.4.2 `question()` 最终返回的带权查询表达式长什么样

无论中英文，`FulltextQueryer.question()` 最终返回的都不是“原始问题字符串”，而是一个 `MatchTextExpr` 对象。它本质上是一个 4 元组：

1. `fields`
   要打到哪些 ES 字段上，例如 `title_tks^10`、`important_kwd^30`、`content_ltks^2`。
2. `matching_text`
   真正传给 ES `query_string.query` 的带权查询串。
3. `topn`
   返回数量上限，当前这里固定写成 `100`。
4. `extra_options`
   额外控制项，例如 `minimum_should_match`。

如果用结构化伪代码表示，它大致等价于：

```text
MatchTextExpr(
  fields=[
    "title_tks^10",
    "title_sm_tks^5",
    "important_kwd^30",
    "important_tks^20",
    "question_tks^20",
    "content_ltks^2",
    "content_sm_ltks"
  ],
  matching_text="<带权 query_string 查询串>",
  topn=100,
  extra_options={"minimum_should_match": 0.3 或省略}
)
```

这里最关键的是 `matching_text`。它不是简单的“分词后空格拼接”，而是已经带上了：

1. token 权重，例如 `token^0.78`
2. 同义词降权，例如 `("synonym")^0.2`
3. 细粒度词补召回，例如 `"位置 编码"`
4. 邻近短语，例如 `("位置 编码"~2)^0.5`
5. term 之间的布尔连接，例如 `OR`

英文查询的最终形态，结构上更接近下面这样：

```text
(apple^0.82 "macintosh"^0.2050) (revenue^0.91 "income"^0.2275) "apple revenue"^1.82
```

它可以拆成三类信号：

1. 原词：`apple^0.82`
2. 同义词：`"macintosh"^0.2050`
3. 相邻短语：`"apple revenue"^1.82`

中文查询的最终形态，结构上更接近下面这样：

```text
((((位置编码 OR ("positional encoding")^0.2) OR "位置 编码" OR ("位置 编码"~2)^0.5)^0.78 ("位置 编码"~2)^1.5)^5 OR ("positional encoding")^0.7)
```

这个例子里的符号含义分别是：

1. `^0.78`
   当前 term 或子词的动态权重。
2. `^0.2`
   同义词相对原词的降权系数。
3. `"位置 编码"`
   细粒度切词后的短语匹配。
4. `~2`
   邻近约束，表示词项之间允许一定距离。
5. 最外层 `OR`
   表示主表达式和扩展表达式之间是并列召回关系。

需要强调的是，上面两个例子都是“结构示意”，真实字符串会随着：

1. `term_weight` 算出来的动态权重
2. `synonym.Dealer()` 是否命中同义词
3. `fine_grained_tokenize()` 的切分结果

而变化。但不变的是：最终传给 ES 的不是用户原句，而是这一整条已经编码过权重、短语和扩展关系的 `query_string` 查询串。

对应实现：

1. `services/rag/core/utils/doc_store_conn.py:73-89`
2. `services/rag/core/nlp/query.py:184-201`
3. `services/rag/core/nlp/query.py:296-336`
4. `services/rag/core/nlp/query.py:360-410`

### 6.5 词法检索底层依赖 ES 倒排能力

项目没有自己实现一套独立的 BM25 或倒排引擎，而是把构造后的查询表达式直接交给 Elasticsearch。因此，词法检索的底层能力本质上来自 ES 的三部分：

1. 倒排索引。
2. 分析器和字段类型。
3. ES 内置相关性打分。

更准确地说，这里的“词法检索”不是一段自研算法，而是“Reader 负责把 query 改写好，ES 负责基于倒排索引完成召回和初始打分”。

#### 6.5.1 哪些字段真正参与了倒排检索

RAG 检索主链路里，全文查询最终会打到 `FulltextQueryer.query_fields` 指定的字段上：

1. `title_tks`
2. `title_sm_tks`
3. `important_kwd`
4. `important_tks`
5. `question_tks`
6. `content_ltks`
7. `content_sm_ltks`

这些字段在索引映射中都被建成了适合倒排检索的 ES 字段：

1. `title_tks`、`title_sm_tks`、`important_tks`、`question_tks`、`content_ltks`、`content_sm_ltks` 是 `text` 字段。
2. `important_kwd` 是 `keyword` 字段。

对应实现：

1. `services/rag/core/nlp/query.py:39-48`
2. `shared/python/recall_lib/es_connection.py:125-166`

这组字段的设计非常关键，因为它说明项目并不是主要在原始正文 `content_with_weight` 上做全文检索，而是更依赖“预处理后的检索字段”：

1. `*_tks` / `*_ltks` 更接近已经整理好的词项序列。
2. `important_kwd` 则承担精确关键词匹配的作用。

#### 6.5.2 倒排索引是如何建出来的

索引 mapping 里定义了两种 analyzer：

1. `text_analyzer`
2. `whitespace_analyzer`

其中：

1. `text_analyzer` 使用 `standard tokenizer + lowercase`
2. `whitespace_analyzer` 使用 `whitespace tokenizer + lowercase`

对应实现：

1. `shared/python/recall_lib/es_connection.py:100-117`

这意味着项目对词法字段采取了两种思路：

1. 对原始内容字段 `content_with_weight`，允许 ES 用标准 tokenizer 做常规切词。
2. 对 `*_tks` / `*_ltks` 这类“已经被上游处理过”的字段，更多是按空白切分，尽量保留上游生成的 token 边界。

这也是为什么项目里的倒排检索不是“把一整段原文交给 ES 自己分词”那么简单，而是“先由 DeepRAG 侧生成结构化 token 字段，再由 ES 建倒排索引”。

#### 6.5.3 查询时发给 ES 的 DSL 是什么

在主 RAG 链路里，`ESAdapter` 会把 `MatchTextExpr` 翻译成一个 ES `query_string` 查询，并挂到 `bool.must` 中：

1. `fields` 来自 `FulltextQueryer.query_fields`
2. `query` 是上一步构造出的带权查询串
3. `type` 是 `best_fields`
4. `minimum_should_match` 由上层透传
5. `boost` 使用 `1 - vector_similarity_weight`

对应实现：

1. `shared/python/recall_lib/es_adapter.py:216-231`

这里有两个实现特点：

1. 查询字符串不是用户原句，而是 `FulltextQueryer` 改写后的表达式，里面已经包含词权重、短语、同义词和邻近匹配。
2. 字段权重不是在 ES mapping 里定义死的，而是动态编码在 `fields` 参数中，例如 `title_tks^10`、`important_kwd^30`。

也就是说，ES 负责执行查询，但“查什么、哪些词更重要、哪些字段更重要”是由 Reader 这一层先算好的。

如果当前走的是纯文本路径，那么 ES 最终收到的 DSL 结构，大致就是下面这样：

```json
{
  "query": {
    "bool": {
      "must": [
        {
          "query_string": {
            "fields": [
              "title_tks^10",
              "title_sm_tks^5",
              "important_kwd^30",
              "important_tks^20",
              "question_tks^20",
              "content_ltks^2",
              "content_sm_ltks"
            ],
            "type": "best_fields",
            "query": "((((位置编码 OR (positional encoding)^0.2) OR \"位置 编码\" OR (\"位置 编码\"~2)^0.5)^0.78 ...))",
            "minimum_should_match": "30%",
            "boost": 0.7
          }
        }
      ],
      "filter": [
        { "terms": { "doc_id": ["docA", "docB"] } },
        { "term": { "available_int": 1 } }
      ]
    }
  },
  "_source": ["docnm_kwd", "content_ltks", "title_tks", "important_kwd", "question_tks", "content_with_weight"],
  "from": 0,
  "size": 128
}
```

这个 JSON 里有三个点最值得注意：

1. `fields` 里的 `^10`、`^30` 是字段级 boost，表示“相同词项命中不同字段时，字段重要性不同”。
2. `query` 里的 `^0.78`、`^0.2`、`~2` 是词项级或短语级约束，表示“同一个字段内部，哪些词更重要、哪些只是补充召回”。
3. `minimum_should_match` 在上层通常以浮点值传入，比如 `0.3`，适配器下发给 ES 时会转成 `"30%"`。

所以这里其实叠了两层权重：

1. 字段层权重：由 `fields` 决定。
2. 词项层权重：由 `matching_text` 决定。

对应实现：

1. `services/rag/core/nlp/query.py:39-48`
2. `shared/python/recall_lib/es_adapter.py:215-229`

#### 6.5.4 ES 在这里具体做了什么

一旦查询进入 ES，倒排索引会承担这些职责：

1. 找出哪些文档块包含查询词项。
2. 利用字段级倒排结构快速完成多字段匹配。
3. 根据字段权重、词频、文档频率和匹配情况计算初始相关性分数。
4. 结合 `minimum_should_match` 控制召回宽松度。

虽然代码里没有手写 BM25 公式，但由于 mapping 中没有自定义 similarity，`text` 字段会使用 ES 默认相关性模型。因此可以把这里理解为：

“项目把候选词项、字段权重和查询结构组织好，ES 再用默认倒排检索相关性模型完成词法召回与初排。”

其中 `keyword` 字段和 `text` 字段的行为不同：

1. `keyword` 字段更适合精确值命中，例如 `important_kwd`
2. `text` 字段更适合常规全文相关性匹配，例如 `content_ltks`、`title_tks`

不过这里有一个边界条件要特别说明：

上面这段“ES 基于 `query_string` 完成词法召回和初始打分”的描述，严格来说只对应纯文本路径，或者“向量字段不存在时的降级路径”。如果向量路径开启且目标索引里存在对应的 `q_{dim}_vec` 字段，候选阶段的 ES 请求会切换成顶层 `knn`，这时 ES 首轮排序不再按 `query_string` 相关性完成，而是按向量近邻结果完成。这个行为在第 8.2 节展开说明。

#### 6.5.5 为什么这里不是普通的全文搜索

这套词法检索和常见“搜索原文”的方式相比，有两个明显差异。

第一，它用的是结构化检索字段，而不是只搜原始正文。

例如：

1. 标题词有单独字段。
2. 重要关键词有单独字段。
3. 问题导向词有单独字段。
4. 正文词和细粒度词分开存。

第二，查询不是裸文本，而是经过了较重的 query rewrite。

例如：

1. 中文会做 term 拆分、细粒度分词和同义词扩展。
2. 英文会做 token 权重、同义词扩展和相邻短语构造。
3. 查询里会显式带上权重和短语约束。

所以更准确的说法是：

“这里底层依赖 ES 倒排索引，但上层已经把文档和 query 都预处理成了更适合检索的形态。”

#### 6.5.6 倒排检索与后续重排的关系

词法检索在这条链路里不是终点，而是后续混合重排的一个输入信号。

它会在两个地方继续发挥作用：

1. `FulltextQueryer.question()` 产生的 `keywords` 会被保留下来。
2. 重排阶段会根据这些 `keywords` 和 chunk 的 token 集重新计算 `token_similarity`。

对应实现：

1. `shared/python/recall_lib/retriever.py:257-275`
2. `shared/python/recall_lib/retriever.py:330-351`
3. `services/rag/core/nlp/query.py:495-550`

因此，ES 倒排检索的作用可以分成两层：

1. 第一层是“在 ES 内部完成词法召回和初始排序”。
2. 第二层是“把 query 关键词继续传给应用层，用于后续混合重排”。

这也是当前实现里词法信号仍然很强的原因。即使首轮候选偏向向量 KNN，词法侧的关键词、字段权重和 token 相似度仍会在后续排序里继续影响最终结果。

对应实现：

1. `shared/python/recall_lib/es_adapter.py:216-231`
2. `backend/modules/knowledge/services/chunk_service.py:108-176`

## 7. 向量召回算法

### 7.1 查询向量生成

检索时会调用 embedding 模型的 `encode_queries` 生成 query vector，并根据向量长度自动推断要查询的 ES 向量字段名：

1. `qv = emb_mdl.encode_queries(question)`
2. `vector_column_name = f"q_{len(qv)}_vec"`

对应实现：

1. `shared/python/recall_lib/retriever.py:90-126`

### 7.2 ES 侧 KNN 召回

当向量字段存在时，适配器会构造顶层 `knn` 查询，关键参数包括：

1. `field`: 向量字段名
2. `query_vector`: 查询向量
3. `k`: `top_k`
4. `num_candidates`: `top_k * 2`
5. `similarity`: 相似度阈值
6. `filter`: 文档白名单和可用状态过滤

对应实现：

1. `shared/python/recall_lib/es_adapter.py:233-278`

这意味着候选集会被约束在：

1. 当前用户索引
2. 本次允许访问的 `doc_ids`
3. `available_int = 1`

对应实现：

1. `shared/python/recall_lib/retriever.py:228-237`
2. `shared/python/recall_lib/es_adapter.py:188-200`

## 8. “混合检索”与重排算法

### 8.1 名义上的混合配置

`DeepRagPureRetriever.search()` 会同时构造：

1. `MatchTextExpr`
2. `MatchDenseExpr`
3. `FusionExpr("weighted_sum")`

并根据 `vector_similarity_weight` 推导文本权重和向量权重。

对应实现：

1. `shared/python/recall_lib/retriever.py:192-223`

RAG `RecallRequest` 当前默认参数是：

1. `similarity_threshold = 0.1`
2. `vector_similarity_weight = 0.3`

对应实现：

1. `services/rag/api/schemas.py:66-92`

因此在当前配置下，混合权重更偏词法侧：

1. 文本权重约 0.7
2. 向量权重约 0.3

### 8.2 当前 ES 候选阶段的真实行为

这里是项目里最容易误解的点。

虽然代码里构造了 `FusionExpr`，但 `ESAdapter` 在发现向量字段存在后，会直接把查询体替换成顶层 `knn` 查询：

1. 文本查询仍然会被构造。
2. `FusionExpr` 也会被解析。
3. 但最终真正发给 ES 的请求主体会切换成 `{"knn": ...}`。

对应实现：

1. `shared/python/recall_lib/es_adapter.py:207-278`

这意味着：

1. 当向量字段存在时，ES 候选召回阶段更接近“向量召回 + 过滤”。
2. 文本信号没有作为完整同层查询参与 ES 初始打分。
3. 真正稳定的混合主要发生在后续重排阶段。

把它翻译成最终下发给 ES 的 JSON，大致就是下面这样：

```json
{
  "knn": {
    "field": "q_1024_vec",
    "query_vector": [0.013, -0.082, 0.441, "..."],
    "k": 1024,
    "num_candidates": 2048,
    "similarity": 0.1,
    "filter": {
      "bool": {
        "filter": [
          { "terms": { "doc_id": ["docA", "docB"] } },
          { "term": { "available_int": 1 } }
        ]
      }
    }
  },
  "_source": ["docnm_kwd", "content_ltks", "title_tks", "important_kwd", "question_tks", "content_with_weight", "q_1024_vec"],
  "from": 0,
  "size": 128
}
```

这个 JSON 和前面的纯文本 DSL 最大的区别是：

1. 顶层不再有 `query.bool.must.query_string`。
2. 文本查询虽然在应用层被构造出来了，但在向量字段存在时没有和 `knn` 一起作为同层打分查询下发。
3. `FusionExpr("weighted_sum")` 在当前适配器实现里主要被用来解析权重参数，而不是直接翻译成 ES 原生的混合打分 DSL。

所以“文本权重 0.7、向量权重 0.3”这类配置，在 ES 候选阶段并不会体现为一个真正的：

```text
score = 0.7 * text_score + 0.3 * vector_score
```

的 ES 同层打分公式。当前实现里更接近下面这个两段式流程：

1. 应用层先构造 `MatchTextExpr`、`MatchDenseExpr`、`FusionExpr`。
2. ES 候选阶段如果有向量字段，就只执行 `knn + filter`。
3. 应用层拿回候选后，再用 `hybrid_similarity = vtweight * vector_similarity + tkweight * token_similarity` 做重排。

这也是为什么文档一直强调：当前实现的“混合”，稳定发生的位置主要在重排阶段，而不是 ES 首轮召回阶段。

对应实现：

1. `shared/python/recall_lib/retriever.py:194-219`
2. `shared/python/recall_lib/es_adapter.py:205-272`
3. `shared/python/recall_lib/retriever.py:340-351`

所以更准确的说法是：

“当前实现是向量候选召回 + 词法/语义混合重排”，而不是严格意义上的 ES 同层混合打分召回。

### 8.3 默认重排算法

对于前 `rerank_page_limit` 页，系统会对候选进行显式重排。默认 `rerank_page_limit = 3`。

对应实现：

1. `shared/python/recall_lib/retriever.py:36-47`
2. `shared/python/recall_lib/retriever.py:434-476`

默认重排包含两部分：

1. 向量相似度：query vector 与 chunk vector 的 cosine similarity
2. 词项相似度：query keywords 与 chunk token 权重字典的重合度

最终计算公式：

`hybrid_similarity = vtweight * vector_similarity + tkweight * token_similarity`

对应实现：

1. `services/rag/core/nlp/query.py:464-493`

### 8.4 Chunk token 权重是通过“重复拼接”编码的

默认重排时，chunk 的 token 集不是简单拼接，而是通过重复次数编码字段权重：

1. `content_ltks`
2. `title_tks * 2`
3. `important_kwd * 5`
4. `question_tks * 6`

对应实现：

1. `shared/python/recall_lib/retriever.py:330-341`

这是一种轻量但有效的字段加权方式，本质上是在不引入额外学习模型的前提下，手工强化标题、关键字和问答型字段。

### 8.5 分页内重排窗口

为了给重排留出足够候选，前 3 页不会只取 `page_size` 个候选，而是扩成：

`max(page_size * rerank_page_limit, 128)`

对应实现：

1. `shared/python/recall_lib/retriever.py:403-417`

这说明系统采用的是：

1. 先宽召回
2. 再页内精排

而不是“先精确召回再直接返回”。

## 9. 可选 rerank 模型算法

当配置了 rerank 模型后，系统会切换到 `rerank_by_model()`：

1. 先为 chunk 准备 `content_ltks + title_tks + important_kwd`
2. 再用 query 构造关键词，算一遍 token similarity
3. 再调用外部 rerank 模型的 `similarity(question, docs)`
4. 最终仍按文本分数与 rerank 分数做线性加权

对应实现：

1. `shared/python/recall_lib/retriever.py:552-627`

最终公式仍然是：

`final_sim = text_weight * token_similarity + vector_weight * rerank_similarity`

也就是说，rerank 模型并不是完全接管排序，而是和词法信号继续混合。

## 10. 降级与容错策略

### 10.1 向量字段缺失时降级为纯文本搜索

如果索引里不存在对应维度的向量字段，或者执行向量查询时报了向量字段相关错误，适配器会退回只保留 `MatchTextExpr` 的纯文本查询。

对应实现：

1. `shared/python/recall_lib/es_adapter.py:120-144`
2. `shared/python/recall_lib/es_adapter.py:275-281`
3. `shared/python/recall_lib/es_adapter.py:516-541`

### 10.2 rerank 模型失败时降级为默认重排

如果外部 rerank 模型异常，系统不会终止召回，而是退回到默认的 `hybrid_similarity` 重排。

对应实现：

1. `shared/python/recall_lib/retriever.py:623-627`

### 10.3 查询向量会在检索链路内复用

系统会把首次生成的 query vector 保存在搜索结果对象里，后续重排直接复用，避免重复 embedding。

对应实现：

1. `shared/python/recall_lib/retriever.py:264-275`
2. `shared/python/recall_lib/retriever.py:428-455`

## 11. 检索权限与 Runtime 边界

RAG 的 `/api/hybrid-recall` 是受内部 token 保护的服务接口，只接受受信任调用方
提供的非空 `doc_ids` 白名单；它自身不负责最终用户身份或租户授权，也不能直接暴露
给浏览器。业务层若调用该接口，必须先完成知识库访问判定并固定文档范围。

Runtime Agent 走另一条链路：Backend 在 prepare/run admission 阶段验证 KB、
稳定的 materialization revision 和文件哈希，随后只把已授权 Markdown 放进线程
工作区。这条链路不依赖文档的 embedding 或 Elasticsearch 状态；Agent 的文件读取
能力受物化 manifest 约束，而不是靠旧的 `KBContext` 或浏览器提交的 `doc_ids` 约束。

## 12. 与项目里其他“搜索”能力的区别

为了避免混淆，这里把几个常见能力区分一下：

### 12.1 `/api/hybrid-recall`

这是 RAG 服务对受信任内部调用方提供的同类检索入口。模型参数和 `doc_ids`
由调用方显式提供，服务端仍强制要求非空文档白名单。

### 12.2 Runtime 工作区文件检索

Runtime Agent 读取 prepare 阶段物化的 Markdown。`read_file`、`bash` 等文件工具
做的是工作区内的字面读取或扫描，不等同于 Elasticsearch 语义检索。

### 12.3 `ChunkService.search_chunks`

这是后台管理/编辑场景的关键词搜索，用的是 `multi_match`，不走向量召回，也不走 RAG 重排。

对应实现：

1. `backend/modules/knowledge/services/chunk_service.py:108-176`

## 13. 当前实现的优点与边界

### 13.1 优点

1. 有明确的文档白名单约束，安全边界清晰。
2. 检索特征同时覆盖标题、关键词、正文和向量。
3. 支持无模型重排与有模型重排两种模式。
4. 向量字段维度自适应，便于切换 embedding 模型。
5. 出错时有文本降级路径，不会因为向量字段问题彻底失效。

### 13.2 边界

1. 当前 ES 候选召回阶段并不是严格意义上的“文本 + 向量联合打分”。
2. 词法与向量的真正融合主要在重排，而不是 ES 首轮召回。
3. 运行时默认 `vector_similarity_weight = 0.3`，整体更偏词法信号。
4. 内部 recall 接口与 Runtime 工作区是两种不同检索路径；模型、阈值和物化范围需要分别治理，不能假设结果天然一致。

## 14. 结论

如果用工程语言来定义 Lumen 当前的 RAG 检索算法，最准确的表述是：

“一套建立在 Elasticsearch 之上的、带白名单过滤的向量候选召回系统；它前接较重的查询改写，后接词法+语义混合重排，并可选接入独立 rerank 模型。”

所以它不是纯向量检索，也不是传统单轮 BM25；更像一条分阶段的检索流水线：

1. 索引阶段做结构化特征准备。
2. 查询阶段做 query rewrite 和 embedding。
3. 候选阶段偏向向量 KNN。
4. 排序阶段再把词法信号和语义信号重新混合。

这也是当前项目里“RAG 检索算法”的核心实现。

## 15. 高频面试题

下面给出 6 个基于本项目 RAG 模块最可能被问到的面试问题，以及更贴近当前实现的参考答案。

### 15.1 你们这个项目的 RAG 检索链路是怎样的

**参考回答：**

这套实现不是“用户问题 -> 直接向量检索 -> 把结果塞给模型”那么简单，而是一条分阶段流水线。

第一步是索引阶段。文档会先被切成 chunk，然后为每个 chunk 准备两类特征：

1. 词法特征，例如 `title_tks`、`content_ltks`、`important_kwd`、`question_tks`
2. 语义特征，也就是 `q_{dim}_vec` 这种 dense vector

第二步是查询阶段。用户问题进入后，系统会先做 query rewrite：

1. 中英文分支处理
2. 停用词清洗
3. 同义词扩展
4. 字段和词项加权

然后再生成 query embedding。

第三步是候选召回。当前实现会基于 ES 做受 `doc_ids` 白名单约束的召回，并且在有向量字段时更偏向顶层 `knn` 候选召回。

第四步是重排。对前几页候选，系统会再用词法相似度和向量相似度做混合重排；如果配置了 rerank 模型，还会把 rerank 分数继续混进最终排序。

所以如果面试官问一句话总结，我会说：

“这是一套基于 Elasticsearch 的、先结构化建索引、再做 query rewrite、再做候选召回、最后做混合重排的 RAG 检索链路。”

### 15.2 你们为什么说是混合检索，但又说它不是严格意义上的 hybrid retrieval

**参考回答：**

因为从工程实现上看，项目里确实同时构造了：

1. 文本查询 `MatchTextExpr`
2. 向量查询 `MatchDenseExpr`
3. 融合配置 `FusionExpr`

但是在 `ESAdapter` 里，当向量字段存在时，最终发给 ES 的查询主体会切成顶层 `knn`。这意味着 ES 首轮候选阶段并不是“文本 + 向量在同一层共同打分”。

真正稳定发生的混合主要在后续重排阶段：

1. 默认重排用 `hybrid_similarity = vector_similarity * vtweight + token_similarity * tkweight`
2. rerank 模式下用 `rerank_score` 替换默认向量分数，但仍然和 token similarity 继续线性融合

所以更准确的描述不是“ES 同层混合打分召回”，而是：

“向量候选召回 + 词法/语义混合重排”

如果面试官继续追问，我会补一句：

“这是一种很常见的工程取舍，因为这样实现简单、可控，而且当 query rewrite 做得比较重时，词法信号在重排阶段仍然能很好地发挥作用。”

### 15.3 你们为什么不只用向量检索，还要保留词法检索和 token 重排

**参考回答：**

因为很多知识库问答不是纯语义问题，词法信号仍然非常重要，尤其是这些场景：

1. 专有名词、公司名、学校名、股票名
2. 缩写和术语
3. 标题词、关键字段
4. 用户问的是精确字段，而不是开放语义

在这个项目里，词法信号存在于两层：

第一层是 ES 倒排检索。系统会把 query 改写成带字段权重、词项权重和短语的查询串，交给 ES 执行。

第二层是应用层重排。系统会保留 query keywords，再和 chunk 里的 `content_ltks`、`title_tks`、`important_kwd`、`question_tks` 做 token similarity。

而且这个 token similarity 不是简单交集计数，它本身带字段强化，比如：

1. `title_tks * 2`
2. `important_kwd * 5`
3. `question_tks * 6`

所以词法不是“备胎”，而是这套 RAG 相关性判断里的核心组成部分。

### 15.4 这个项目里怎么保证 Agent 检索不会越权

**参考回答：**

这个问题在这个项目里很关键，因为它不是全库开放搜索，而是带 KB 白名单约束的受限检索。

约束大致分三层：

第一层在 Lumen 侧。Lumen 会先校验用户是否有权限访问某个 KB，并且只把当前请求允许访问的 `kb_docs` 传给 Agent。

第二层在 Agent 工具侧。`kb_hybrid_recall` 不会自己随便搜，而是从当前请求上下文里提取 `doc_ids` 和 owner 信息，然后构造唯一索引名，只在这批文档范围内调用 `/api/hybrid-recall`。

第三层在 RAG 服务侧。`/api/hybrid-recall` 要求必须传 `doc_ids`，并且还要校验内部 token，所以它不是一个对外开放的裸搜索接口。

因此这套实现的边界不是“搜完再过滤”，而是“从入口开始就把检索范围收窄到白名单”。

如果面试官问安全收益，我会回答：

“这样做能避免 Agent 越权访问不在本次上下文里的文档，也避免把对象存储或整个 ES 索引暴露成任意搜索面。”

### 15.5 你们为什么使用 ES 来承载向量检索，而不是换成其它向量数据库

**参考回答：**

不能简单说“其它向量数据库做不到”。更准确的说法是：

“很多向量数据库现在也能做过滤、全文或 hybrid；但在 Reader 这个项目里，ES 和现有检索链路的匹配度更高，整体工程代价更低。”

举几个具体例子：

1. Qdrant 支持 metadata filtering，也支持全文检索和 phrase filter。
2. Milvus 支持 filtered search，也支持 BM25 全文检索，以及 dense + sparse 的 hybrid search。
3. pgvector 也明确支持和 Postgres full-text search 结合做 hybrid search。

所以这里的关键不是“只有 ES 能做”，而是“ES 在这个项目里有什么更明显的优势”。

第一，ES 更适合承接当前已经存在的重词法检索逻辑。

这个项目不是只有一个向量字段，而是强依赖一套比较重的词法信号：

1. `title_tks^10`
2. `important_kwd^30`
3. `question_tks^20`
4. 短语查询
5. 同义词扩展
6. analyzer 分词

举个例子，像“苹果 2024Q4 revenue guidance”这种问题，系统既希望向量理解语义，又希望 `苹果`、`2024Q4`、`revenue`、`guidance` 这些词按字段和权重参与排序。

ES 原生就擅长这种“多字段加权 + query DSL 控制 + 向量召回”的组合。很多专用向量库现在也能做 hybrid，但要把词法侧做到和当前实现一样细，往往需要额外配置 sparse/BM25 字段，或者在应用层自己做 fusion。

第二，ES 在这个项目里更适合做“受限检索”。

Lumen 的召回不是全库裸搜，而是常常要同时带：

1. `doc_ids` 白名单
2. `available_int=1`
3. 标题、关键词、正文等多字段条件
4. 向量 KNN 候选召回

举个例子，如果一次请求只允许搜某个 KB 下的 200 篇文档，那么系统希望在这 200 篇文档里同时做：

1. 白名单过滤
2. 关键词匹配
3. 向量近邻搜索

Qdrant 和 Milvus 也支持过滤，这不是 ES 独有；但 ES 的优势是这些过滤能力本来就和全文检索 DSL 结合得很成熟，可以直接放在同一套检索表达式和同一套索引体系里处理。

第三，ES 对当前项目的可解释性和排障更友好。

这个项目有很多字段权重和 query rewrite 逻辑，所以线上排障时很容易遇到这种问题：

“为什么这个 chunk 排在前面，是因为 `important_kwd` 命中了，还是因为 `q_1024_vec` 的 cosine 分数高？”

ES 在 explain、profile、字段级打分分析这类搜索可观测性上更成熟，比较适合这种“既要调词法，又要调向量，还要解释为什么排成这样”的系统。

第四，切换到专门向量数据库不是不行，但会引入额外工程成本。

比如如果换成 Qdrant 或 Milvus，通常会面临两种路径：

1. 保留 ES 做全文检索，再引入一套向量库做向量召回，形成双写和双检索链路。
2. 尽量把全文和向量都迁到新库里，但要重做现有 query rewrite、字段加权和排序调优。

再比如换成 pgvector，也确实可以走“Postgres + pgvector + FTS”的单库方案，但这会把当前 ES 上已经稳定的搜索模型，迁移成另一套 SQL/FTS/索引调优问题，本质上还是一笔迁移成本。

所以 ES 在这个项目里的优势，不是“它比所有向量库都强”，而是：

1. 对当前这套重词法的 RAG 检索更贴合
2. 不需要把全文和向量拆成两套基础设施
3. 白名单过滤、结构化过滤、全文检索、向量检索能放在同一底座里
4. 线上调参与排障更方便

如果面试官继续追问“那什么时候我会优先考虑专门的向量数据库”，我会回答：

“当业务更偏超大规模纯向量 ANN、弱词法、强多向量召回，或者需要围绕向量检索做更深的专用能力时，Milvus、Qdrant 这类专门向量库会更值得优先考虑；但对 Lumen 当前这种带权限白名单、重 query rewrite、重字段权重的知识库 RAG，ES 是更合适的工程选择。”

### 15.6 如果让你优化这个 RAG 检索模块，你最优先改什么

**参考回答：**

我会优先做三件事。

第一，把 ES 首轮召回改成更真实的 hybrid retrieval。

现在首轮候选更偏向顶层 `knn`，文本信号主要在重排补回来。如果业务里有很多专名词、精确字段和弱语义问法，我会考虑把文本和向量都纳入首轮候选生成，比如：

1. 双路召回后合并候选
2. 或者使用支持更明确混合打分的检索方式

第二，收敛内部入口的参数治理。

当前受信任调用方可以提交模型、阈值和权重。工程上应把它们收敛成版本化检索配置，
只让调用方选择已审核的 profile，并通过 A/B 验证后升级默认值。

第三，补充检索评估体系。

当前代码里检索逻辑已经比较完整，但如果要持续优化，还需要离线评估集和线上指标，例如：

1. recall@k
2. mrr
3. hit rate
4. 不同 query 类型下的命中表现

因为没有评估闭环，很难判断应该增强 query rewrite、调权重，还是换 rerank 模型。

如果要再补一句总结，我会说：

“我会优先把首轮召回做得更真实、把参数体系做得更一致、把评估体系补起来，这三件事对长期效果提升最大。”
