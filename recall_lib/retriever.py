#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""

核心算法逻辑，包括：
- MatchExpr体系（MatchTextExpr, MatchDenseExpr, FusionExpr）
- 真正的混合搜索和权重融合
- 完整的重排序算法
- 降级策略

"""

import sys
import logging
import asyncio
from typing import List, Dict, Any
from pathlib import Path
from dataclasses import dataclass

# 添加DeepRag根目录到路径
current_dir = Path(__file__).parent.absolute()
DeepRag_root = current_dir.parent / "rag"
sys.path.insert(0, str(DeepRag_root))

# 导入DeepRag核心组件
from rag.nlp import query
from rag.utils import rmSpace
from rag.utils.doc_store_conn import MatchDenseExpr, FusionExpr, OrderByExpr
import numpy as np

# 导入recall_lib内部的ES连接和适配器
from .es_connection import SimpleESConnection
from .es_adapter import ESAdapter


@dataclass
class DeepRagRetrievalConfig:
    """DeepRag召回配置"""
    index_names: List[str]                    # ES索引名称列表
    page: int = 1                            # 页码
    page_size: int = 10                      # 每页大小
    similarity_threshold: float = 0.1        # 相似度阈值（DeepRag默认0.1）
    vector_similarity_weight: float = 0.95   # 向量相似度权重
    top_k: int = 1024                        # 向量召回top-k
    highlight: bool = True                   # 是否高亮
    es_config: Dict[str, Any] = None         # ES配置
    rerank_page_limit: int = 3               # 重排序页面限制（DeepRag默认3）


class DeepRagPureRetriever:
    """
    - 使用MatchExpr体系进行搜索
    - 实现真正的FusionExpr混合搜索
    - 包含完整的重排序算法
    - 支持降级策略
    """
    
    def __init__(self, config: DeepRagRetrievalConfig):
        """
        初始化召回器
        
        Args:
            config: 召回配置
        """
        self.config = config
        
        # 设置默认ES配置
        es_config = config.es_config or {
            "hosts": "http://10.0.100.36:9201",
            "timeout": 600
        }
        
        # 创建简单的ES连接
        simple_es = SimpleESConnection(es_config.get("hosts", "http://localhost:9200"))

        # 创建ES适配器
        self.es_conn = ESAdapter(simple_es)

        # 创建DeepRag的查询器
        self.qryr = query.FulltextQueryer()
        
        logging.info(f"DeepRag召回器已初始化，索引: {config.index_names}")
    
    async def ensure_connected(self):
        """确保ES连接已建立"""
        if self.es_conn and hasattr(self.es_conn, 'es_conn'):
            await self.es_conn.es_conn.ensure_connected()
            logging.info("DeepRagPureRetriever ES连接已建立")
    
    async def get_vector(self, txt: str, emb_mdl, topk: int = 10, similarity: float = 0.1):
        """
        异步创建向量搜索表达式
        
        Args:
            txt: 查询文本
            emb_mdl: 向量化模型
            topk: top-k数量
            similarity: 相似度阈值
            
        Returns:
            MatchDenseExpr对象
        """
        import time
        t1 = time.time()
        # 使用 asyncio.to_thread 避免阻塞事件循环
        qv, _ = await asyncio.to_thread(emb_mdl.encode_queries, txt)
        logging.info(f"⏱️ [get_vector] embedding模型encode_queries耗时: {(time.time()-t1)*1000:.0f}ms")
        
        t2 = time.time()
        shape = np.array(qv).shape
        if len(shape) > 1:
            raise Exception(
                f"DeepRagPureRetriever.get_vector returned array's shape {shape} doesn't match expectation(exact one dimension).")
        
        embedding_data = [float(v) for v in qv]
        vector_column_name = f"q_{len(embedding_data)}_vec"
        logging.info(f"⏱️ [get_vector] 向量数据处理耗时: {(time.time()-t2)*1000:.0f}ms, 向量维度: {len(embedding_data)}")
        
        return MatchDenseExpr(
            vector_column_name, 
            embedding_data, 
            'float', 
            'cosine', 
            topk, 
            {"similarity": similarity}
        )

    def _create_empty_result(self):
        """
        创建空的搜索结果对象

        使用正确的方式创建空结果，避免可变默认值的问题

        Returns:
            具有空结果属性的对象
        """
        class EmptySearchResult:
            def __init__(self):
                # 每次创建新的实例时都创建新的可变对象，避免共享问题
                self.total = 0
                self.ids = []           # 每次都是新的列表
                self.field = {}         # 每次都是新的字典
                self.highlight = {}     # 每次都是新的字典
                self.aggregation = {}   # 每次都是新的字典
                self.keywords = []      # 每次都是新的列表

        return EmptySearchResult()

    async def search(self, req: Dict[str, Any], emb_mdl=None, highlight: bool = False):
        """
        异步搜索方法
        
        Args:
            req: 搜索请求
            emb_mdl: 向量化模型
            highlight: 是否高亮
            
        Returns:
            搜索结果对象
        """
        import time
        search_start = time.time()
        
        # 确保ES连接已建立
        await self.ensure_connected()
        
        qst = req.get("question", "")
        if not qst:
            # 返回空结果
            return self._create_empty_result()
        
        # 源字段
        t1 = time.time()
        src = req.get("fields", [
            "docnm_kwd", "content_ltks", "img_id", "title_tks", 
            "important_kwd", "position_int", "doc_id", "page_num_int", 
            "top_int", "create_timestamp_flt", "knowledge_graph_kwd",
            "question_kwd", "question_tks", "available_int", "content_with_weight"
        ])
        
        # 高亮字段
        highlightFields = ["content_ltks", "title_tks"] if highlight else []
        

        # 分页参数
        page = req.get("page", 1)
        page_size = req.get("size", 10)
        offset = (page - 1) * page_size
        limit = page_size
        logging.info(f"⏱️ [search] 参数准备耗时: {(time.time()-t1)*1000:.0f}ms")
        
        # 构建MatchExpr列表
        t2 = time.time()
        matchExprs = []
        
        # 1. 文本搜索
        matchText, keywords = self.qryr.question(qst, min_match=0.3)
        matchExprs.append(matchText)
        logging.info(f"⏱️ [search] 文本查询构建耗时: {(time.time()-t2)*1000:.0f}ms")
        
        # 2. 向量搜索和融合（如果有向量模型）
        t3 = time.time()
        q_vec = []  # 初始化查询向量
        if emb_mdl and req.get("vector", True):
            topk = req.get("topk", self.config.top_k)
            similarity = req.get("similarity", self.config.similarity_threshold)
            
            # 异步调用 get_vector
            matchDense = await self.get_vector(qst, emb_mdl, topk, similarity)
            q_vec = matchDense.embedding_data
            src.append(f"q_{len(q_vec)}_vec")
            
            # 创建融合表达式（使用DeepRag的权重配置）
            text_weight = 1.0 - self.config.vector_similarity_weight
            vector_weight = self.config.vector_similarity_weight
            fusionExpr = FusionExpr(
                "weighted_sum", 
                topk, 
                {"weights": f"{text_weight:.2f}, {vector_weight:.2f}"}
            )
            
            matchExprs.extend([matchDense, fusionExpr])
            logging.info(f"⏱️ [search] 向量查询构建耗时: {(time.time()-t3)*1000:.0f}ms, topk={topk}")
        
        # 排序
        orderBy = OrderByExpr()
        
        # 构建过滤条件
        t4 = time.time()
        condition = {}
        if "doc_ids" in req and req["doc_ids"]:
            condition["doc_ids"] = req["doc_ids"]
        
        # 添加 available_int 过滤（只检索启用的块）
        if "available_int" in req:
            condition["available_int"] = req["available_int"]
        logging.info(f"⏱️ [search] 过滤条件构建耗时: {(time.time()-t4)*1000:.0f}ms")
        
        # 执行异步搜索
        t5 = time.time()
        try:
            res = await self.es_conn.search(
                selectFields=src,
                highlightFields=highlightFields,
                condition=condition,
                matchExprs=matchExprs,
                orderBy=orderBy,
                offset=offset,
                limit=limit,
                indexNames=self.config.index_names,
                aggFields=["docnm_kwd"]
            )
            logging.info(f"⏱️ [search] ES查询耗时: {(time.time()-t5)*1000:.0f}ms")
            
            # 构建结果对象
            t6 = time.time()
            class SearchResult:
                def __init__(self, es_result, es_conn, keywords, query_vec=None):
                    self.total = es_conn.getTotal(es_result)
                    self.ids = es_conn.getChunkIds(es_result)
                    self.field = es_conn.getFields(es_result, src)
                    self.highlight = es_conn.getHighlight(es_result, keywords, "content_with_weight") if highlight else {}
                    self.aggregation = es_conn.getAggregation(es_result, "docnm_kwd")
                    self.keywords = keywords
                    self.query_vector = query_vec if query_vec else []  # 保存查询向量供后续复用
            
            # 从matchDense中提取查询向量（如果有的话）
            saved_query_vector = []
            if emb_mdl and req.get("vector", True):
                saved_query_vector = q_vec  # q_vec在前面已经定义
            
            result = SearchResult(res, self.es_conn, keywords, saved_query_vector)
            logging.info(f"⏱️ [search] 构建结果对象耗时: {(time.time()-t6)*1000:.0f}ms, 返回 {result.total} 条结果")
            logging.info(f"⏱️ [search] 保存查询向量（维度:{len(saved_query_vector)}）供后续复用")
            logging.info(f"⏱️ [search] ========== search总耗时: {(time.time()-search_start)*1000:.0f}ms ==========")
            return result
            
        except Exception as e:
            logging.error(f"搜索失败: {e}")
            # 返回空结果
            return self._create_empty_result()

    def rerank(self, chunk_ids: List[str], fields_data: Dict[str, Dict],
               question: str, keywords: List[str], query_vector: List[float],
               text_weight: float = 0.05, vector_weight: float = 0.95):
        """
        重排序算法

        Args:
            chunk_ids: 分块ID列表
            fields_data: 字段数据
            question: 查询问题
            keywords: 关键词列表
            query_vector: 查询向量
            text_weight: 文本权重
            vector_weight: 向量权重

        Returns:
            相似度分数数组
        """
        if not chunk_ids:
            return np.array([]), np.array([]), np.array([])

        logging.debug(f"开始重排序，分块数量: {len(chunk_ids)}")

        # 1. 提取向量数据
        ins_embd = []
        ins_tw = []

        for chunk_id in chunk_ids:
            chunk_data = fields_data.get(chunk_id, {})

            # 提取向量数据
            vector_field = f"q_{len(query_vector)}_vec" if query_vector else "q_1024_vec"
            chunk_vector = chunk_data.get(vector_field, [])
            if isinstance(chunk_vector, str):
                # 如果是字符串，尝试解析
                try:
                    import json
                    chunk_vector = json.loads(chunk_vector)
                except:
                    chunk_vector = []

            if not chunk_vector or len(chunk_vector) != len(query_vector):
                # 如果没有向量或维度不匹配，使用零向量
                chunk_vector = [0.0] * len(query_vector) if query_vector else [0.0] * 1024

            ins_embd.append(chunk_vector)

            # 构建token权重
            content_ltks = chunk_data.get("content_ltks", "").split()
            title_tks = [t for t in chunk_data.get("title_tks", "").split() if t]
            question_tks = [t for t in chunk_data.get("question_tks", "").split() if t]
            important_kwd = chunk_data.get("important_kwd", [])

            if isinstance(important_kwd, str):
                important_kwd = [important_kwd]

            # DeepRag的权重配置：content_ltks + title_tks * 2 + important_kwd * 5 + question_tks * 6
            tks = content_ltks + title_tks * 2 + important_kwd * 5 + question_tks * 6
            ins_tw.append(tks)

        # 2. 使用DeepRag的hybrid_similarity计算相似度
        sim, tksim, vtsim = self.qryr.hybrid_similarity(
            query_vector,    # avec: 查询向量
            ins_embd,        # bvecs: 文档向量列表
            keywords,        # atks: 查询关键词列表
            ins_tw,          # btkss: 文档token列表的列表
            text_weight,     # tkweight: token权重
            vector_weight    # vtweight: 向量权重
        )

        logging.debug(f"重排序完成，相似度范围: {np.min(sim):.4f} - {np.max(sim):.4f}")
        return sim, tksim, vtsim

    async def retrieval(self, question: str, embd_mdl, page: int = 1, page_size: int = 10,
                 similarity_threshold: float = 0.1, vector_similarity_weight: float = 0.95,
                 top: int = 1024, doc_ids: List[str] = None, rerank_mdl=None, highlight: bool = True):
        """
        召回方法

        Args:
            question: 查询问题
            embd_mdl: 向量化模型
            page: 页码
            page_size: 每页大小
            similarity_threshold: 相似度阈值
            vector_similarity_weight: 向量相似度权重
            top: 向量召回top-k
            doc_ids: 指定文档ID列表
            rerank_mdl: 重排序模型
            highlight: 是否高亮

        Returns:
            召回结果字典
        """
        import time
        retrieval_start = time.time()
        
        # 确保ES连接已建立
        await self.ensure_connected()
        
        if not question:
            return {"total": 0, "chunks": [], "doc_aggs": {}}

        logging.info(f"开始DeepRag召回，问题: {question}")

        try:
            # 更新配置
            t1 = time.time()
            self.config.page = page
            self.config.page_size = page_size
            self.config.similarity_threshold = similarity_threshold
            self.config.vector_similarity_weight = vector_similarity_weight
            self.config.top_k = top
            self.config.highlight = highlight
            if doc_ids:
                self.config.doc_ids = doc_ids
            logging.info(f"⏱️ [retrieval] 配置更新耗时: {(time.time()-t1)*1000:.0f}ms")

            # 构建搜索请求
            t2 = time.time()
            req = {
                "question": question,
                "page": page,
                "size": max(page_size * self.config.rerank_page_limit, 128) if page <= self.config.rerank_page_limit else page_size,
                "topk": top,
                "similarity": similarity_threshold,
                "vector": True,
                "available_int": 1,
                "fields": [
                    "docnm_kwd", "content_ltks", "img_id", "title_tks",
                    "important_kwd", "position_int", "doc_id", "page_num_int",
                    "top_int", "create_timestamp_flt", "knowledge_graph_kwd",
                    "question_kwd", "question_tks", "available_int", "content_with_weight"
                ]
            }

            if doc_ids:
                req["doc_ids"] = doc_ids
            logging.info(f"⏱️ [retrieval] 构建搜索请求耗时: {(time.time()-t2)*1000:.0f}ms")

            # 执行异步搜索
            t3 = time.time()
            sres = await self.search(req, embd_mdl, highlight)
            logging.info(f"⏱️ [retrieval] search操作耗时: {(time.time()-t3)*1000:.0f}ms, 返回结果数: {sres.total}")
            
            # 从search结果中获取查询向量（复用第1次embedding调用的结果）
            query_vector = getattr(sres, 'query_vector', [])

            if sres.total == 0:
                return {"total": 0, "chunks": [], "doc_aggs": {}}

            # 重排序（如果在重排序页面范围内）
            t4 = time.time()
            if page <= self.config.rerank_page_limit:
                if rerank_mdl:
                    # 使用重排序模型（复用已获取的查询向量）
                    logging.info(f"⏱️ [retrieval] 复用查询向量，跳过重复embedding调用")

                    t4_2 = time.time()
                    sim, tsim, vsim = await self.rerank_by_model(
                        rerank_mdl, sres.ids, sres.field, question, query_vector,
                        1.0 - vector_similarity_weight, vector_similarity_weight
                    )
                    logging.info(f"⏱️ [retrieval] 重排序模型计算耗时: {(time.time()-t4_2)*1000:.0f}ms")
                else:
                    # 使用默认重排序（复用已获取的查询向量）
                    logging.info(f"⏱️ [retrieval] 复用查询向量，跳过重复embedding调用")

                    t4_2 = time.time()
                    sim, tsim, vsim = self.rerank(
                        sres.ids, sres.field, question, sres.keywords, query_vector,
                        1.0 - vector_similarity_weight, vector_similarity_weight
                    )
                    logging.info(f"⏱️ [retrieval] 默认重排序计算耗时: {(time.time()-t4_2)*1000:.0f}ms")

                # 按相似度排序
                idx = np.argsort(sim * -1)
            else:
                # 超出重排序页面范围，直接使用ES排序
                idx = list(range(len(sres.ids)))
                sim = np.ones(len(sres.ids))
            logging.info(f"⏱️ [retrieval] 重排序总耗时: {(time.time()-t4)*1000:.0f}ms")

            # 分页处理
            t5 = time.time()
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            if page <= self.config.rerank_page_limit:
                # 重排序页面，从排序后的结果中取
                page_idx = idx[start_idx:end_idx]
            else:
                # 非重排序页面，直接分页
                page_idx = idx[:page_size]
            logging.info(f"⏱️ [retrieval] 分页处理耗时: {(time.time()-t5)*1000:.0f}ms")

            # 构建最终结果
            t6 = time.time()
            chunks = []
            doc_aggs = {}

            for i in page_idx:
                if i >= len(sres.ids):
                    continue

                chunk_id = sres.ids[i]
                chunk_data = sres.field.get(chunk_id, {})
                
                # 计算相似度分数
                chunk_similarity = float(sim[i]) if i < len(sim) else 0.0
                
                # 应用相似度阈值过滤
                if chunk_similarity < similarity_threshold:
                    logging.debug(f"过滤低相似度块 {chunk_id}：相似度 {chunk_similarity:.4f} < 阈值 {similarity_threshold}")
                    continue

                # 基本信息
                chunk = {
                    "chunk_id": chunk_id,
                    "content_with_weight": chunk_data.get("content_with_weight", ""),
                    "doc_id": chunk_data.get("doc_id", ""),
                    "docnm_kwd": chunk_data.get("docnm_kwd", ""),
                    "page_num_int": chunk_data.get("page_num_int", []),
                    "position_int": chunk_data.get("position_int", []),
                    "available_int": chunk_data.get("available_int", 1),
                    "similarity": chunk_similarity
                }

                # 添加其他字段
                for field in ["img_id", "title_tks", "important_kwd", "top_int",
                             "create_timestamp_flt", "knowledge_graph_kwd", "question_kwd", "question_tks"]:
                    if field in chunk_data:
                        chunk[field] = chunk_data[field]

                chunks.append(chunk)

                # 文档聚合
                doc_name = chunk.get("docnm_kwd", "Unknown")
                doc_id = chunk.get("doc_id", "")
                if doc_name not in doc_aggs:
                    doc_aggs[doc_name] = {"doc_id": doc_id, "count": 0}
                doc_aggs[doc_name]["count"] += 1

            # 转换文档聚合格式
            doc_aggs_list = [
                {"doc_name": k, "doc_id": v["doc_id"], "count": v["count"]}
                for k, v in sorted(doc_aggs.items(), key=lambda x: x[1]["count"], reverse=True)
            ]
            logging.info(f"⏱️ [retrieval] 构建结果耗时: {(time.time()-t6)*1000:.0f}ms, 返回 {len(chunks)} 个chunks")

            # 复用已获取的查询向量（无需再次调用embedding）
            logging.info(f"⏱️ [retrieval] 复用查询向量（已在search阶段获取），节省embedding调用")

            result = {
                "total": sres.total,
                "chunks": chunks,
                "doc_aggs": doc_aggs_list,
                "query_vector": query_vector  # 使用search阶段获取的向量
            }

            logging.info(f"⏱️ [retrieval] ========== retrieval总耗时: {(time.time()-retrieval_start)*1000:.0f}ms ==========")
            logging.info(f"DeepRag召回完成，总数: {sres.total}, 返回: {len(chunks)} 个分块")
            return result

        except Exception as e:
            import traceback
            logging.error(f"DeepRag召回失败: {e}")
            logging.error(f"错误详情: {traceback.format_exc()}")
            return {"total": 0, "chunks": [], "doc_aggs": {}, "error": str(e)}

    async def rerank_by_model(self, rerank_mdl, chunk_ids: List[str], fields_data: Dict[str, Dict],
                       question: str, query_vector: List[float], text_weight: float, vector_weight: float):
        """
        使用重排序模型进行重排序

        Args:
            rerank_mdl: 重排序模型
            chunk_ids: 分块ID列表
            fields_data: 字段数据
            question: 查询问题
            query_vector: 查询向量
            text_weight: 文本权重
            vector_weight: 向量权重

        Returns:
            相似度分数数组
        """
        import time
        rerank_start = time.time()
        
        if not chunk_ids or not rerank_mdl:
            return np.array([]), np.array([]), np.array([])

        logging.info(f"⏱️ [rerank_by_model] 开始重排序，分块数量: {len(chunk_ids)}")

        try:
            # 1. 准备token数据（完全按照DeepRag第318-324行的逻辑）
            t1 = time.time()
            ins_tw = []
            for chunk_id in chunk_ids:
                chunk_data = fields_data.get(chunk_id, {})

                # 按照DeepRag的逻辑处理token
                content_ltks = chunk_data.get("content_ltks", "").split()
                title_tks = [t for t in chunk_data.get("title_tks", "").split() if t]
                important_kwd = chunk_data.get("important_kwd", [])

                # 确保important_kwd是列表
                if isinstance(important_kwd, str):
                    important_kwd = [important_kwd]

                # 组合token（按照DeepRag第323行）
                tks = content_ltks + title_tks + important_kwd
                ins_tw.append(tks)
            logging.info(f"⏱️ [rerank_by_model] 准备token数据耗时: {(time.time()-t1)*1000:.0f}ms")

            # 2. 计算token相似度
            t2 = time.time()
            _, keywords = self.qryr.question(question)
            tksim = self.qryr.token_similarity(keywords, ins_tw)
            logging.info(f"⏱️ [rerank_by_model] 计算token相似度耗时: {(time.time()-t2)*1000:.0f}ms")

            # 3. 使用重排序模型计算相似度（异步调用避免阻塞）
            t3 = time.time()
            from rag.utils import rmSpace
            docs_for_rerank = [rmSpace(" ".join(tks)) for tks in ins_tw]
            logging.info(f"⏱️ [rerank_by_model] 准备rerank文档耗时: {(time.time()-t3)*1000:.0f}ms")
            
            t4 = time.time()
            vtsim, _ = await asyncio.to_thread(rerank_mdl.similarity, question, docs_for_rerank)
            logging.info(f"⏱️ [rerank_by_model] 重排序模型similarity调用耗时: {(time.time()-t4)*1000:.0f}ms")

            # 4. 计算最终相似度
            t5 = time.time()
            sim = text_weight * np.array(tksim) + vector_weight * np.array(vtsim)
            logging.info(f"⏱️ [rerank_by_model] 计算最终相似度耗时: {(time.time()-t5)*1000:.0f}ms")

            logging.info(f"⏱️ [rerank_by_model] ========== rerank_by_model总耗时: {(time.time()-rerank_start)*1000:.0f}ms ==========")
            logging.debug(f"重排序模型计算完成，相似度范围: {np.min(sim):.4f} - {np.max(sim):.4f}")
            return sim, np.array(tksim), np.array(vtsim)

        except Exception as e:
            logging.error(f"重排序模型计算失败: {e}")
            # 如果重排序模型失败，降级为默认重排序
            logging.warning("重排序模型失败，降级为默认重排序算法")
            return self.rerank(chunk_ids, fields_data, question, [], query_vector, text_weight, vector_weight)

    def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        try:
            # 通过适配器获取ES健康状态
            try:
                es_health = self.es_conn.es.cluster.health()
                es_status = es_health.get("status") == "green" or es_health.get("status") == "yellow"
            except Exception as e:
                es_health = {"status": "red", "error": str(e)}
                es_status = False

            index_status = {}
            for index_name in self.config.index_names:
                try:
                    exists = self.es_conn.indexExist(index_name)
                    index_status[index_name] = exists
                except Exception as e:
                    index_status[index_name] = f"error: {e}"

            return {
                "status": "healthy" if es_status else "unhealthy",
                "components": {
                    "elasticsearch": es_status,
                    "query_processor": self.qryr is not None,
                    "es_adapter": self.es_conn is not None
                },
                "elasticsearch": es_health,
                "indices": index_status,
                "config": {
                    "index_names": self.config.index_names,
                    "page_size": self.config.page_size,
                    "similarity_threshold": self.config.similarity_threshold,
                    "vector_similarity_weight": self.config.vector_similarity_weight,
                    "rerank_page_limit": self.config.rerank_page_limit
                }
            }

        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e)
            }
    
    async def close(self):
        """
        关闭召回器，清理ES连接资源
        """
        if self.es_conn and hasattr(self.es_conn, 'es_conn'):
            try:
                await self.es_conn.es_conn.close()
                logging.info("DeepRagPureRetriever ES连接已关闭")
            except Exception as e:
                logging.error(f"关闭DeepRagPureRetriever ES连接失败: {e}")
