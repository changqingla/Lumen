"""Thinking stream generators for transparent AI reasoning.

This module provides base classes and implementations for generating
human-readable reasoning processes from each node in the agent workflow.

Note: 当前所有节点的思考输出已简化，不再输出硬编码的状态描述。
ThoughtGeneratorManager 保留用于未来扩展，但目前所有 generate_thought 方法都不输出内容。
"""

from typing import Dict


class ThoughtGeneratorManager:
    """Manager for thought generators.
    
    This class provides a centralized interface for thought generation.
    Currently all thought generators are no-ops as we've simplified the output
    to only show meaningful LLM reasoning results.
    
    The manager is kept for future extensibility and to maintain the existing
    interface used by nodes.
    """
    
    # 支持的节点名称列表
    SUPPORTED_NODES = {"react_agent"}
    
    def __init__(self):
        """Initialize the thought generator manager."""
        pass
    
    def reset_all_counters(self):
        """Reset step counters (no-op, kept for interface compatibility)."""
        pass
    
    def has_generator(self, node_name: str) -> bool:
        """Check if a thought generator exists for a node.
        
        Args:
            node_name: Name of the node
            
        Returns:
            True if node is supported, False otherwise
        """
        return node_name in self.SUPPORTED_NODES
