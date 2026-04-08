import json

from firecrawl import FirecrawlApp
from langchain.tools import tool

from src.config import get_app_config


def _get_firecrawl_client() -> FirecrawlApp:
    config = get_app_config().get_tool_config("web_search")
    api_key = None
    if config is not None:
        api_key = config.model_extra.get("api_key")
    return FirecrawlApp(api_key=api_key)  # type: ignore[arg-type]


@tool("web_search", parse_docstring=False)
def web_search_tool(query: str) -> str:
    """执行网页搜索。

    参数：
        query: 搜索查询词。
    """
    try:
        config = get_app_config().get_tool_config("web_search")
        max_results = 5
        if config is not None:
            max_results = config.model_extra.get("max_results", max_results)

        client = _get_firecrawl_client()
        result = client.search(query, limit=max_results)

        # `result.web` 包含 `SearchResultWeb` 对象列表
        web_results = result.web or []
        normalized_results = [
            {
                "title": getattr(item, "title", "") or "",
                "url": getattr(item, "url", "") or "",
                "snippet": getattr(item, "description", "") or "",
            }
            for item in web_results
        ]
        json_results = json.dumps(normalized_results, indent=2, ensure_ascii=False)
        return json_results
    except Exception as e:
        return f"Error: {str(e)}"


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
    try:
        client = _get_firecrawl_client()
        result = client.scrape(url, formats=["markdown"])

        markdown_content = result.markdown or ""
        metadata = result.metadata
        title = metadata.title if metadata and metadata.title else "Untitled"

        if not markdown_content:
            return "Error: No content found"
    except Exception as e:
        return f"Error: {str(e)}"

    return f"# {title}\n\n{markdown_content[:4096]}"
