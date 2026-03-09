"""完成条件检测器 - 智能判断 ReAct 循环是否应该结束"""
from dataclasses import dataclass
from typing import Tuple, List, Set
from enum import Enum

from .scratchpad import Scratchpad
from .config import ReActConfig


class CompletionReason(Enum):
    """完成原因"""
    NOT_COMPLETE = "not_complete"
    SUFFICIENT_INFO = "sufficient_info"
    STUCK_IN_LOOP = "stuck_in_loop"
    TOKEN_LIMIT = "token_limit"
    NO_PROGRESS = "no_progress"
    MAX_ERRORS = "max_errors"


@dataclass
class CompletionResult:
    """完成检测结果"""
    should_finish: bool
    reason: CompletionReason
    message: str
    confidence: float  # 0.0 - 1.0


class CompletionDetector:
    """完成条件检测器"""
    
    def __init__(self, config: ReActConfig):
        self.config = config
        
        # 检测参数 — 从 config 读取，config 从 settings.py / .env 加载
        self.min_successful_calls = config.min_successful_calls
        self.max_consecutive_errors = config.max_consecutive_errors
        self.loop_detection_window = 4  # 循环检测窗口大小
    
    def check(self, scratchpad: Scratchpad, user_query: str) -> CompletionResult:
        """
        检测是否应该结束循环
        
        Args:
            scratchpad: 当前 scratchpad
            user_query: 用户原始问题
            
        Returns:
            CompletionResult 包含是否应该结束及原因
        """
        # 1. 检查 token 预算
        token_result = self._check_token_limit(scratchpad)
        if token_result.should_finish:
            return token_result
        
        # 2. 检查是否陷入循环
        loop_result = self._check_stuck_in_loop(scratchpad)
        if loop_result.should_finish:
            return loop_result
        
        # 3. 检查连续错误
        error_result = self._check_consecutive_errors(scratchpad)
        if error_result.should_finish:
            return error_result
        
        # 4. 检查是否已收集足够信息
        info_result = self._check_sufficient_info(scratchpad, user_query)
        if info_result.should_finish:
            return info_result
        
        # 5. 检查是否无进展
        progress_result = self._check_no_progress(scratchpad)
        if progress_result.should_finish:
            return progress_result
        
        return CompletionResult(
            should_finish=False,
            reason=CompletionReason.NOT_COMPLETE,
            message="",
            confidence=0.0
        )
    
    def _check_token_limit(self, scratchpad: Scratchpad) -> CompletionResult:
        """检查 token 使用率"""
        stats = scratchpad.get_statistics()
        usage_ratio = stats["token_usage_ratio"]
        
        if usage_ratio >= self.config.token_warning_threshold:
            return CompletionResult(
                should_finish=True,
                reason=CompletionReason.TOKEN_LIMIT,
                message=f"Token 使用率已达 {usage_ratio:.1%}，建议尽快完成",
                confidence=0.9
            )
        
        return CompletionResult(
            should_finish=False,
            reason=CompletionReason.NOT_COMPLETE,
            message="",
            confidence=0.0
        )
    
    def _check_stuck_in_loop(self, scratchpad: Scratchpad) -> CompletionResult:
        """检查是否陷入重复循环"""
        if len(scratchpad.entries) < self.loop_detection_window:
            return CompletionResult(
                should_finish=False,
                reason=CompletionReason.NOT_COMPLETE,
                message="",
                confidence=0.0
            )
        
        # 获取最近的 action 序列
        recent_actions: List[Tuple[str, str]] = []
        for entry in scratchpad.entries[-self.loop_detection_window:]:
            if not entry.is_summary:
                recent_actions.append((entry.action, entry.action_input[:50]))
        
        # 检查是否有重复模式
        if len(recent_actions) >= 2:
            unique_actions = set(recent_actions)
            if len(unique_actions) == 1:
                return CompletionResult(
                    should_finish=True,
                    reason=CompletionReason.STUCK_IN_LOOP,
                    message="检测到重复的工具调用模式，建议结束并生成答案",
                    confidence=0.85
                )
        
        return CompletionResult(
            should_finish=False,
            reason=CompletionReason.NOT_COMPLETE,
            message="",
            confidence=0.0
        )
    
    def _check_consecutive_errors(self, scratchpad: Scratchpad) -> CompletionResult:
        """检查连续错误"""
        if len(scratchpad.entries) < self.max_consecutive_errors:
            return CompletionResult(
                should_finish=False,
                reason=CompletionReason.NOT_COMPLETE,
                message="",
                confidence=0.0
            )
        
        # 检查最近的条目是否都是错误
        recent_entries = scratchpad.entries[-self.max_consecutive_errors:]
        error_count = sum(
            1 for e in recent_entries 
            if e.observation and (
                e.observation.startswith("[ERROR]") or 
                e.observation.startswith("[LOW_QUALITY]")
            )
        )
        
        if error_count >= self.max_consecutive_errors:
            return CompletionResult(
                should_finish=True,
                reason=CompletionReason.MAX_ERRORS,
                message=f"连续 {error_count} 次工具调用失败，建议结束",
                confidence=0.8
            )
        
        return CompletionResult(
            should_finish=False,
            reason=CompletionReason.NOT_COMPLETE,
            message="",
            confidence=0.0
        )
    
    def _check_sufficient_info(self, scratchpad: Scratchpad, user_query: str) -> CompletionResult:
        """检查是否已收集足够信息"""
        # 统计成功的工具调用
        successful_calls = 0
        total_info_length = 0
        
        for entry in scratchpad.entries:
            if entry.is_summary:
                continue
            if entry.observation and not entry.observation.startswith("[ERROR]"):
                successful_calls += 1
                total_info_length += len(entry.observation)
        
        # 如果有足够的成功调用和信息量
        if successful_calls >= self.min_successful_calls and total_info_length > 500:
            # 简单的关键词匹配检查
            query_keywords = set(user_query.lower().split())
            info_text = " ".join(
                e.observation.lower() 
                for e in scratchpad.entries 
                if e.observation and not e.observation.startswith("[ERROR]")
            )
            
            # 计算关键词覆盖率
            covered = sum(1 for kw in query_keywords if kw in info_text)
            coverage = covered / len(query_keywords) if query_keywords else 0
            
            if coverage > 0.5:
                return CompletionResult(
                    should_finish=False,  # 只是建议，不强制结束
                    reason=CompletionReason.SUFFICIENT_INFO,
                    message=f"已收集 {successful_calls} 条有效信息，关键词覆盖率 {coverage:.0%}",
                    confidence=coverage
                )
        
        return CompletionResult(
            should_finish=False,
            reason=CompletionReason.NOT_COMPLETE,
            message="",
            confidence=0.0
        )
    
    def _check_no_progress(self, scratchpad: Scratchpad) -> CompletionResult:
        """检查是否无进展"""
        if len(scratchpad.entries) < 3:
            return CompletionResult(
                should_finish=False,
                reason=CompletionReason.NOT_COMPLETE,
                message="",
                confidence=0.0
            )
        
        # 检查最近 3 个条目是否都没有有效信息
        # 注意：成功的工具调用（如 write_file 返回 "已写入 X 字符到 Y"）不应被视为无进展，
        # 因此排除以常见成功前缀开头的短消息
        _SUCCESS_PREFIXES = ("已写入", "已追加", "已上传", "文件已", "已将")
        recent_entries = scratchpad.entries[-3:]
        no_info_count = sum(
            1 for e in recent_entries
            if not e.observation or 
               e.observation.startswith("[ERROR]") or
               "未找到" in e.observation or
               (len(e.observation) < 50 and not any(e.observation.startswith(p) for p in _SUCCESS_PREFIXES))
        )
        
        if no_info_count >= 3:
            return CompletionResult(
                should_finish=True,
                reason=CompletionReason.NO_PROGRESS,
                message="最近的工具调用未获取有效信息，建议结束",
                confidence=0.7
            )
        
        return CompletionResult(
            should_finish=False,
            reason=CompletionReason.NOT_COMPLETE,
            message="",
            confidence=0.0
        )
