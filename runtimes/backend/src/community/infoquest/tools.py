from langchain.tools import tool

from src.config import get_app_config
from src.utils.readability import ReadabilityExtractor

from .infoquest_client import InfoQuestClient

readability_extractor = ReadabilityExtractor()


def _get_infoquest_client() -> InfoQuestClient:
    search_config = get_app_config().get_tool_config("web_search")
    search_time_range = -1
    if search_config is not None and "search_time_range" in search_config.model_extra:
        search_time_range = search_config.model_extra.get("search_time_range")
    fetch_config = get_app_config().get_tool_config("web_fetch")
    fetch_time = -1
    if fetch_config is not None and "fetch_time" in fetch_config.model_extra:
        fetch_time = fetch_config.model_extra.get("fetch_time")
    fetch_timeout = -1
    if fetch_config is not None and "timeout" in fetch_config.model_extra:
        fetch_timeout = fetch_config.model_extra.get("timeout")
    navigation_timeout = -1
    if fetch_config is not None and "navigation_timeout" in fetch_config.model_extra:
        navigation_timeout = fetch_config.model_extra.get("navigation_timeout")

    return InfoQuestClient(
        search_time_range=search_time_range,
        fetch_timeout=fetch_timeout,
        fetch_navigation_timeout=navigation_timeout,
        fetch_time=fetch_time,
    )


@tool("web_search", parse_docstring=False)
def web_search_tool(query: str) -> str:
    """
    参数：
        query: 搜索查询词。
    """

    client = _get_infoquest_client()
    return client.web_search(query)


@tool("web_fetch", parse_docstring=False)
def web_fetch_tool(url: str) -> str:
    """
    只能抓取以下来源的精确 URL：用户直接提供，或来自 web_search / web_fetch 工具结果。
    本工具无法访问需要认证的内容（例如私有 Google Docs、登录墙后页面）。
    不要给原本不带 `www.` 的 URL 人工添加 `www.`。
    URL 必须包含协议头：`https://example.com` 是合法 URL，`example.com` 非法。

    参数：
        url: 要抓取内容的 URL。
    """
    client = _get_infoquest_client()
    result = client.fetch(url)
    if result.startswith("Error: "):
        return result
    article = readability_extractor.extract_article(result)
    return article.to_markdown()[:4096]
