"""文档召回工具 - 直接 import recall_lib 执行检索"""
import sys
import asyncio
from typing import Dict, Any, List, Optional

# Docker 容器中的路径配置，确保能 import recall_lib 和 rag 依赖
sys.path.insert(0, "/workspace")        # for recall_lib
sys.path.insert(0, "/workspace/rag")    # for rag.nlp, rag.llm etc.

from langchain.tools import BaseTool
from langchain_core.callbacks import CallbackManagerForToolRun

from recall_lib import (
    DeepRagPureRetriever,
    DeepRagRetrievalConfig,
    RecallConfig,
    create_embedding_model,
    create_rerank_model,
)

from ..utils.logger import get_logger

logger = get_logger(__name__)


class RecallTool(BaseTool):
    """文档召回工具 - 直接使用 recall_lib 检索文档知识库"""

    name: str = "recall"
    description: str = """从文档知识库中检索相关信息，当且仅当用户提供的具体的文档时可用。

使用场景：
- 查找内部文档
- 检索历史记录
- 获取规范、标准文档
- 查询产品信息、技术文档等

输入：检索查询文本（query）
输出：相关文档片段
"""

    # 通过 RecallConfig 接收全部配置
    recall_config: RecallConfig

    # 私有实例（懒初始化，首次调用时创建，后续复用）
    _retriever: Optional[DeepRagPureRetriever] = None
    _embedding_model: Any = None
    _rerank_model: Any = None

    class Config:
        arbitrary_types_allowed = True
        underscore_attrs_are_private = True

    def _create_retriever(self) -> DeepRagPureRetriever:
        """创建 DeepRagPureRetriever 实例"""
        cfg = self.recall_config
        retrieval_config = DeepRagRetrievalConfig(
            index_names=cfg.index_names,
            page_size=cfg.top_n,
            similarity_threshold=cfg.similarity_threshold,
            vector_similarity_weight=cfg.vector_similarity_weight,
            top_k=cfg.top_k,
            es_config={"hosts": cfg.es_host, "timeout": 600},
        )
        return DeepRagPureRetriever(retrieval_config)

    def _ensure_initialized(self) -> None:
        """确保 retriever 和 embedding_model 已初始化（懒加载，首次调用创建，后续复用）"""
        if self._retriever is None:
            logger.info("首次调用，初始化 retriever 和 embedding_model...")
            cfg = self.recall_config
            self._retriever = self._create_retriever()
            self._embedding_model = create_embedding_model(
                model_factory=cfg.embedding_model_factory,
                model_name=cfg.embedding_model_name,
                model_base_url=cfg.embedding_base_url,
                api_key=cfg.embedding_api_key,
            )
            if cfg.use_rerank and cfg.rerank_factory and cfg.rerank_model_name:
                self._rerank_model = create_rerank_model(
                    rerank_factory=cfg.rerank_factory,
                    rerank_model_name=cfg.rerank_model_name,
                    rerank_base_url=cfg.rerank_base_url,
                    rerank_api_key=cfg.rerank_api_key,
                )
            logger.info("retriever 和 embedding_model 初始化完成")

    def _format_response(self, result: Dict[str, Any]) -> str:
        """格式化检索结果"""
        # 检查是否有错误
        if result.get("error"):
            error_msg = result["error"]
            logger.error(f"检索返回错误: {error_msg}")
            return f"检索失败: {error_msg}"

        chunks = result.get("chunks", [])
        total = result.get("total", 0)

        logger.info(f"检索响应 - total: {total}, chunks: {len(chunks)}")

        if not chunks:
            logger.warning(f"未返回结果，可能被相似度阈值过滤: {self.recall_config.similarity_threshold}")
            return "未找到相关信息。"

        formatted_results = []
        for i, chunk in enumerate(chunks, 1):
            doc_name = chunk.get("docnm_kwd", "Unknown")
            content = chunk.get("content_with_weight", "")
            page_nums = chunk.get("page_num_int", [])

            result_str = f"【文档 {i}】\n来源：{doc_name}"
            if page_nums:
                result_str += f" (第{page_nums[0]}页)"
            result_str += f"\n内容：{content}\n"
            formatted_results.append(result_str)

        logger.info(f"召回完成，返回 {len(formatted_results)} 个结果")
        return "\n".join(formatted_results)

    def _run(
        self,
        query: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        """同步执行文档召回（LangChain BaseTool 要求）"""
        try:
            return asyncio.get_event_loop().run_until_complete(self._arun(query, run_manager))
        except RuntimeError:
            # 如果没有事件循环，创建一个新的
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._arun(query, run_manager))
            finally:
                loop.close()

    async def _arun(
        self,
        query: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> str:
        """异步执行文档召回 - 直接使用 recall_lib"""
        try:
            logger.info(f"异步召回: {query[:100]}...")
            self._ensure_initialized()

            cfg = self.recall_config
            result = await self._retriever.retrieval(
                question=query,
                embd_mdl=self._embedding_model,
                page=1,
                page_size=cfg.top_n,
                similarity_threshold=cfg.similarity_threshold,
                vector_similarity_weight=cfg.vector_similarity_weight,
                top=cfg.top_k,
                doc_ids=cfg.doc_ids,
                rerank_mdl=self._rerank_model,
            )

            return self._format_response(result)

        except Exception as e:
            logger.error(f"异步召回出错: {str(e)}")
            raise RuntimeError(f"召回出错: {str(e)}")

    async def get_document_name_async(self, doc_id: str) -> Optional[str]:
        """异步获取文档名称 - 通过 retriever 直接检索"""
        try:
            self._ensure_initialized()

            result = await self._retriever.retrieval(
                question="获取文档信息",
                embd_mdl=self._embedding_model,
                page=1,
                page_size=1,
                similarity_threshold=0.0,
                vector_similarity_weight=self.recall_config.vector_similarity_weight,
                top=10,
                doc_ids=[doc_id],
            )

            chunks = result.get("chunks", [])
            if chunks:
                return chunks[0].get("docnm_kwd")

            return None

        except Exception as e:
            logger.error(f"获取文档名称异常: {doc_id}, error: {str(e)}")
            return None

    async def get_document_names_batch_async(self, doc_ids: List[str]) -> Dict[str, str]:
        """异步批量获取文档名称"""
        async def fetch_name(doc_id: str) -> tuple:
            name = await self.get_document_name_async(doc_id)
            return doc_id, name

        tasks = [fetch_name(doc_id) for doc_id in doc_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        document_names = {}
        for result in results:
            if isinstance(result, tuple):
                doc_id, name = result
                if name:
                    document_names[doc_id] = name

        logger.info(f"批量获取文档名称完成: {len(document_names)}/{len(doc_ids)}")
        return document_names


def create_recall_tool(
    index_names: List[str],
    es_host: str,
    model_base_url: str,
    api_key: str,
    doc_ids: Optional[List[str]] = None,
    top_n: int = 10,
    similarity_threshold: float = 0.2,
    vector_similarity_weight: float = 0.3,
    model_factory: str = "VLLM",
    model_name: str = "bge-m3",
    use_rerank: bool = False,
    rerank_factory: Optional[str] = None,
    rerank_model_name: Optional[str] = None,
    rerank_base_url: Optional[str] = None,
    rerank_api_key: Optional[str] = None,
    # 保留 api_url 参数以兼容旧调用方，但不再使用
    api_url: Optional[str] = None,
) -> RecallTool:
    """创建配置好的 RecallTool 实例

    构建 RecallConfig 并传递给 RecallTool，不再需要 HTTP API URL。
    api_url 参数保留以兼容旧调用方签名，但不再使用。
    """
    recall_config = RecallConfig(
        es_host=es_host,
        index_names=index_names,
        doc_ids=doc_ids,
        top_n=top_n,
        similarity_threshold=similarity_threshold,
        vector_similarity_weight=vector_similarity_weight,
        embedding_model_factory=model_factory,
        embedding_model_name=model_name,
        embedding_base_url=model_base_url,
        embedding_api_key=api_key,
        use_rerank=use_rerank,
        rerank_factory=rerank_factory,
        rerank_model_name=rerank_model_name,
        rerank_base_url=rerank_base_url,
        rerank_api_key=rerank_api_key,
    )
    return RecallTool(recall_config=recall_config)
