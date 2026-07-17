"""
配置方式请参考：
https://docs.byteplus.com/en/docs/InfoQuest/What_is_Info_Quest
"""

import json
import logging
import os
from typing import Any

from src.utils.provider_http import provider_post

logger = logging.getLogger(__name__)

_DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
_MAX_REQUEST_TIMEOUT_SECONDS = 120.0


def _request_timeout(configured: int | float) -> float:
    try:
        value = float(configured)
    except (TypeError, ValueError):
        return _DEFAULT_REQUEST_TIMEOUT_SECONDS
    if value <= 0:
        return _DEFAULT_REQUEST_TIMEOUT_SECONDS
    return min(value, _MAX_REQUEST_TIMEOUT_SECONDS)


class InfoQuestClient:
    """用于调用 InfoQuest 搜索与抓取 API 的客户端。"""

    def __init__(self, fetch_time: int = -1, fetch_timeout: int = -1, fetch_navigation_timeout: int = -1, search_time_range: int = -1):
        logger.info("\n============================================\n🚀 BytePlus InfoQuest Client Initialization 🚀\n============================================")

        self.fetch_time = fetch_time
        self.fetch_timeout = fetch_timeout
        self.fetch_navigation_timeout = fetch_navigation_timeout
        self.search_time_range = search_time_range
        self.api_key_set = bool(os.getenv("INFOQUEST_API_KEY"))
        if logger.isEnabledFor(logging.DEBUG):
            config_details = (
                f"\n📋 Configuration Details:\n"
                f"├── Fetch time: {fetch_time} {'(Default: No fetch time)' if fetch_time == -1 else '(Custom)'}\n"
                f"├── Fetch Timeout: {fetch_timeout} {'(Default: No fetch timeout)' if fetch_timeout == -1 else '(Custom)'}\n"
                f"├── Navigation Timeout: {fetch_navigation_timeout} {'(Default: No Navigation Timeout)' if fetch_navigation_timeout == -1 else '(Custom)'}\n"
                f"├── Search Time Range: {search_time_range} {'(Default: No Search Time Range)' if search_time_range == -1 else '(Custom)'}\n"
                f"└── API Key: {'✅ Configured' if self.api_key_set else '❌ Not set'}"
            )

            logger.debug(config_details)
            logger.debug("\n" + "*" * 70 + "\n")

    def fetch(self, url: str, return_format: str = "html") -> str:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"InfoQuest - Fetch API request initiated | "
                f"operation=crawl url | "
                f"url_length={len(url)} | "
                f"has_timeout_filter={self.fetch_timeout > 0} | timeout_filter={self.fetch_timeout} | "
                f"has_fetch_time_filter={self.fetch_time > 0} | fetch_time_filter={self.fetch_time} | "
                f"has_navigation_timeout_filter={self.fetch_navigation_timeout > 0} | navi_timeout_filter={self.fetch_navigation_timeout} | "
                f"request_type=sync"
            )

        # 准备请求头
        headers = self._prepare_headers()

        # 准备请求数据
        data = self._prepare_crawl_request_data(url, return_format)

        logger.debug("Sending crawl request to InfoQuest API")
        try:
            response = provider_post(
                "https://reader.infoquest.bytepluses.com",
                headers=headers,
                json=data,
                timeout=_request_timeout(self.fetch_timeout),
            )

            # 检查状态码是否为 200
            if response.status_code != 200:
                logger.debug(
                    "InfoQuest Crawler returned status %d (response_bytes=%d)",
                    response.status_code,
                    len(response.content),
                )
                return f"Error: fetch API returned status {response.status_code}"

            # 检查是否为空响应
            if not response.text or not response.text.strip():
                error_message = "no result found"
                logger.debug("InfoQuest Crawler returned an empty response")
                return f"Error: {error_message}"

            # 尝试解析 JSON 并提取 reader_result
            try:
                response_data = json.loads(response.text)
                # 若存在 reader_result 字段则优先返回
                if "reader_result" in response_data:
                    logger.debug("Successfully extracted reader_result from JSON response")
                    return response_data["reader_result"]
                elif "content" in response_data:
                    # 若 `reader_result` 不存在，则回退到 `content` 字段
                    logger.debug(
                        "reader_result missing in JSON response; using content field"
                    )
                    return response_data["content"]
                else:
                    # 两个字段都不存在时，返回原始响应
                    logger.warning("Neither reader_result nor content field found in JSON response")
            except json.JSONDecodeError:
                # 非 JSON 响应时，直接返回原始文本
                logger.debug("Response is not in JSON format, returning as-is")
                return response.text

            logger.debug(
                "Successfully received InfoQuest response (content_chars=%d)",
                len(response.text),
            )
            return response.text
        except Exception as exc:
            logger.error("InfoQuest fetch API failed (%s)", type(exc).__name__)
            return "Error: fetch API failed"

    @staticmethod
    def _prepare_headers() -> dict[str, str]:
        """准备请求头。"""
        headers = {
            "Content-Type": "application/json",
        }

        # 若存在 API Key，则加入请求头
        if os.getenv("INFOQUEST_API_KEY"):
            headers["Authorization"] = f"Bearer {os.getenv('INFOQUEST_API_KEY')}"
            logger.debug("API key added to request headers")
        else:
            logger.warning("InfoQuest API key is not set. Provide your own key for authentication.")

        return headers

    def _prepare_crawl_request_data(self, url: str, return_format: str) -> dict[str, Any]:
        """准备并格式化抓取请求参数。"""
        # 规范化 return_format
        if return_format and return_format.lower() == "html":
            normalized_format = "HTML"
        else:
            normalized_format = return_format

        data = {"url": url, "format": normalized_format}

        # 仅在超时参数为正值时加入请求
        timeout_params = {}
        if self.fetch_time > 0:
            timeout_params["fetch_time"] = self.fetch_time
        if self.fetch_timeout > 0:
            timeout_params["timeout"] = self.fetch_timeout
        if self.fetch_navigation_timeout > 0:
            timeout_params["navi_timeout"] = self.fetch_navigation_timeout

        # 记录生效的超时参数
        if timeout_params:
            logger.debug("Applying timeout parameters: %s", timeout_params)
            data.update(timeout_params)

        return data

    def web_search_raw_results(
        self,
        query: str,
        site: str,
        output_format: str = "JSON",
    ) -> dict:
        """同步调用 InfoQuest Web-Search API 获取结果。"""
        headers = self._prepare_headers()

        params = {"format": output_format, "query": query}
        if self.search_time_range > 0:
            params["time_range"] = self.search_time_range

        if site != "":
            params["site"] = site

        response = provider_post(
            "https://search.infoquest.bytepluses.com",
            headers=headers,
            json=params,
            timeout=_DEFAULT_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        response_json = response.json()
        logger.debug(
            "Search API request completed successfully | service=InfoQuest | "
            "status=success | top_level_fields=%d",
            len(response_json) if isinstance(response_json, dict) else 0,
        )

        return response_json

    @staticmethod
    def clean_results(raw_results: list[dict[str, dict[str, dict[str, Any]]]]) -> list[dict]:
        """清洗 InfoQuest Web-Search API 返回结果。"""
        logger.debug("Processing web-search results")

        seen_urls = set()
        clean_results = []
        counts = {"pages": 0, "news": 0}

        for content_list in raw_results:
            content = content_list["content"]
            results = content["results"]

            if results.get("organic"):
                organic_results = results["organic"]
                for result in organic_results:
                    clean_result = {
                        "type": "page",
                    }
                    if "title" in result:
                        clean_result["title"] = result["title"]
                    if "desc" in result:
                        clean_result["desc"] = result["desc"]
                        clean_result["snippet"] = result["desc"]
                    if "url" in result:
                        clean_result["url"] = result["url"]
                        url = clean_result["url"]
                        if isinstance(url, str) and url and url not in seen_urls:
                            seen_urls.add(url)
                            clean_results.append(clean_result)
                            counts["pages"] += 1

            if results.get("top_stories"):
                news = results["top_stories"]
                for obj in news["items"]:
                    clean_result = {
                        "type": "news",
                    }
                    if "time_frame" in obj:
                        clean_result["time_frame"] = obj["time_frame"]
                    if "source" in obj:
                        clean_result["source"] = obj["source"]
                    title = obj.get("title")
                    url = obj.get("url")
                    if title:
                        clean_result["title"] = title
                    if url:
                        clean_result["url"] = url
                    if title and isinstance(url, str) and url and url not in seen_urls:
                        seen_urls.add(url)
                        clean_results.append(clean_result)
                        counts["news"] += 1
        logger.debug(f"Results processing completed | total_results={len(clean_results)} | pages={counts['pages']} | news_items={counts['news']} | unique_urls={len(seen_urls)}")

        return clean_results

    def web_search(
        self,
        query: str,
        site: str = "",
        output_format: str = "JSON",
    ) -> str:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"InfoQuest - Search API request initiated | "
                f"operation=search webs | "
                f"query_length={len(query)} | "
                f"has_time_filter={self.search_time_range > 0} | time_filter={self.search_time_range} | "
                f"has_site_filter={bool(site)} | site_length={len(site)} | "
                f"request_type=sync"
            )

        try:
            logger.debug("InfoQuest Web-Search - Executing search with parameters")
            raw_results = self.web_search_raw_results(
                query,
                site,
                output_format,
            )
            if "search_result" in raw_results:
                logger.debug("InfoQuest Web-Search - Successfully extracted search_result from JSON response")
                results = raw_results["search_result"]

                logger.debug("InfoQuest Web-Search - Processing raw search results")
                cleaned_results = self.clean_results(results["results"])

                result_json = json.dumps(cleaned_results, indent=2, ensure_ascii=False)

                logger.debug(f"InfoQuest Web-Search - Search tool execution completed | mode=synchronous | results_count={len(cleaned_results)}")
                return result_json

            elif "content" in raw_results:
                # 若 `search_result` 不存在，则回退到 `content` 字段
                error_message = "web search API return wrong format"
                logger.error(
                    "Web search API returned an unexpected content-only response"
                )
                return f"Error: {error_message}"
            else:
                # 两个字段都不存在时，返回原始响应
                logger.warning("InfoQuest Web-Search - Neither search_result nor content field found in JSON response")
                return json.dumps(raw_results, indent=2, ensure_ascii=False)

        except Exception as exc:
            logger.error(
                "InfoQuest Web-Search failed (%s)",
                type(exc).__name__,
            )
            return "Error: InfoQuest Web-Search failed"

    @staticmethod
    def clean_results_with_image_search(raw_results: list[dict[str, dict[str, dict[str, Any]]]]) -> list[dict]:
        """清洗 InfoQuest Web-Search API 图片检索结果。"""
        logger.debug("Processing web-search results")

        seen_urls = set()
        clean_results = []
        counts = {"images": 0}

        for content_list in raw_results:
            content = content_list["content"]
            results = content["results"]

            if results.get("images_results"):
                images_results = results["images_results"]
                for result in images_results:
                    clean_result = {}
                    if "image_url" in result:
                        clean_result["image_url"] = result["image_url"]
                        url = clean_result["image_url"]
                        if isinstance(url, str) and url and url not in seen_urls:
                            seen_urls.add(url)
                            clean_results.append(clean_result)
                            counts["images"] += 1
                    if "thumbnail_url" in result:
                        clean_result["thumbnail_url"] = result["thumbnail_url"]
                    if "url" in result:
                        clean_result["url"] = result["url"]
        logger.debug(f"Results processing completed | total_results={len(clean_results)} | images={counts['images']} | unique_urls={len(seen_urls)}")

        return clean_results
