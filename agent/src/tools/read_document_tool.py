"""文档阅读工具 — 通过后端 API 读取文档内容（支持分段）"""
import re
from typing import Optional, List

from langchain.tools import BaseTool
from langchain_core.callbacks import CallbackManagerForToolRun

from ..utils.logger import get_logger

logger = get_logger(__name__)


class ReadDocumentTool(BaseTool):
    """读取文档内容 — 从已传入的 document_contents 中按段读取"""

    name: str = "read_document"
    description: str = """读取指定文档的内容。可以读取全文或指定段落范围。

输入格式（JSON）:
  {"doc_id": "文档ID"}                          — 读取全文（自动截断过长内容）
  {"doc_id": "文档ID", "section": "方法"}        — 读取包含关键词的章节
  
输出: 文档的 markdown 内容。

注意: 如果文档很长，建议先用 read_document_outline 查看结构，再有针对性地读取特定章节。"""

    # 运行时注入
    document_contents: dict = {}
    document_names: dict = {}
    max_chars: int = 12000  # 单次返回最大字符数

    class Config:
        arbitrary_types_allowed = True

    def _run(self, query: str, run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        import json
        try:
            params = json.loads(query) if query.strip().startswith("{") else {"doc_id": query.strip()}
        except json.JSONDecodeError:
            params = {"doc_id": query.strip()}

        doc_id = params.get("doc_id", "").strip()
        section_keyword = params.get("section", "").strip()

        if not doc_id:
            # 如果没指定 doc_id，返回可用文档列表
            if self.document_contents:
                doc_list = []
                for did in self.document_contents:
                    name = self.document_names.get(did, did)
                    length = len(self.document_contents[did])
                    doc_list.append(f"- {name} (doc_id: {did}, {length} 字符)")
                return f"可用文档:\n" + "\n".join(doc_list)
            return "当前没有可用文档。"

        content = self.document_contents.get(doc_id)
        if not content:
            # 尝试模糊匹配
            for did, c in self.document_contents.items():
                if doc_id in did or did in doc_id:
                    content = c
                    doc_id = did
                    break
            if not content:
                available = list(self.document_contents.keys())
                return f"未找到文档 '{doc_id}'。可用文档ID: {available}"

        doc_name = self.document_names.get(doc_id, doc_id)

        if section_keyword:
            # 按章节关键词提取
            sections = self._extract_sections(content)
            matched = []
            for title, body in sections:
                if section_keyword.lower() in title.lower() or section_keyword.lower() in body[:200].lower():
                    matched.append(f"## {title}\n{body}")
            if matched:
                result = f"【{doc_name}】匹配章节:\n\n" + "\n\n---\n\n".join(matched)
                if len(result) > self.max_chars:
                    result = result[:self.max_chars] + f"\n\n[内容已截断，共 {len(result)} 字符]"
                return result
            return f"在文档 '{doc_name}' 中未找到包含 '{section_keyword}' 的章节。"

        # 返回全文（可能截断）
        if len(content) <= self.max_chars:
            return f"【{doc_name}】全文 ({len(content)} 字符):\n\n{content}"
        else:
            return (
                f"【{doc_name}】前 {self.max_chars} 字符 (全文共 {len(content)} 字符):\n\n"
                f"{content[:self.max_chars]}\n\n"
                f"[内容已截断。请使用 read_document_outline 查看文档结构，"
                f"然后用 section 参数读取特定章节]"
            )

    async def _arun(self, query: str, run_manager=None) -> str:
        return self._run(query, run_manager)

    @staticmethod
    def _extract_sections(content: str) -> List[tuple]:
        """从 markdown 中提取章节 (title, body)"""
        lines = content.split("\n")
        sections = []
        current_title = ""
        current_body = []

        for line in lines:
            if re.match(r"^#{1,4}\s+", line):
                if current_title:
                    sections.append((current_title, "\n".join(current_body).strip()))
                current_title = re.sub(r"^#{1,4}\s+", "", line).strip()
                current_body = []
            else:
                current_body.append(line)

        if current_title:
            sections.append((current_title, "\n".join(current_body).strip()))

        return sections


class ReadDocumentOutlineTool(BaseTool):
    """读取文档大纲 — 提取 markdown 标题结构"""

    name: str = "read_document_outline"
    description: str = """获取文档的标题结构和各章节的大致长度。
用于了解文档整体结构，决定需要重点阅读哪些部分。

输入: 文档ID（字符串）
输出: 文档的标题大纲和各章节字符数"""

    document_contents: dict = {}
    document_names: dict = {}

    class Config:
        arbitrary_types_allowed = True

    def _run(self, query: str, run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        doc_id = query.strip().strip('"').strip("'")

        content = self.document_contents.get(doc_id)
        if not content:
            for did, c in self.document_contents.items():
                if doc_id in did or did in doc_id:
                    content = c
                    doc_id = did
                    break
            if not content:
                available = list(self.document_contents.keys())
                return f"未找到文档 '{doc_id}'。可用文档ID: {available}"

        doc_name = self.document_names.get(doc_id, doc_id)
        lines = content.split("\n")

        outline = []
        current_section_start = 0

        for i, line in enumerate(lines):
            match = re.match(r"^(#{1,4})\s+(.+)", line)
            if match:
                level = len(match.group(1))
                title = match.group(2).strip()
                indent = "  " * (level - 1)
                # 计算上一节的长度
                section_text = "\n".join(lines[current_section_start:i])
                char_count = len(section_text.strip())
                if outline:
                    outline[-1] += f" ({char_count} 字符)"
                outline.append(f"{indent}- {title}")
                current_section_start = i

        # 最后一节
        if outline:
            section_text = "\n".join(lines[current_section_start:])
            outline[-1] += f" ({len(section_text.strip())} 字符)"

        total_chars = len(content)
        header = f"【{doc_name}】文档大纲 (总计 {total_chars} 字符):\n"

        if outline:
            return header + "\n".join(outline)
        else:
            # 没有标题结构
            return f"{header}该文档没有明确的标题结构。全文共 {total_chars} 字符。"

    async def _arun(self, query: str, run_manager=None) -> str:
        return self._run(query, run_manager)
