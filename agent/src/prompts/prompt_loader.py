"""提示词加载工具"""
from pathlib import Path
from typing import Dict, Optional
from functools import lru_cache

# 提示词目录
PROMPTS_DIR = Path(__file__).parent / "templates"


@lru_cache(maxsize=50)
def load_prompt(name: str) -> str:
    """
    从 Markdown 文件加载提示词
    
    Args:
        name: 提示词名称（不含 .md 后缀）
        
    Returns:
        提示词内容
        
    Raises:
        FileNotFoundError: 提示词文件不存在
    """
    prompt_file = PROMPTS_DIR / f"{name}.md"
    
    if not prompt_file.exists():
        raise FileNotFoundError(f"提示词文件不存在: {prompt_file}")
    
    return prompt_file.read_text(encoding="utf-8")


def format_prompt(name: str, **kwargs) -> str:
    """
    加载并格式化提示词
    
    Args:
        name: 提示词名称
        **kwargs: 占位符替换参数
        
    Returns:
        格式化后的提示词
    """
    template = load_prompt(name)
    return template.format(**kwargs)


def clear_cache() -> None:
    """清除提示词缓存"""
    load_prompt.cache_clear()
