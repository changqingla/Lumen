import json

from langchain.tools import tool
from tavily import TavilyClient

from src.config import get_app_config


def _get_tavily_client() -> TavilyClient:
    config = get_app_config().get_tool_config("web_search")
    api_key = None
    if config is not None and "api_key" in config.model_extra:
        api_key = config.model_extra.get("api_key")
    return TavilyClient(api_key=api_key)


@tool("web_search", parse_docstring=False)
def web_search_tool(query: str) -> str:
    """执行网页搜索。

    参数：
        query: 搜索查询词。
    """
    config = get_app_config().get_tool_config("web_search")
    max_results = 5
    if config is not None and "max_results" in config.model_extra:
        max_results = config.model_extra.get("max_results")

    client = _get_tavily_client()
    res = client.search(query, max_results=max_results)
    normalized_results = [
        {
            "title": result["title"],
            "url": result["url"],
            "snippet": result["content"],
        }
        for result in res["results"]
    ]
    json_results = json.dumps(normalized_results, indent=2, ensure_ascii=False)
    return json_results


@tool("web_fetch", parse_docstring=False)
def web_fetch_tool(url: str) -> str:
    """抓取给定 URL 的网页内容。
    只能抓取以下来源的精确 URL：用户直接提供，或来自 web_search / web_fetch 工具结果。
    本工具无法访问需要认证的内容（例如私有 Google Docs、登录墙后页面）。
    不要给原本不带 `www.` 的 URL 人工添加 `www.`。
    URL 必须包含协议头：`https://example.com` 是合法 URL，`example.com` 非法。

    参数：
        url: 要抓取内容的 URL。
    """
    client = _get_tavily_client()
    res = client.extract([url])
    if "failed_results" in res and len(res["failed_results"]) > 0:
        return f"Error: {res['failed_results'][0]['error']}"
    elif "results" in res and len(res["results"]) > 0:
        result = res["results"][0]
        return f"# {result['title']}\n\n{result['raw_content'][:4096]}"
    else:
        return "Error: No results found"
