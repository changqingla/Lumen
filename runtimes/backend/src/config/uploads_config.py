from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class UploadsConfig(BaseModel):
    """文件上传相关配置。"""

    max_file_size_bytes: int = Field(
        default=100 * 1024 * 1024,
        gt=0,
        description="单个上传文件允许的最大字节数。",
    )
    max_request_size_bytes: int = Field(
        default=200 * 1024 * 1024,
        gt=0,
        description="一次上传请求中所有文件允许的最大累计字节数。",
    )
    stream_chunk_size_bytes: int = Field(
        default=1024 * 1024,
        gt=0,
        le=8 * 1024 * 1024,
        description="从 multipart 临时文件流式写盘时使用的块大小。",
    )

    markdown_extensions: set[str] = Field(
        default_factory=lambda: {
            ".pdf",
            ".ppt",
            ".pptx",
            ".xls",
            ".xlsx",
            ".doc",
            ".docx",
        },
        description="上传后会额外尝试转换为 Markdown 的文件扩展名集合。",
    )

    @field_validator("markdown_extensions", mode="before")
    @classmethod
    def normalize_markdown_extensions(cls, value: object) -> object:
        if value is None:
            return set()
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, set, tuple)):
            raise TypeError("markdown_extensions must be a list, tuple, or set of strings")

        normalized: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise TypeError("markdown_extensions items must be strings")

            ext = item.strip().lower()
            if not ext:
                continue
            if not ext.startswith("."):
                ext = f".{ext}"
            normalized.add(ext)

        return normalized
