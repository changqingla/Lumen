import logging
import os

from src.utils.provider_http import provider_post

logger = logging.getLogger(__name__)

_MAX_REQUEST_TIMEOUT_SECONDS = 120


def _request_timeout(value: int) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        return 10
    return max(1, min(timeout, _MAX_REQUEST_TIMEOUT_SECONDS))


class JinaClient:
    def crawl(self, url: str, return_format: str = "html", timeout: int = 10) -> str:
        request_timeout = _request_timeout(timeout)
        headers = {
            "Content-Type": "application/json",
            "X-Return-Format": return_format,
            "X-Timeout": str(request_timeout),
        }
        if os.getenv("JINA_API_KEY"):
            headers["Authorization"] = f"Bearer {os.getenv('JINA_API_KEY')}"
        else:
            logger.warning("未设置 Jina API Key。若要获得更高限流额度，请配置你自己的 Key。详情见 https://jina.ai/reader 。")
        data = {"url": url}
        try:
            response = provider_post(
                "https://r.jina.ai/",
                headers=headers,
                json=data,
                timeout=request_timeout,
            )

            if response.status_code != 200:
                logger.error(
                    "Jina API returned non-success status=%s response_bytes=%s",
                    response.status_code,
                    len(response.content),
                )
                return f"Error: Jina API returned status {response.status_code}"

            if not response.text or not response.text.strip():
                error_message = "Jina API 返回了空响应"
                logger.error(error_message)
                return f"Error: {error_message}"

            return response.text
        except Exception as exc:
            logger.error("Jina API request failed (%s)", type(exc).__name__)
            return "Error: Jina API request failed"
