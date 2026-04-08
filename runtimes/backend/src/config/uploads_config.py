from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class UploadsConfig(BaseModel):
    """文件上传相关配置。"""

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
