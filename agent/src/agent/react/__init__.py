"""ReAct Agent 模块"""
from .config import ReActConfig, DEFAULT_REACT_CONFIG
from .scratchpad import Scratchpad, ScratchpadEntry
from .action_parser import ActionParser, ParsedAction
from .hooks import (
    ToolHook,
    HookManager,
    HookAction,
    HookResult,
    QuerySanitizationHook,
    ResultValidationHook,
    LoopDetectionHook,
    create_default_hook_manager,
)
from .completion_detector import (
    CompletionDetector,
    CompletionResult,
    CompletionReason,
)

__all__ = [
    # Config
    "ReActConfig",
    "DEFAULT_REACT_CONFIG",
    # Scratchpad
    "Scratchpad",
    "ScratchpadEntry",
    # Action Parser
    "ActionParser",
    "ParsedAction",
    # Hooks
    "ToolHook",
    "HookManager",
    "HookAction",
    "HookResult",
    "QuerySanitizationHook",
    "ResultValidationHook",
    "LoopDetectionHook",
    "create_default_hook_manager",
    # Completion Detector
    "CompletionDetector",
    "CompletionResult",
    "CompletionReason",
]
