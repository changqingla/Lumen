"""网页搜索工具 - 支持多种搜索引擎"""
from typing import Optional, Literal
import httpx
import json

from langchain.tools import BaseTool
from langchain_core.callbacks import CallbackManagerForToolRun

from ..utils.logger import get_logger

logger = get_logger(__name__)

SearchEngine = Literal["tavily", "bocha"]

# HTTP 超时配置
HTTP_TIMEOUT = 30.0


class WebSearchTool(BaseTool):
    """
    网页搜索工具
    
    支持搜索引擎: Tavily, Bocha
    """
    
    name: str = "web_search"
    description: str = """从互联网搜索最新信息。

使用场景：
- 获取最新资讯
- 查找公开数据
- 了解行业动态
- 获取实时信息

输入：搜索查询文本（query）
输出：搜索结果摘要
"""
    
    api_key: str
    search_engine: SearchEngine = "tavily"
    max_results: int = 5
    
    class Config:
        arbitrary_types_allowed = True
    
    def _format_tavily_results(self, response: dict) -> str:
        """格式化 Tavily 搜索结果"""
        results = response.get("results", [])
        
        if not results:
            return "未找到相关信息。"
        
        formatted_results = []
        for i, result in enumerate(results, 1):
            title = result.get("title", "N/A")
            url = result.get("url", "N/A")
            content = result.get("content", "")
            
            result_str = f"【结果 {i}】\n标题：{title}\n来源：{url}\n内容：{content}\n"
            formatted_results.append(result_str)
        
        answer = response.get("answer", "")
        result_text = ""
        
        if answer:
            result_text += f"\n概要回答：\n{answer}\n\n"
        
        result_text += f"搜索结果 (共 {len(results)} 条)\n" + "=" * 50 + "\n"
        result_text += "\n".join(formatted_results)
        
        logger.info(f"Tavily 搜索完成，返回 {len(results)} 条结果")
        return result_text
    
    def _format_bocha_results(self, response_data: dict) -> str:
        """格式化 Bocha 搜索结果"""
        if response_data.get("code") != 200:
            error_msg = response_data.get("msg", "Unknown error")
            return f"搜索失败：{error_msg}"
        
        data = response_data.get("data", {})
        web_pages = data.get("webPages", {})
        results = web_pages.get("value", [])
        
        if not results:
            return "未找到相关信息。"
        
        formatted_results = []
        for i, result in enumerate(results, 1):
            name = result.get("name", "N/A")
            url = result.get("url", "N/A")
            summary = result.get("summary", "")
            snippet = result.get("snippet", "")
            content = summary if summary else snippet
            site_name = result.get("siteName", "")
            
            result_str = f"【结果 {i}】\n标题：{name}\n来源：{url}"
            if site_name:
                result_str += f"\n网站：{site_name}"
            result_str += f"\n内容：{content}\n"
            formatted_results.append(result_str)
        
        result_text = f"搜索结果 (共 {len(results)} 条)\n" + "=" * 50 + "\n"
        result_text += "\n".join(formatted_results)
        
        logger.info(f"Bocha 搜索完成，返回 {len(results)} 条结果")
        return result_text
    
    def _run(
        self,
        query: str,
        run_manager: Optional[CallbackManagerForToolRun] = None
    ) -> str:
        """
        同步执行网页搜索
        
        Args:
            query: 搜索查询
            run_manager: 回调管理器
            
        Returns:
            格式化的搜索结果
        """
        if self.search_engine == "tavily":
            return self._search_tavily_sync(query)
        elif self.search_engine == "bocha":
            return self._search_bocha_sync(query)
        else:
            raise ValueError(f"不支持的搜索引擎: {self.search_engine}")
    
    def _search_tavily_sync(self, query: str) -> str:
        """同步 Tavily 搜索"""
        try:
            from tavily import TavilyClient
            
            logger.info(f"执行 Tavily 搜索: {query[:100]}...")
            client = TavilyClient(api_key=self.api_key)
            response = client.search(query=query, max_results=self.max_results)
            
            if not response:
                return "未找到相关信息。"
            
            return self._format_tavily_results(response)
            
        except Exception as e:
            logger.error(f"Tavily 搜索失败: {str(e)}", exc_info=True)
            raise RuntimeError(f"Tavily 搜索失败: {str(e)}")
    
    def _search_bocha_sync(self, query: str) -> str:
        """同步 Bocha 搜索"""
        try:
            logger.info(f"执行 Bocha 搜索: {query[:100]}...")
            
            url = "https://api.bochaai.com/v1/web-search"
            payload = {"query": query, "summary": True, "count": self.max_results}
            headers = {"Authorization": self.api_key, "Content-Type": "application/json"}
            
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                response_data = response.json()
            
            return self._format_bocha_results(response_data)
            
        except httpx.HTTPError as e:
            logger.error(f"Bocha 搜索网络错误: {str(e)}", exc_info=True)
            raise RuntimeError(f"Bocha 搜索网络错误: {str(e)}")
        except Exception as e:
            logger.error(f"Bocha 搜索失败: {str(e)}", exc_info=True)
            raise RuntimeError(f"Bocha 搜索失败: {str(e)}")
    
    async def _arun(
        self,
        query: str,
        run_manager: Optional[CallbackManagerForToolRun] = None
    ) -> str:
        """
        异步执行网页搜索
        
        Args:
            query: 搜索查询
            run_manager: 回调管理器
            
        Returns:
            格式化的搜索结果
        """
        if self.search_engine == "tavily":
            return await self._search_tavily_async(query)
        elif self.search_engine == "bocha":
            return await self._search_bocha_async(query)
        else:
            raise ValueError(f"不支持的搜索引擎: {self.search_engine}")
    
    async def _search_tavily_async(self, query: str) -> str:
        """异步 Tavily 搜索"""
        try:
            from tavily import AsyncTavilyClient
            
            logger.info(f"异步执行 Tavily 搜索: {query[:100]}...")
            client = AsyncTavilyClient(api_key=self.api_key)
            response = await client.search(query=query, max_results=self.max_results)
            
            if not response:
                return "未找到相关信息。"
            
            return self._format_tavily_results(response)
            
        except ImportError:
            # 如果没有异步客户端，回退到同步
            logger.warning("AsyncTavilyClient 不可用，使用同步客户端")
            return self._search_tavily_sync(query)
        except Exception as e:
            logger.error(f"Tavily 异步搜索失败: {str(e)}", exc_info=True)
            raise RuntimeError(f"Tavily 异步搜索失败: {str(e)}")
    
    async def _search_bocha_async(self, query: str) -> str:
        """异步 Bocha 搜索"""
        try:
            logger.info(f"异步执行 Bocha 搜索: {query[:100]}...")
            
            url = "https://api.bochaai.com/v1/web-search"
            payload = {"query": query, "summary": True, "count": self.max_results}
            headers = {"Authorization": self.api_key, "Content-Type": "application/json"}
            
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                response_data = response.json()
            
            return self._format_bocha_results(response_data)
            
        except httpx.HTTPError as e:
            logger.error(f"Bocha 异步搜索网络错误: {str(e)}", exc_info=True)
            raise RuntimeError(f"Bocha 异步搜索网络错误: {str(e)}")
        except Exception as e:
            logger.error(f"Bocha 异步搜索失败: {str(e)}", exc_info=True)
            raise RuntimeError(f"Bocha 异步搜索失败: {str(e)}")


def create_web_search_tool(
    api_key: str,
    search_engine: SearchEngine = "tavily",
    max_results: int = 5
) -> WebSearchTool:
    """
    创建网页搜索工具
    
    Args:
        api_key: 搜索引擎 API 密钥
        search_engine: 搜索引擎类型 ("tavily" 或 "bocha")
        max_results: 最大返回结果数
        
    Returns:
        配置好的 WebSearchTool 实例
    """
    if not api_key:
        raise ValueError("搜索引擎 API 密钥不能为空")
    
    if search_engine not in ["tavily", "bocha"]:
        raise ValueError(f"不支持的搜索引擎: {search_engine}，必须是 'tavily' 或 'bocha'")
    
    return WebSearchTool(
        api_key=api_key,
        search_engine=search_engine,
        max_results=max_results
    )
