"""
压缩Prompt模板

用于生成对话历史的XML格式摘要
"""

from typing import List
from context.models import Message


COMPRESSION_SYSTEM_PROMPT = """你是一个专业的对话历史摘要助手。你的任务是将用户与AI助手之间的对话历史总结为结构化的XML格式。

要求：
1. **保留关键信息**：包括讨论的主要话题、重要结论、用户的决策和偏好
2. **保持时间顺序**：按照对话发生的先后顺序组织内容
3. **简洁但完整**：删除重复和冗余，但不能丢失关键信息
4. **结构化输出**：使用提供的XML模板组织内容

输出格式（必须严格遵守）：
```xml
<conversation_summary>
  <topic>主要讨论话题的简要描述</topic>
  <key_points>
    <point>关键点1：具体内容</point>
    <point>关键点2：具体内容</point>
    <point>关键点3：具体内容</point>
  </key_points>
  <decisions>
    用户做出的决策、表达的偏好或达成的结论
  </decisions>
  <context>
    其他需要保留的上下文信息，如：
    - 用户的背景信息
    - 特殊要求或约束
    - 待解决的问题
  </context>
</conversation_summary>
```

注意：
- 如果某个部分没有内容，保留标签但内容为"无"
- 所有内容用中文表达
- 不要添加额外的解释或评论
"""


def build_compression_prompt(messages: List[Message]) -> str:
    """
    构建压缩Prompt
    
    Args:
        messages: 需要压缩的消息列表
        
    Returns:
        完整的压缩Prompt
    """
    # 构建对话历史部分
    conversation_text = "## 需要总结的对话历史\n\n"
    
    turn_number = 1
    for i in range(0, len(messages), 2):
        # 用户消息
        if i < len(messages):
            user_msg = messages[i]
            conversation_text += f"### 第{turn_number}轮对话\n\n"
            conversation_text += f"**用户**: {user_msg.content}\n\n"
        
        # 助手消息
        if i + 1 < len(messages):
            assistant_msg = messages[i + 1]
            conversation_text += f"**助手**: {assistant_msg.content}\n\n"
            turn_number += 1
    
    # 构建完整Prompt
    prompt = f"""{COMPRESSION_SYSTEM_PROMPT}

{conversation_text}

请根据以上对话历史，生成XML格式的摘要。"""
    
    return prompt


def validate_compression_output(output: str) -> bool:
    """
    验证压缩输出是否符合格式要求
    
    Args:
        output: LLM生成的输出
        
    Returns:
        是否有效
    """
    required_tags = [
        "<conversation_summary>",
        "</conversation_summary>",
        "<topic>",
        "</topic>",
        "<key_points>",
        "</key_points>",
        "<decisions>",
        "</decisions>",
        "<context>",
        "</context>"
    ]
    
    for tag in required_tags:
        if tag not in output:
            return False
    
    return True


def extract_summary_content(output: str) -> str:
    """
    从LLM输出中提取XML摘要内容
    
    处理LLM可能在XML前后添加的额外文本
    
    Args:
        output: LLM的原始输出
        
    Returns:
        提取的XML摘要
    """
    # 查找XML起始和结束位置
    start_tag = "<conversation_summary>"
    end_tag = "</conversation_summary>"
    
    start_idx = output.find(start_tag)
    end_idx = output.find(end_tag)
    
    if start_idx != -1 and end_idx != -1:
        # 提取XML部分
        return output[start_idx:end_idx + len(end_tag)]
    
    # 如果没有找到完整的XML标签，返回原始输出
    return output

