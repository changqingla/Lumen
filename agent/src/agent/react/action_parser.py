"""Action 解析器 - 从 LLM 输出解析 Thought, Action, Action Input"""
import re
from dataclasses import dataclass
from typing import Optional, Tuple

from .config import ReActConfig, DEFAULT_REACT_CONFIG


@dataclass
class ParsedAction:
    """解析后的 Action"""
    thought: str
    action: str
    action_input: str
    is_valid: bool = True
    error_message: Optional[str] = None


class ActionParser:
    """Action 解析器"""
    
    # 正则表达式模式
    THOUGHT_PATTERN = re.compile(r"Thought:\s*(.+?)(?=Action:|$)", re.DOTALL | re.IGNORECASE)
    ACTION_PATTERN = re.compile(r"Action:\s*(\w+)", re.IGNORECASE)
    ACTION_INPUT_PATTERN = re.compile(r"Action Input:\s*(.+?)(?=Thought:|Observation:|$)", re.DOTALL | re.IGNORECASE)
    
    def __init__(self, config: ReActConfig = None):
        self.config = config or DEFAULT_REACT_CONFIG
    
    def parse(self, llm_output: str) -> ParsedAction:
        """
        解析 LLM 输出
        
        Args:
            llm_output: LLM 的原始输出
            
        Returns:
            ParsedAction 对象
        """
        llm_output = llm_output.strip()
        
        # 提取 Thought
        thought_match = self.THOUGHT_PATTERN.search(llm_output)
        thought = thought_match.group(1).strip() if thought_match else ""
        
        # 提取 Action
        action_match = self.ACTION_PATTERN.search(llm_output)
        if not action_match:
            return ParsedAction(
                thought=thought,
                action="",
                action_input="",
                is_valid=False,
                error_message="Could not parse Action from output. Please use format: Action: <tool_name>"
            )
        action = action_match.group(1).strip().lower()
        
        # 提取 Action Input
        action_input_match = self.ACTION_INPUT_PATTERN.search(llm_output)
        action_input = action_input_match.group(1).strip() if action_input_match else ""
        
        # 验证 Action 是否有效
        if action not in self.config.available_tools:
            return ParsedAction(
                thought=thought,
                action=action,
                action_input=action_input,
                is_valid=False,
                error_message=f"Invalid action '{action}'. Available actions: {', '.join(self.config.available_tools)}"
            )
        
        # 验证 Action Input 非空
        if not action_input:
            return ParsedAction(
                thought=thought,
                action=action,
                action_input="",
                is_valid=False,
                error_message=f"Action Input is required for action '{action}'"
            )
        
        return ParsedAction(
            thought=thought,
            action=action,
            action_input=action_input,
            is_valid=True
        )
    
    def is_finish_action(self, parsed: ParsedAction) -> bool:
        """检查是否是 finish action"""
        return parsed.is_valid and parsed.action == "finish"
    
    def extract_final_answer(self, parsed: ParsedAction) -> str:
        """从 finish action 中提取最终答案"""
        if self.is_finish_action(parsed):
            return parsed.action_input
        return ""
