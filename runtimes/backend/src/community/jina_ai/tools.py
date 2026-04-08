from langchain.tools import tool

from src.community.jina_ai.jina_client import JinaClient
from src.config import get_app_config
from src.utils.readability import ReadabilityExtractor

readability_extractor = ReadabilityExtractor()


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
    jina_client = JinaClient()
    timeout = 10
    config = get_app_config().get_tool_config("web_fetch")
    if config is not None and "timeout" in config.model_extra:
        timeout = config.model_extra.get("timeout")
    html_content = jina_client.crawl(url, return_format="html", timeout=timeout)
    article = readability_extractor.extract_article(html_content)
    return article.to_markdown()[:4096]
