import logging
import os

import requests

logger = logging.getLogger(__name__)


class JinaClient:
    def crawl(self, url: str, return_format: str = "html", timeout: int = 10) -> str:
        headers = {
            "Content-Type": "application/json",
            "X-Return-Format": return_format,
            "X-Timeout": str(timeout),
        }
        if os.getenv("JINA_API_KEY"):
            headers["Authorization"] = f"Bearer {os.getenv('JINA_API_KEY')}"
        else:
            logger.warning("未设置 Jina API Key。若要获得更高限流额度，请配置你自己的 Key。详情见 https://jina.ai/reader 。")
        data = {"url": url}
        try:
            response = requests.post("https://r.jina.ai/", headers=headers, json=data)

            if response.status_code != 200:
                error_message = f"Jina API 返回状态码 {response.status_code}: {response.text}"
                logger.error(error_message)
                return f"Error: {error_message}"

            if not response.text or not response.text.strip():
                error_message = "Jina API 返回了空响应"
                logger.error(error_message)
                return f"Error: {error_message}"

            return response.text
        except Exception as e:
            error_message = f"请求 Jina API 失败：{str(e)}"
            logger.error(error_message)
            return f"Error: {error_message}"
