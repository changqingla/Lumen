from typing import Literal

from langchain.tools import tool


@tool("ask_clarification", parse_docstring=False, return_direct=True)
def ask_clarification_tool(
    question: str,
    clarification_type: Literal[
        "missing_info",
        "ambiguous_requirement",
        "approach_choice",
        "risk_confirmation",
        "suggestion",
    ],
    context: str | None = None,
    options: list[str] | None = None,
) -> str:
    """向用户发起澄清并暂停执行。

    仅当代理缺少关键信息、无法继续推进，或必须在高风险/破坏性操作前
    获得明确确认时，才使用此工具。

    不应在以下场景使用：
    - 简单信息问答
    - 低风险请求中的轻微歧义
    - 可以从当前会话、上传文件、选中文档、截图或最近上下文中推断出的指代
    - 当前上下文里只有一个明显候选对象时，对“这篇论文 / 这个文件 / 这张图”再追问确认

    参数：
        question: 展示给用户的澄清问题。
        clarification_type: 澄清类别，可取：
            missing_info、ambiguous_requirement、approach_choice、
            risk_confirmation、suggestion。
        context: 说明为何需要澄清的可选上下文。
        options: 供用户选择的可选候选项。

    返回：
        一个占位字符串。真正的中断与处理逻辑由
        `ClarificationMiddleware` 接管。
    """
    # 这里是占位实现
    # 实际逻辑由 ClarificationMiddleware 接管：拦截该工具调用，
    # 中断执行并向用户展示澄清问题
    return "Clarification request processed by middleware"
