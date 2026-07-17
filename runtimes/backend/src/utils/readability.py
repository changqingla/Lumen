import logging
import subprocess

from markdownify import markdownify as md
from readabilipy import simple_json_from_html_string

logger = logging.getLogger(__name__)


class Article:
    def __init__(self, title: str, html_content: str):
        self.title = title
        self.html_content = html_content

    def to_markdown(self, including_title: bool = True) -> str:
        markdown = ""
        if including_title:
            markdown += f"# {self.title}\n\n"

        if self.html_content is None or not str(self.html_content).strip():
            markdown += "*无可用内容*\n"
        else:
            markdown += md(self.html_content)

        return markdown

class ReadabilityExtractor:
    def extract_article(self, html: str) -> Article:
        try:
            article = simple_json_from_html_string(html, use_readability=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            logger.warning(
                "Readability.js 提取失败（%s）；将回退到纯 Python 提取",
                type(exc).__name__,
            )
            article = simple_json_from_html_string(html, use_readability=False)

        html_content = article.get("content")
        if not html_content or not str(html_content).strip():
            html_content = "无法从该页面提取有效内容"

        title = article.get("title")
        if not title or not str(title).strip():
            title = "未命名"

        return Article(title=title, html_content=html_content)
