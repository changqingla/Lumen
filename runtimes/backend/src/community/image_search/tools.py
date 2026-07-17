"""
图片搜索工具 - 使用 DuckDuckGo 搜索图片，为图像生成提供参考。
"""

import json
import logging

from langchain.tools import tool

from src.config import get_app_config

logger = logging.getLogger(__name__)


def _search_images(
    query: str,
    max_results: int = 5,
    region: str = "wt-wt",
    safesearch: str = "moderate",
    size: str | None = None,
    color: str | None = None,
    type_image: str | None = None,
    layout: str | None = None,
    license_image: str | None = None,
) -> list[dict]:
    """
    使用 DuckDuckGo 执行图片搜索。

    参数：
        query: 搜索关键词
        max_results: 最大返回数量
        region: 搜索区域
        safesearch: 安全搜索等级
        size: 图片尺寸（Small/Medium/Large/Wallpaper）
        color: 颜色过滤
        type_image: 图片类型（photo/clipart/gif/transparent/line）
        layout: 布局（Square/Tall/Wide）
        license_image: 许可证过滤

    返回：
        搜索结果列表
    """
    try:
        from ddgs import DDGS
    except ImportError:
        logger.error("ddgs library not installed. Run: pip install ddgs")
        return []

    ddgs = DDGS(timeout=30)

    try:
        kwargs = {
            "region": region,
            "safesearch": safesearch,
            "max_results": max_results,
        }

        if size:
            kwargs["size"] = size
        if color:
            kwargs["color"] = color
        if type_image:
            kwargs["type_image"] = type_image
        if layout:
            kwargs["layout"] = layout
        if license_image:
            kwargs["license_image"] = license_image

        results = ddgs.images(query, **kwargs)
        return list(results) if results else []

    except Exception as exc:
        logger.error("Failed to search images (%s)", type(exc).__name__)
        return []


@tool("image_search", parse_docstring=False)
def image_search_tool(
    query: str,
    max_results: int = 5,
    size: str | None = None,
    type_image: str | None = None,
    layout: str | None = None,
) -> str:
    """
    **适用场景：**
    - 生成人物/肖像前：先搜相似姿态、表情、风格
    - 生成特定物体/产品前：先搜准确视觉参考
    - 生成场景/地点前：先搜建筑或环境参考
    - 生成时尚/服饰前：先搜款式与细节参考

    返回的图片 URL 可作为图像生成的参考图，显著提升生成质量。

    参数：
        query: 描述目标图片的搜索词。越具体结果越好（例如 “Japanese woman street photography 1990s”，而不是仅写 “woman”）。
        max_results: 返回图片最大数量。默认 5。
        size: 图片尺寸过滤。可选："Small"、"Medium"、"Large"、"Wallpaper"。参考图建议用 "Large"。
        type_image: 图片类型过滤。可选："photo"、"clipart"、"gif"、"transparent"、"line"。真实参考建议用 "photo"。
        layout: 布局过滤。可选："Square"、"Tall"、"Wide"。按生成需求选择。
    """
    config = get_app_config().get_tool_config("image_search")

    # 若配置中设置了 max_results，则覆盖参数值
    if config is not None and "max_results" in config.model_extra:
        max_results = config.model_extra.get("max_results", max_results)

    results = _search_images(
        query=query,
        max_results=max_results,
        size=size,
        type_image=type_image,
        layout=layout,
    )

    if not results:
        return json.dumps({"error": "No images found", "query": query}, ensure_ascii=False)

    normalized_results = [
        {
            "title": r.get("title", ""),
            "image_url": r.get("thumbnail", ""),
            "thumbnail_url": r.get("thumbnail", ""),
        }
        for r in results
    ]

    output = {
        "query": query,
        "total_results": len(normalized_results),
        "results": normalized_results,
        "usage_hint": "Use the 'image_url' values as reference images in image generation. Download them first if needed.",
    }

    return json.dumps(output, indent=2, ensure_ascii=False)
