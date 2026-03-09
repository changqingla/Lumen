"""Scratchpad 管理器 - 管理 ReAct 循环中的历史记录"""
from dataclasses import dataclass, field
from typing import List, Optional, Set


def _estimate_tokens(text: str) -> int:
    """简单估算 token 数 (len/4)"""
    if not text:
        return 0
    return len(text) // 4


@dataclass
class ScratchpadEntry:
    """单个 Scratchpad 条目"""
    thought: str
    action: str
    action_input: str
    observation: Optional[str] = None
    is_summary: bool = False  # 标记是否为摘要条目
    
    def to_string(self) -> str:
        """转换为字符串格式"""
        lines = [
            f"Thought: {self.thought}",
            f"Action: {self.action}",
            f"Action Input: {self.action_input}",
        ]
        if self.observation is not None:
            lines.append(f"Observation: {self.observation}")
        return "\n".join(lines)
    
    def get_token_count(self, model: str = "gpt-4") -> int:
        """计算此条目的 token 数（使用简单估算）"""
        return _estimate_tokens(self.to_string())


@dataclass
class Scratchpad:
    """Scratchpad 管理器 - 支持智能摘要"""
    entries: List[ScratchpadEntry] = field(default_factory=list)
    max_tokens: int = 4000
    model: str = "gpt-4"
    
    def add_entry(self, entry: ScratchpadEntry) -> None:
        """添加新条目"""
        self.entries.append(entry)
        self._truncate_if_needed()
    
    def update_last_observation(self, observation: str) -> None:
        """更新最后一个条目的 observation"""
        if self.entries:
            self.entries[-1].observation = observation
            self._truncate_if_needed()
    
    def get_total_tokens(self) -> int:
        """计算总 token 数（使用简单估算）"""
        return _estimate_tokens(self.to_string())
    
    def to_string(self) -> str:
        """转换为完整的 scratchpad 字符串"""
        if not self.entries:
            return ""
        return "\n\n".join(entry.to_string() for entry in self.entries)
    
    def _truncate_if_needed(self) -> None:
        """如果超过 token 限制，使用智能摘要截断早期条目"""
        if len(self.entries) <= 2:
            return
        
        total_tokens = self.get_total_tokens()
        if total_tokens <= self.max_tokens:
            return
        
        # 保留第一个和最后两个条目
        first_entry = self.entries[0]
        last_entries = self.entries[-2:]
        middle_entries = self.entries[1:-2]
        
        if not middle_entries:
            return
        
        # 生成智能摘要
        summary = self._generate_smart_summary(middle_entries)
        self.entries = [first_entry, summary] + last_entries
    
    def _generate_smart_summary(self, entries: List[ScratchpadEntry]) -> ScratchpadEntry:
        """
        生成智能摘要，提取关键信息
        
        Args:
            entries: 需要摘要的条目列表
            
        Returns:
            摘要条目
        """
        # 收集工具调用统计
        tool_calls: dict = {}
        key_findings: List[str] = []
        queries_used: Set[str] = set()
        
        for entry in entries:
            # 统计工具调用
            action = entry.action
            if action not in tool_calls:
                tool_calls[action] = 0
            tool_calls[action] += 1
            
            # 收集查询词（去重）
            if entry.action_input and len(entry.action_input) < 100:
                queries_used.add(entry.action_input[:50])
            
            # 提取关键发现（从 observation 中提取）
            if entry.observation and not entry.observation.startswith("[ERROR]"):
                # 提取前 100 字符作为关键发现
                finding = entry.observation[:100].strip()
                if finding and len(finding) > 20:
                    key_findings.append(finding)
        
        # 构建摘要
        tool_summary = ", ".join(f"{k}×{v}" for k, v in tool_calls.items())
        
        # 限制关键发现数量
        key_findings = key_findings[:3]
        findings_text = ""
        if key_findings:
            findings_text = "\n关键发现: " + "; ".join(f[:50] + "..." for f in key_findings)
        
        queries_text = ""
        if queries_used:
            queries_text = f"\n查询词: {', '.join(list(queries_used)[:5])}"
        
        summary_thought = (
            f"[Earlier {len(entries)} steps summarized]\n"
            f"工具调用: {tool_summary}"
            f"{queries_text}"
            f"{findings_text}"
        )
        
        return ScratchpadEntry(
            thought=summary_thought,
            action="[summarized]",
            action_input="[summarized]",
            observation="[详细信息已压缩，关键发现保留在上方摘要中]",
            is_summary=True
        )
    
    def get_statistics(self) -> dict:
        """获取 scratchpad 统计信息"""
        total_entries = len(self.entries)
        summary_entries = sum(1 for e in self.entries if e.is_summary)
        total_tokens = self.get_total_tokens()
        
        return {
            "total_entries": total_entries,
            "summary_entries": summary_entries,
            "total_tokens": total_tokens,
            "token_usage_ratio": total_tokens / self.max_tokens if self.max_tokens > 0 else 0
        }
    
    def clear(self) -> None:
        """清空 scratchpad"""
        self.entries.clear()
    
    def __len__(self) -> int:
        return len(self.entries)
