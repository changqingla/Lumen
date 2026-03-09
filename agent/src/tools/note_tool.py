"""笔记工具 — Agent 的工作记忆，用于在多步推理中保存和读取中间结果"""
import json
from typing import Optional, Dict

from langchain.tools import BaseTool
from langchain_core.callbacks import CallbackManagerForToolRun

from ..utils.logger import get_logger

logger = get_logger(__name__)


class WriteNoteTool(BaseTool):
    """写入笔记 — 保存中间分析结果供后续步骤使用"""

    name: str = "write_note"
    description: str = """将中间分析结果保存为笔记，供后续步骤使用。
适用于多文档任务中，逐篇阅读后记录要点，最后综合所有笔记生成答案。

输入格式（JSON）:
  {"title": "笔记标题", "content": "笔记内容"}

输出: 确认信息"""

    notes: Dict[str, str] = {}

    class Config:
        arbitrary_types_allowed = True

    def _run(self, query: str, run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        try:
            params = json.loads(query) if query.strip().startswith("{") else None
        except json.JSONDecodeError:
            params = None

        if not params or "title" not in params or "content" not in params:
            return '[ERROR] 输入格式错误。请使用 JSON: {"title": "标题", "content": "内容"}'

        title = params["title"].strip()
        content = params["content"].strip()

        if not title or not content:
            return "[ERROR] 标题和内容不能为空。"

        self.notes[title] = content
        logger.info(f"📝 笔记已保存: {title} ({len(content)} 字符)")
        return f"笔记 '{title}' 已保存 ({len(content)} 字符)。当前共 {len(self.notes)} 条笔记。"

    async def _arun(self, query: str, run_manager=None) -> str:
        return self._run(query, run_manager)


class ReadNoteTool(BaseTool):
    """读取笔记 — 查看之前保存的中间分析结果"""

    name: str = "read_note"
    description: str = """读取之前保存的笔记。

输入:
  - 笔记标题（精确匹配）→ 返回该笔记内容
  - "all" → 返回所有笔记的标题和内容
  - "list" → 返回所有笔记的标题列表

输出: 笔记内容"""

    notes: Dict[str, str] = {}

    class Config:
        arbitrary_types_allowed = True

    def _run(self, query: str, run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        title = query.strip().strip('"').strip("'")

        if not self.notes:
            return "当前没有保存的笔记。"

        if title.lower() == "list":
            titles = list(self.notes.keys())
            return f"已保存的笔记 ({len(titles)} 条):\n" + "\n".join(f"- {t}" for t in titles)

        if title.lower() == "all":
            parts = []
            for t, c in self.notes.items():
                parts.append(f"## {t}\n{c}")
            return "\n\n---\n\n".join(parts)

        if title in self.notes:
            return f"## {title}\n{self.notes[title]}"

        # 模糊匹配
        for t, c in self.notes.items():
            if title.lower() in t.lower():
                return f"## {t}\n{c}"

        available = list(self.notes.keys())
        return f"未找到笔记 '{title}'。已保存的笔记: {available}"

    async def _arun(self, query: str, run_manager=None) -> str:
        return self._run(query, run_manager)
