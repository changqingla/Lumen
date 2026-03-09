# Agent 工作流算法说明

## 概述

本系统是一个面向学术文献的智能问答助手，采用**混合架构**处理用户查询：

- **Pipeline 路线**：针对文献总结、综述生成、文献问答三种专用任务，使用优化的流水线处理
- **ReAct 路线**：针对通用任务，使用 ReAct Agent 模式进行灵活的推理和工具调用

系统根据用户问题的类型和关联文档的数量，动态选择最优的处理策略。

## 架构概览

```
用户请求
    │
    ▼
┌─────────────────────────────────────┐
│         Intent Router               │
│   (识别是否属于 3 种专用任务)         │
└─────────────────────────────────────┘
    │
    ├─── LITERATURE_SUMMARY ───→ Pipeline Mode
    │                              │
    │                              ├→ Strategy Selection
    │                              ├→ Document Summary / Chunk Recall
    │                              └→ Answer Generation (专用 Prompt)
    │
    ├─── REVIEW_GENERATION ────→ Pipeline Mode
    │                              │
    │                              └→ (同上)
    │
    ├─── LITERATURE_QA ────────→ Pipeline Mode
    │                              │
    │                              └→ (同上)
    │
    └─── GENERAL_TASK ─────────→ ReAct Agent Mode
                                   │
                                   ▼
                         ┌─────────────────────┐
                         │   ReAct Loop        │
                         │                     │
                         │  Thought → Action   │
                         │  → Observation →    │
                         │  ... → Finish       │
                         └─────────────────────┘
```

## 意图类型

系统支持 4 种意图类型：

| 意图类型 | 枚举值 | 处理路线 | 说明 |
|---------|--------|---------|------|
| 文献总结 | `LITERATURE_SUMMARY` | Pipeline | 对文献内容进行概括总结 |
| 综述生成 | `REVIEW_GENERATION` | Pipeline | 基于多篇文献生成综述报告 |
| 文献问答 | `LITERATURE_QA` | Pipeline | 针对文献内容的具体问题回答 |
| 通用任务 | `GENERAL_TASK` | ReAct | 日常对话、论文评审、通用问题等 |

## 工作流主流程

### 第一阶段：请求初始化

当用户发起查询请求时，系统首先进行初始化准备工作：

**后端预处理（`src/rag/service.py`）：**
1. 获取用户的 ES 索引名称
2. 根据 `kb_id` 和 `doc_ids` 获取文档列表
3. **预加载文档内容**：
   - 单文档场景：加载 `content`（单个文档的 markdown 内容）
   - 多文档场景：批量加载 `document_contents`（所有文档的完整 markdown 内容字典）和 `document_names`（文档名称映射）
4. 调用 Agent System，传递预加载的内容

**Agent 初始化（`agent_system/src/agent/agent.py`）：**
1. 加载或创建会话，获取历史对话上下文
2. 计算当前可用的上下文窗口大小
3. 根据请求参数动态创建语言模型实例和工具实例

### 第二阶段：意图识别与路由（intent_recognition_node）

分析用户问题的意图类型，并决定路由：

**意图识别流程：**
1. 如果请求中指定了 `mode_type`，直接使用指定的意图类型
2. 否则调用 LLM 进行意图识别（最多重试 3 次）
3. 如果识别失败，默认使用 `GENERAL_TASK`

**路由决策：**
```python
if detected_intent in [LITERATURE_SUMMARY, REVIEW_GENERATION, LITERATURE_QA]:
    route = "pipeline"
else:
    route = "react"
```

### 第三阶段：分支处理

#### ReAct 路线（route == "react"）

当路由到 ReAct 时，执行 `react_agent_node_stream`：

**ReAct 循环：**
1. 构建 prompt，包含用户问题、对话历史、文档信息、scratchpad
2. 调用 LLM 生成 Thought + Action + Action Input
3. 解析 Action：
   - `recall(query)`: 从文档库检索信息
   - `web_search(query)`: 从互联网搜索信息
   - `finish(answer)`: 完成任务，输出最终答案
4. 执行工具，获取 Observation
5. 将 Thought-Action-Observation 添加到 scratchpad
6. 重复直到调用 finish 或达到最大迭代次数

**循环保护：**
- 最大迭代次数：10 次（可配置）
- 超过限制时强制生成答案

**会话保存：**
- 只保存用户问题和最终答案
- 不保存 scratchpad 中间过程（避免 session 膨胀）

#### Pipeline 路线（route == "pipeline"）

继续执行原有的流水线处理：

**策略选择（strategy_selection_node）：**
```
if use_direct_content:
    strategy = "full_content"
elif doc_count > 1 and intent in [LITERATURE_SUMMARY, REVIEW_GENERATION]:
    strategy = "multi_doc_summary"
else:
    strategy = "chunk_recall"
```

**三种策略：**

| 策略 | 适用场景 | 处理方式 |
|------|---------|---------|
| `full_content` | 单文档且内容较小 | 直接使用完整文档内容 |
| `multi_doc_summary` | 多文档 + 文献总结/综述生成 | 并行为每篇文档生成压缩总结 |
| `chunk_recall` | 其他所有场景 | 分解子问题，从文档中检索相关片段 |

### 第四阶段：答案生成

**Pipeline 路线：**
- 根据意图类型选择专用 prompt 模板
- 使用收集到的信息生成答案

**ReAct 路线：**
- 答案在 ReAct 循环中通过 finish action 生成

---

## ReAct Agent 详细说明

### 可用工具

| 工具名 | 描述 | 输入 | 输出 |
|-------|------|------|------|
| `recall` | 从文档知识库检索信息 | query: str | 相关文档片段 |
| `web_search` | 从互联网搜索信息 | query: str | 搜索结果摘要 |
| `finish` | 完成任务，输出最终答案 | answer: str | (终止循环) |

### Scratchpad 管理

Scratchpad 用于记录 ReAct 循环中的历史：

```
Thought: [推理过程]
Action: [工具名]
Action Input: [工具输入]
Observation: [工具返回结果]

Thought: [下一步推理]
...
```

**Token 管理：**
- 最大 scratchpad token 数：4000（可配置）
- 超过限制时使用**智能摘要**截断早期 entries
- 智能摘要会提取：工具调用统计、查询词、关键发现

**智能摘要示例：**
```
[Earlier 5 steps summarized]
工具调用: recall×3, web_search×2
查询词: 深度学习, 神经网络, 注意力机制
关键发现: 发现了关于 Transformer 架构的详细说明...
```

### Hook 机制

ReAct Agent 支持 Hook 机制，用于拦截和处理工具调用：

| Hook 类型 | 功能 | 触发时机 |
|----------|------|---------|
| `QuerySanitizationHook` | 清理查询输入，移除无意义词 | 工具执行前 |
| `ResultValidationHook` | 验证结果质量，标记低质量结果 | 工具执行后 |
| `LoopDetectionHook` | 检测重复调用，防止死循环 | 工具执行前 |

**Hook 处理流程：**
```
用户查询 → Pre-Hooks → 工具执行 → Post-Hooks → Observation
```

### 智能完成检测

系统会智能检测是否应该结束 ReAct 循环：

| 检测条件 | 触发行为 |
|---------|---------|
| Token 使用率 > 85% | 强制结束，生成答案 |
| 检测到重复调用模式 | 强制结束，生成答案 |
| 连续 3 次工具调用失败 | 强制结束，生成答案 |
| 最近 3 次调用无有效信息 | 强制结束，生成答案 |

### 错误处理

| 错误类型 | 处理方式 |
|---------|---------|
| 无效 Action 格式 | 返回错误 Observation，允许重试 |
| 工具执行超时 | 返回超时 Observation（30 秒超时） |
| 工具执行失败 | 返回错误信息作为 Observation |
| 超过最大迭代次数 | 强制生成答案 |
| 重复工具调用 | Hook 拦截并跳过 |
| 低质量结果 | 标记 [LOW_QUALITY] 前缀 |

### 思考过程可视化

ReAct Agent 输出增强的思考过程信息：

```
**[迭代 1/10]**

**思考**: 用户询问关于深度学习的问题，我需要先从文档中检索相关信息...

**动作**: recall(深度学习基础概念)

**观察结果**: 找到了关于深度学习的定义和基本原理...

*[Scratchpad: 3 条记录, Token 使用率 25%]*
```

---

## 任务类型与处理策略矩阵

| 任务类型 | 无文档 | 单文档（小） | 单文档（大） | 多文档 |
|---------|--------|-------------|-------------|--------|
| **文献总结** | N/A | full_content | chunk_recall | multi_doc_summary |
| **综述生成** | N/A | full_content | chunk_recall | multi_doc_summary |
| **文献问答** | N/A | full_content | chunk_recall | chunk_recall |
| **通用任务** | ReAct | ReAct | ReAct | ReAct |

---

## 上下文注入策略

| 处理阶段 | 注入策略 | 说明 |
|---------|---------|------|
| 意图识别 | 最近 2 轮对话 | 足够理解上下文 |
| Pipeline 答案生成 | 最近 3 轮对话 | 需要更多上下文 |
| ReAct Agent | 最近 2-3 轮对话 | 注入到 prompt 的 conversation_history |

---

## 流程图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              请求初始化                                      │
│  • 加载/创建会话  • 计算可用 tokens  • 创建 LLM 和工具实例                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           意图识别与路由                                      │
│  识别 4 种意图类型，决定路由到 Pipeline 或 ReAct                              │
└─────────────────────────────────────────────────────────────────────────────┘
                    │                                    │
              route == "react"                    route == "pipeline"
                    │                                    │
                    ▼                                    ▼
        ┌───────────────────┐              ┌───────────────────────────────┐
        │   ReAct Agent     │              │       策略选择                 │
        │                   │              │  full_content / multi_doc_    │
        │  Thought → Action │              │  summary / chunk_recall       │
        │  → Observation    │              └───────────────────────────────┘
        │  → ... → Finish   │                            │
        └───────────────────┘              ┌─────────────┼─────────────────┐
                │                          │             │                 │
                │                   full_content   multi_doc_summary  chunk_recall
                │                          │             │                 │
                │                          ▼             ▼                 ▼
                │                    ┌─────────┐  ┌───────────┐  ┌─────────────────┐
                │                    │ 跳过    │  │ 文档总结   │  │ 子问题→计划→执行 │
                │                    └─────────┘  └───────────┘  └─────────────────┘
                │                          │             │                 │
                │                          └─────────────┼─────────────────┘
                │                                        │
                │                                        ▼
                │                          ┌───────────────────────────┐
                │                          │   Pipeline 答案生成        │
                │                          │   根据意图选择专用 Prompt   │
                │                          └───────────────────────────┘
                │                                        │
                └────────────────────────────────────────┤
                                                         │
                                                         ▼
                                           ┌───────────────────────────┐
                                           │        会话保存            │
                                           │  保存用户问题和最终答案     │
                                           └───────────────────────────┘
```
