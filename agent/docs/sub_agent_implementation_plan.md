# 子 Agent 引入详细实施方案

> 版本: 2.0  
> 最后更新: 2026-01-06  
> 状态: ⚠️ 重大修订 - 基于架构深入分析  
> 参考: Claude Code 插件架构

## ⚠️ 重要提示

本文档经过深度分析后发现原方案存在多处**架构理解偏差**和**过度工程化**问题。  
本版本包含：
- **问题分析**：指出原方案的 10+ 个关键问题
- **修正建议**：提供更务实的优化路径
- **保留内容**：原方案中合理的部分（Markdown 定义、工具限制等）

---

## 一、原方案问题分析

### 1.1 对 Claude Code 架构的根本性误解

#### ❌ 误解 1：Claude Code 的 "agents" 是独立执行实例

**原方案认为**：
- 子 agent 是独立的执行实例，有自己的 LLM、上下文、迭代循环
- 需要创建 `SubAgentExecutor` 来运行完整的 ReAct 循环
- 需要 `ParallelExecutor` 来并发执行多个子 agent 实例

**实际情况**：
从 `claude-code-main/plugins/feature-dev/agents/code-explorer.md` 可以看出，Claude Code 的 agents 是：
- **系统提示模板**（Persona/Role），而非独立进程
- **主 Agent 的不同工作模式**，切换系统提示后仍是同一个会话
- **工具限制**是为了让主 Agent 在特定角色下只能使用特定工具

```yaml
---
name: code-explorer
description: Deeply analyzes existing codebase features...
tools: Glob, Grep, LS, Read, NotebookRead, WebFetch, TodoWrite, WebSearch
model: sonnet
---
```

这个定义的含义是：
- 当主 Agent 需要"代码探索"能力时，**切换**到这个系统提示
- 在此模式下，主 Agent 只能使用列出的工具（工具限制）
- 仍然是**同一个对话上下文**，只是角色和工具集变了

#### ❌ 误解 2：需要并行创建多个 LLM 实例

**原方案设计**：
```python
# 为每个子 agent 创建独立 LLM 实例
async def execute(self, tasks: List[ParallelTask]) -> List[SubAgentResult]:
    for task in tasks:
        executor = SubAgentExecutor(
            config=config,
            parent_llm=self.parent_llm,  # 每个 executor 都有 LLM
            ...
        )
        await executor.run()  # 每个都运行完整 ReAct 循环
```

**问题**：
- 真正的并行意味着同时发起多个 LLM 请求，**Token 成本呈倍数增长**
- 每个子 agent 运行 5-10 次迭代，意味着 5 个文档 × 10 次迭代 = 50 次 LLM 调用
- Claude Code 中的"并行"实际是**批量任务描述**，由主 Agent 决策如何处理

#### ❌ 误解 3：子 agent 需要独立上下文

**原方案**：
```python
# 构建初始消息
messages = [
    {"role": "system", "content": self.config.system_prompt},
    {"role": "user", "content": user_content}
]
# 子 Agent 有独立上下文，不继承父 Agent 的对话历史
```

**问题**：
- 如果上下文完全独立，子 agent 无法访问用户的原始问题和会话历史
- 如果要注入上下文，那"独立"就失去意义
- 真正的问题是**如何分割和管理上下文**，而非创建隔离的上下文

### 1.2 当前系统已有的优秀设计被忽略

#### ✅ 当前系统已经实现了并行文档处理

查看 `agent_system/src/agent/nodes/document_nodes.py` 和 `answer_nodes.py`：

```python
# document_nodes.py - 已有的并行总结
async def document_summary_node_stream(self, state):
    # 使用信号量限制并发
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_LLM_CALLS)
    
    async def summarize_doc(doc_id, content):
        async with semaphore:
            # 为单个文档生成总结
            ...
    
    # 并行处理所有文档
    tasks = [summarize_doc(doc_id, content) for doc_id, content in docs.items()]
    results = await asyncio.gather(*tasks)
```

**这个设计**：
- ✅ 已经实现了真正的并行（asyncio.gather）
- ✅ 使用信号量控制并发数，避免资源耗尽
- ✅ 有完善的缓存机制（document_summary_cache）
- ✅ 支持流式输出进度（doc_summary_start/complete 事件）

#### ✅ Pipeline 策略选择设计合理

```python
# planning_nodes.py - 已有的策略选择
def strategy_selection_node_stream(self, state):
    if doc_count > 1 and intent in [LITERATURE_SUMMARY, REVIEW_GENERATION]:
        strategy = "multi_doc_summary"  # 并行为每篇文档生成总结
    else:
        strategy = "chunk_recall"  # 分块召回
```

这个路由逻辑是**基于任务特性的合理决策**，而非"硬编码"。

### 1.3 原方案的过度工程化

#### 问题清单

| 问题 | 严重性 | 说明 |
|------|--------|------|
| 1. 引入大量新抽象 | 🔴 高 | SubAgentLoader, SubAgentExecutor, ParallelExecutor, SubAgentConfig... |
| 2. Token 成本暴增 | 🔴 高 | 并行创建 5 个子 agent，每个运行 10 次迭代 = 50 次 LLM 调用 |
| 3. 复杂度急剧增加 | 🔴 高 | 错误处理、状态同步、token 计数汇总、会话管理... |
| 4. 与现有代码重复 | 🟡 中 | 当前已有并行处理、缓存机制、流式输出 |
| 5. 测试和维护成本 | 🟡 中 | 单元测试、集成测试、并发测试、超时处理... |
| 6. 响应延迟增加 | 🟡 中 | 启动多个子 agent 需要时间，用户等待更久 |
| 7. 调试困难 | 🟡 中 | 多个并行执行流，错误追踪复杂 |
| 8. 缺少降级策略 | 🟢 低 | 如果部分子 agent 失败，如何处理？ |

### 1.4 未充分考虑的关键问题

#### 1. 成本评估缺失

**示例场景**：用户要求"总结这 5 篇论文"

**原方案成本**：
```
主 Agent:
  - 意图识别: 1 次 LLM 调用（~1000 tokens）
  - 决定启动 5 个子 agent: 1 次 LLM 调用（~500 tokens）
  
并行子 Agents（5 个）:
  - 每个子 agent 运行 5-10 次迭代
  - 每次迭代: ~5000 tokens（输入） + ~1000 tokens（输出）
  - 总计: 5 agents × 8 iterations × 6000 tokens = 240,000 tokens
  
最终综合: 1 次 LLM 调用（~10000 tokens）

总计: ~251,500 tokens
```

**当前系统成本**：
```
意图识别: ~1000 tokens
并行文档总结（5 个）:
  - 每个文档: 1 次 LLM 调用（~8000 tokens 输入 + ~1000 tokens 输出）
  - 总计: 5 × 9000 = 45,000 tokens
最终综合: ~10000 tokens

总计: ~56,000 tokens
```

**成本对比**：原方案是当前系统的 **4.5 倍**！

#### 2. 上下文管理的矛盾

**原方案说法**：
> 子 Agent 有独立上下文，不继承父 Agent 的对话历史

**但又说**：
```python
async def run(self, task: str, context: str = "") -> SubAgentResult:
    # 注入的上下文（如文档内容）
    user_content = f"{context}\n\n任务: {task}" if context else task
```

**矛盾点**：
- 如果上下文独立，子 agent 怎么知道用户的意图？
- 如果要注入上下文，那"独立"的意义何在？
- 如何确定注入多少上下文才合适？

#### 3. Pipeline vs ReAct 的真正差异被误解

**原方案认为**：
> 架构割裂：根据意图类型硬编码路由

**实际情况**：
- Pipeline 适合**确定性任务**：文献总结、综述生成（步骤固定）
- ReAct 适合**探索性任务**：问答、推理（需要动态决策）

这不是"硬编码"，而是**任务特性决定的合理选择**。

#### 4. 缺少与现有系统的对比分析

原方案没有回答：
- ❓ 当前的 `multi_doc_summary` 策略有什么问题？
- ❓ 上下文超限的具体场景是什么？（现在有 100k context window）
- ❓ 为什么现有的并行处理不够？
- ❓ 引入子 agent 如何解决这些问题？

### 1.5 合理的部分

尽管存在诸多问题，原方案中仍有一些值得借鉴的想法：

✅ **Markdown 定义工具/Agent**：
- 使用 YAML frontmatter + Markdown body 定义配置
- 热加载、易于扩展

✅ **工具限制**：
- 为不同任务类型提供受限的工具集
- 避免 LLM 选择错误的工具

✅ **系统提示模板化**：
- 为不同任务类型准备专门的系统提示
- 提高任务完成质量

---

## 二、修正后的优化方案

### 2.1 真正需要解决的问题

#### 问题 1：上下文超限（真实场景）

**场景**：
- 用户上传 20 篇长论文（每篇 50k tokens）
- 当前系统需要将所有文档总结放入最终答案生成的上下文

**当前限制**：
- Claude Sonnet 3.5 上下文窗口: 200k tokens
- 20 篇文档总结（每篇压缩到 2k tokens）= 40k tokens
- 加上系统提示、用户问题、会话历史 ≈ 50k tokens
- **目前仍在安全范围内**

**真正的瓶颈**：
- 100+ 篇文档的综述生成
- 需要更激进的压缩策略

#### 问题 2：代码复杂度（确实存在）

当前 `agent.py` 和各种 nodes 确实存在：
- 路由逻辑分散
- 节点职责不够清晰
- 缺少统一的抽象

#### 问题 3：扩展性（部分存在）

添加新的任务类型需要：
1. 修改 `IntentType` 枚举
2. 创建新的 prompt 模板
3. 可能需要修改策略选择逻辑

### 2.2 务实的优化建议

#### 方案 A：轻量级 Prompt 模板系统（推荐）

**核心思想**：
- 保留当前架构（Pipeline + ReAct）
- 将系统提示和工具配置抽离到 Markdown 文件
- 主 Agent 根据任务类型**切换系统提示**，而非启动子进程

**目录结构**：
```
agent_system/
├── config/
│   ├── prompts/                        # 任务类型提示模板（新增）
│   │   ├── literature-summary.md       # 文献总结提示
│   │   ├── review-generation.md        # 综述生成提示
│   │   ├── literature-qa.md            # 文献问答提示
│   │   └── document-comparison.md      # 文档对比提示
│   └── tools/                          # 工具配置（新增）
│       ├── recall-tool.md              # Recall 工具定义
│       └── web-search-tool.md          # Web Search 工具定义
```

**Prompt 模板格式**：
```markdown
---
name: literature-summary
description: 总结单个或多个学术文献的核心内容
intent_types: LITERATURE_SUMMARY
strategy: multi_doc_summary
tools: recall
max_iterations: 5
---

你是文献总结专家，擅长提取学术论文的核心信息。

## 核心职责
1. 阅读文档全文或关键片段
2. 提取核心观点和关键数据
3. 生成结构化摘要

## 输出格式
...
```

**实现**：
```python
# agent_system/src/prompts/prompt_manager.py

class PromptManager:
    """管理任务类型的系统提示"""
    
    def __init__(self, prompts_dir: str = "config/prompts"):
        self.prompts_dir = Path(prompts_dir)
        self._cache: Dict[str, PromptConfig] = {}
    
    def load_all(self):
        """加载所有提示模板"""
        for md_file in self.prompts_dir.glob("*.md"):
            config = self._parse_prompt_file(md_file)
            for intent in config.intent_types:
                self._cache[intent] = config
    
    def get_prompt_for_intent(self, intent: IntentType) -> PromptConfig:
        """根据意图获取提示配置"""
        return self._cache.get(intent.value)


```

**优势**：
- ✅ 实现成本低（~1-2 天）
- ✅ 保留现有架构和代码
- ✅ 支持热加载和动态扩展
- ✅ 不增加 Token 成本
- ✅ 易于测试和维护

**劣势**：
- ⚠️ 仍然保留 Pipeline/ReAct 双路由

#### 方案 B：统一 ReAct + 动态工具注入（激进）

**核心思想**：
- 完全移除 Pipeline 路线
- 所有任务都走 ReAct，但根据任务类型动态调整工具集和系统提示
- 使用更智能的工具描述来引导 LLM 选择合适的工具

**示例**：
```python
# 根据意图类型调整工具
if detected_intent == IntentType.LITERATURE_SUMMARY:
    tools = [
        summarize_document_tool,  # 新增：文档总结工具
        finish_tool
    ]
    system_prompt = LITERATURE_SUMMARY_PROMPT
elif detected_intent == IntentType.LITERATURE_QA:
    tools = [
        recall_tool,
        web_search_tool,
        finish_tool
    ]
    system_prompt = QA_PROMPT
```

**优势**：
- ✅ 架构统一，只有一条执行路径
- ✅ LLM 自主决策，更灵活
- ✅ 易于扩展新任务类型

**劣势**：
- ⚠️ 需要重构现有 Pipeline 逻辑
- ⚠️ 风险较高，可能影响稳定性
- ⚠️ 需要充分测试

#### 方案 C：渐进式上下文压缩（针对超限问题）

**核心思想**：
- 保留当前架构
- 增强文档总结的分层压缩能力
- 使用更激进的信息提取策略

**实现**：
```python
# 两阶段压缩
async def generate_hierarchical_summary(doc_content: str, user_query: str):
    # 第一阶段：生成详细总结（保留细节）
    detailed_summary = await generate_summary(doc_content, level="detailed")
    
    # 第二阶段：根据用户问题提取关键信息
    query_focused_summary = await extract_relevant_info(
        detailed_summary,
        user_query
    )
    
    return query_focused_summary
```

**优势**：
- ✅ 针对性解决上下文超限问题
- ✅ 不改变整体架构
- ✅ 可以与方案 A 结合

**劣势**：
- ⚠️ 增加一次 LLM 调用
- ⚠️ 可能丢失部分信息

### 2.3 推荐实施路径

#### 第一阶段：轻量级优化（1-2 周）

1. **实施方案 A**：Markdown 配置化系统提示
   - 创建 `config/prompts/` 目录
   - 实现 `PromptManager`
   - 将现有 prompt 模板迁移到 Markdown 文件

2. **优化文档总结缓存**：
   - 增加缓存命中率监控
   - 实现缓存预热机制

3. **改进日志和监控**：
   - 添加 Token 使用统计
   - 添加性能指标监控

#### 第二阶段：架构简化（2-4 周，可选）

1. **评估 Pipeline 移除的可行性**：
   - 对比测试 Pipeline vs ReAct 的效果
   - 分析哪些场景必须用 Pipeline

2. **如果可行，实施方案 B**：
   - 渐进式迁移 Pipeline 任务到 ReAct
   - 保留回滚开关

#### 第三阶段：高级优化（按需）

1. **如果遇到上下文超限问题**：
   - 实施方案 C：分层压缩
   - 实现更智能的信息过滤

2. **如果需要更强的并行能力**：
   - 优化现有的 `asyncio.gather` 并发控制
   - 实现任务优先级调度

---

## 三、保留的合理设计（原方案）

以下是原方案中值得保留和借鉴的设计思想：

### 3.1 Markdown 配置文件格式（✅ 保留）

**YAML Frontmatter + Markdown Body** 是一个优秀的配置格式：

```markdown
---
name: document-summarizer
description: 总结单个文档的核心内容
tools: recall
max_iterations: 5
timeout: 120
---

你是文档总结专家，擅长提取学术论文和技术文档的核心信息。

## 核心职责
1. 阅读文档全文或关键片段
2. 提取核心观点和关键数据
3. 生成结构化摘要
...
```

**优势**：
- 易于阅读和编辑
- 支持版本控制
- 可以包含丰富的文档说明
- 热加载友好

### 3.2 工具限制机制（✅ 保留）

为不同任务类型指定可用工具集：

```yaml
---
name: document-summarizer
tools: recall  # 只能使用 recall 工具
---
```

```yaml
---
name: web-researcher
tools: web_search  # 只能使用 web_search 工具
---
```

**好处**：
- 避免 LLM 选择不相关的工具
- 提高任务完成效率
- 减少错误率

### 3.3 配置热加载（✅ 保留）

```python
@app.post("/agents/reload")
async def reload_agents():
    """热重载配置"""
    loader = get_prompt_manager()
    loader.reload()
    return {"success": True}
```

**好处**：
- 无需重启服务即可更新配置
- 方便 A/B 测试不同的提示
- 加快迭代速度

### 3.4 分层日志和监控（✅ 保留）

```python
logger.info("=" * 60)
logger.info("📦 文档总结缓存状态:")
logger.info(f"   - 总文档数: {total_docs}")
logger.info(f"   - 缓存命中: {cache_hit_count}")
logger.info("=" * 60)
```

这种清晰的日志格式值得在整个系统中推广。

---

## 四、对原方案的完整评估

### 4.1 架构设计评分

| 维度 | 原方案 | 修正方案 A | 说明 |
|------|--------|-----------|------|
| **正确性** | ⭐⭐ | ⭐⭐⭐⭐⭐ | 原方案误解了 Claude Code 架构 |
| **实用性** | ⭐⭐ | ⭐⭐⭐⭐ | 原方案过度工程化，实用性低 |
| **成本** | ⭐ | ⭐⭐⭐⭐ | 原方案 Token 成本是当前 4.5 倍 |
| **复杂度** | ⭐ | ⭐⭐⭐⭐ | 原方案引入大量新抽象 |
| **扩展性** | ⭐⭐⭐ | ⭐⭐⭐⭐ | 两者都支持扩展，但方案 A 更简单 |
| **维护性** | ⭐⭐ | ⭐⭐⭐⭐ | 原方案测试和调试成本高 |
| **风险** | 🔴 高 | 🟢 低 | 原方案需要大规模重构 |

### 4.2 技术债务评估

**引入原方案会带来的技术债务**：

1. **代码债务**：
   - 新增 ~1500 行代码（SubAgentExecutor, ParallelExecutor, Loader...）
   - 需要维护两套执行路径（现有 + 子 agent）

2. **测试债务**：
   - 单元测试：~20 个新测试用例
   - 集成测试：并发、超时、错误处理
   - 性能测试：成本、延迟监控

3. **文档债务**：
   - 开发文档、API 文档
   - 故障排查指南
   - 最佳实践文档

4. **运维债务**：
   - 新的监控指标
   - 报警规则
   - 性能调优

**修正方案 A 的技术债务**：
- 新增 ~300 行代码（PromptManager）
- 测试成本低
- 文档简单
- 运维影响小

### 4.3 未考虑的关键场景

#### 场景 1：子 Agent 失败处理

**原方案缺失**：
- 如果 5 个并行子 agent 中 2 个失败，如何处理？
- 部分成功的结果如何合并？
- 是否需要重试机制？

#### 场景 2：用户交互

**原方案缺失**：
- 子 agent 执行期间用户取消请求怎么办？
- 如何向用户展示多个子 agent 的进度？
- 用户如何理解"子 agent"的概念？

#### 场景 3：成本控制

**原方案缺失**：
- 如何限制单个请求的最大 Token 消耗？
- 如何防止并行子 agent 导致成本失控？
- 是否需要成本预估和预警？

#### 场景 4：调试和排错

**原方案缺失**：
- 如何追踪子 agent 的执行链？
- 如何重现并行执行中的随机错误？
- 日志如何关联主 agent 和子 agent？

---

## 五、最终建议

### 5.1 不建议实施原方案

**核心理由**：
1. **架构理解错误**：误解了 Claude Code 的设计，导致方向性错误
2. **成本不可接受**：Token 成本增加 4.5 倍，经济上不可行
3. **复杂度过高**：引入大量新抽象，维护成本激增
4. **解决伪问题**：当前系统已有并行处理，上下文超限是低频场景
5. **风险太大**：需要大规模重构，稳定性难以保证

### 5.2 推荐实施修正方案 A

**轻量级 Prompt 模板系统**：
- ✅ 实现成本低（1-2 天）
- ✅ 保留现有架构
- ✅ 支持动态扩展
- ✅ 借鉴 Claude Code 的优秀设计（Markdown 配置）
- ✅ 风险可控

**实施步骤**：
1. 创建 `config/prompts/` 目录
2. 实现 `PromptManager` 类
3. 将现有 prompt 模板迁移到 Markdown 文件
4. 更新 `agent.py` 使用新的 PromptManager
5. 添加热加载 API 端点
6. 测试和验证

### 5.3 长期优化路径

**如果未来确实遇到瓶颈**：

1. **上下文超限**（100+ 篇文档）：
   - 实施分层压缩策略
   - 使用更激进的信息过滤
   - 考虑分批处理

2. **架构复杂度**（确实存在）：
   - 评估 Pipeline → ReAct 迁移的可行性
   - 简化路由逻辑
   - 统一执行路径

3. **扩展性不足**（未来可能）：
   - 插件化架构
   - DSL 定义任务流程
   - 可视化工作流编排

---

## 六、附录：原方案剩余内容（供参考）

以下是原方案的剩余内容，标注了✅保留 / ⚠️修改 / ❌废弃：

### 附录 A：原架构设计（❌ 废弃）

**原因**：基于错误的架构理解

### 6.1 原系统架构

```
┌─────────────────────────────────────────┐
│           用户请求                       │
└─────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│      意图识别 + Prompt 选择              │
│  根据任务类型加载对应的系统提示          │
└─────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│      主 Agent (统一执行引擎)             │
│  - 使用选定的系统提示                    │
│  - 使用限定的工具集                      │
│  - 当前架构 (Pipeline/ReAct)             │
└─────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│           最终答案                       │
└─────────────────────────────────────────┘
```

**对比原方案**：
- ❌ 原方案：创建多个子 Agent 实例，每个运行完整循环
- ✅ 修正方案：主 Agent 切换系统提示，保持单一执行路径

### 附录 B：Prompt 模板示例（✅ 保留并修改）

#### 文献总结模板

```markdown
---
name: literature-summary
description: 总结学术文献的核心内容
intent_types: LITERATURE_SUMMARY
strategy: multi_doc_summary
tools: recall
---

你是文献总结专家，擅长提取学术论文的核心信息。

## 核心职责
1. 阅读文档关键片段或完整内容
2. 提取核心观点和关键数据
3. 生成结构化摘要

## 输出格式
### 主题
[一句话概括]

### 核心观点
1. [观点1]
2. [观点2]
3. [观点3]

### 关键数据/结论
- [数据/结论1]
- [数据/结论2]

### 研究方法（如适用）
[简述研究方法]

## 注意事项
- 保持客观，不添加主观评价
- 引用原文时使用引号
- 标注不确定的信息
```

#### 综述生成模板

```markdown
---
name: review-generation
description: 基于多篇文献生成综述报告
intent_types: REVIEW_GENERATION
strategy: multi_doc_summary
tools: recall
---

你是学术综述撰写专家，擅长整合多篇文献生成结构化的综述报告。

## 核心职责
1. 理解研究领域背景
2. 梳理文献脉络
3. 综合各方观点
4. 生成结构化综述

## 输出格式
### 1. 引言
[研究背景和综述目的]

### 2. 研究现状
#### 2.1 [主题1]
[相关文献综述]

#### 2.2 [主题2]
[相关文献综述]

### 3. 主要发现
[综合各文献的主要发现]

### 4. 研究趋势
[领域发展趋势分析]

### 5. 结论与展望
[总结和未来研究方向]

## 注意事项
- 保持学术写作风格
- 正确引用文献
- 逻辑清晰，层次分明
- 避免简单罗列，要有分析和综合
```

### 附录 C：配置加载器实现（✅ 简化保留）

```python
# agent_system/src/prompts/prompt_manager.py

import yaml
from pathlib import Path
from typing import Dict, Optional, List
from dataclasses import dataclass
from ..agent.state import IntentType

@dataclass
class PromptConfig:
    """任务类型的提示配置"""
    name: str
    description: str
    system_prompt: str
    intent_types: List[IntentType]
    strategy: Optional[str] = None
    tools: Optional[List[str]] = None

class PromptManager:
    """管理任务类型的系统提示"""
    
    def __init__(self, prompts_dir: str = "config/prompts"):
        self.prompts_dir = Path(prompts_dir)
        self._cache: Dict[IntentType, PromptConfig] = {}
        self._loaded = False
    
    def load_all(self) -> Dict[IntentType, PromptConfig]:
        """加载所有提示模板"""
        if not self.prompts_dir.exists():
            return {}
        
        for md_file in self.prompts_dir.glob("*.md"):
            try:
                config = self._parse_prompt_file(md_file)
                for intent in config.intent_types:
                    self._cache[intent] = config
            except Exception as e:
                logger.warning(f"Failed to load prompt {md_file}: {e}")
        
        self._loaded = True
        return self._cache
    
    def get_prompt_for_intent(self, intent: IntentType) -> Optional[PromptConfig]:
        """根据意图获取提示配置"""
        if not self._loaded:
            self.load_all()
        return self._cache.get(intent)
    
    def reload(self):
        """重新加载配置（热更新）"""
        self._cache.clear()
        self._loaded = False
        self.load_all()
    
    def _parse_prompt_file(self, file_path: Path) -> PromptConfig:
        """解析 Markdown 文件"""
        content = file_path.read_text(encoding='utf-8')
        
        if not content.startswith('---'):
            raise ValueError(f"Invalid prompt file (missing frontmatter): {file_path}")
        
        parts = content.split('---', 2)
        if len(parts) < 3:
            raise ValueError(f"Invalid prompt file format: {file_path}")
        
        frontmatter = yaml.safe_load(parts[1])
        body = parts[2].strip()
        
        # 解析 intent_types
        intent_types_raw = frontmatter.get('intent_types', [])
        if isinstance(intent_types_raw, str):
            intent_types_raw = [intent_types_raw]
        intent_types = [IntentType(it) for it in intent_types_raw]
        
        # 解析 tools
        tools = None
        if 'tools' in frontmatter:
            tools_str = frontmatter['tools']
            if isinstance(tools_str, str):
                tools = [t.strip() for t in tools_str.split(',')]
            elif isinstance(tools_str, list):
                tools = tools_str
        
        return PromptConfig(
            name=frontmatter['name'],
            description=frontmatter.get('description', ''),
            system_prompt=body,
            intent_types=intent_types,
            strategy=frontmatter.get('strategy'),
            tools=tools
        )

# 全局单例
_manager: Optional[PromptManager] = None

def get_prompt_manager() -> PromptManager:
    global _manager
    if _manager is None:
        _manager = PromptManager()
    return _manager
```

### 附录 D：原方案废弃的组件（❌ 不建议实施）

以下组件在原方案中占据大量篇幅，但**不建议实施**：

1. **SubAgentExecutor**（~200 行代码）
   - 原因：基于错误的架构理解
   - 成本：增加 Token 消耗 4.5 倍

2. **ParallelExecutor**（~150 行代码）
   - 原因：当前系统已有 asyncio.gather 并行处理
   - 冗余：功能重复

3. **spawn_sub_agent_tool**（~100 行代码）
   - 原因：创建独立子 agent 实例不可行
   - 替代：直接切换系统提示

4. **spawn_parallel_sub_agents_tool**（~100 行代码）
   - 原因：成本和复杂度不可接受
   - 替代：优化现有并行处理

---

## 七、实施检查清单

### 方案 A 实施检查清单（推荐）

#### 阶段 1：基础设施（1 天）
- [ ] 创建 `config/prompts/` 目录
- [ ] 实现 `PromptManager` 类（~150 行）
- [ ] 编写单元测试（~50 行）

#### 阶段 2：迁移现有 Prompt（1 天）
- [ ] 迁移 `literature_summary_prompt` → `literature-summary.md`
- [ ] 迁移 `review_generation_prompt` → `review-generation.md`
- [ ] 迁移 `literature_qa_prompt` → `literature-qa.md`
- [ ] 迁移 `document_comparison_prompt` → `document-comparison.md`

#### 阶段 3：集成到主 Agent（半天）
- [ ] 修改 `agent.py` 使用 `PromptManager`
- [ ] 更新相关节点使用新的 prompt 获取方式
- [ ] 保留旧代码作为回滚备份

#### 阶段 4：测试和验证（半天）
- [ ] 单元测试通过
- [ ] 集成测试通过
- [ ] 对比测试：新旧实现效果一致
- [ ] 性能测试：无明显性能下降

#### 阶段 5：热加载功能（半天）
- [ ] 添加 `/prompts/reload` API 端点
- [ ] 添加 `/prompts` 列表端点
- [ ] 更新 API 文档

#### 总耗时：~3 天
#### 代码量：~300 行新代码
#### 风险等级：🟢 低

---

## 八、总结与建议

### 核心结论

1. **原方案不可行**：
   - 基于对 Claude Code 架构的误解
   - Token 成本增加 4.5 倍
   - 复杂度过高，维护困难
   - 解决的是伪问题

2. **修正方案 A 可行**：
   - 实现成本低（3 天）
   - 保留现有架构
   - 借鉴 Claude Code 优秀设计
   - 风险可控

3. **当前系统其实不错**：
   - 已有并行处理机制
   - 缓存机制完善
   - 架构清晰（Pipeline/ReAct 有其合理性）
   - 上下文超限是低频场景

### 最终建议

**短期（1-2 周）**：
- ✅ 实施方案 A：Markdown 配置化系统提示
- ✅ 优化现有缓存机制
- ✅ 改进监控和日志

**中期（1-2 月）**：
- 🤔 评估 Pipeline → ReAct 迁移可行性
- 🤔 收集上下文超限的真实数据
- 🤔 考虑是否需要更激进的优化

**长期（按需）**：
- 如果确实遇到瓶颈，再考虑更复杂的方案
- 基于真实数据做决策，而非臆测

### 给团队的建议

1. **避免过度设计**：
   - 不要为了"先进架构"而引入复杂度
   - 关注实际问题和成本收益

2. **深入理解参考架构**：
   - Claude Code 的 agents 不是独立进程
   - 理解设计背后的原理，而非照搬形式

3. **数据驱动决策**：
   - 收集上下文超限的真实频率
   - 测量 Token 成本和响应延迟
   - 基于数据优化，而非猜测

4. **渐进式演进**：
   - 从小的改进开始
   - 验证效果后再扩大范围
   - 保留回滚能力

---

## 九、参考资料

- [Claude Code Plugins](https://github.com/anthropics/claude-code/tree/main/plugins)
- [LangChain Tools](https://python.langchain.com/docs/modules/agents/tools/)
- [ReAct Pattern](https://arxiv.org/abs/2210.03629)
- 当前系统文档：
  - `workflow_algorithm.md`
  - `architecture_redesign_discussion.md`
  - `智能体工作流详细介绍.md`

### 附录 E：原方案删减说明（❌ 已删除）

以下章节在原方案中存在，但因基于错误理解而删除：

1. **子 Agent 执行器实现**（~200 行代码）
   - 删除原因：基于"独立执行实例"的错误理解
   - 成本影响：会导致 Token 成本增加 4.5 倍

2. **并行执行器实现**（~150 行代码）
   - 删除原因：当前系统已有 asyncio.gather 并行处理
   - 功能重复：与现有实现冗余

3. **子 Agent 工具定义**（~200 行代码）
   - 删除原因：创建独立子 agent 实例不可行
   - 替代方案：直接在主 Agent 中切换系统提示

4. **对现有系统的影响分析**
   - 删除原因：基于不会实施的方案分析影响无意义
   - 实际影响：方案 A 几乎不影响现有系统

5. **迁移策略**（4 阶段，6-9 周）
   - 删除原因：时间和成本评估基于错误方案
   - 实际时间：方案 A 仅需 3 天

6. **子 Agent 定义示例**（多个 Markdown 文件）
   - 保留部分：Markdown 格式和结构设计优秀
   - 修改为：Prompt 模板示例（附录 B）

7. **ReAct Prompt 模板更新**
   - 删除原因：不需要添加 spawn_sub_agent 工具
   - 保留原有：当前的 ReAct prompt 已经足够好

8. **API 端点扩展**
   - 删除原因：子 agent 管理端点不需要
   - 保留：Prompt 热加载端点（方案 A 需要）

9. **测试计划**（单元、集成、并发测试）
   - 删除原因：测试不存在的组件无意义
   - 简化为：方案 A 的简单测试清单

10. **性能考量**（并发控制、资源管理、监控指标）
    - 删除原因：方案 A 性能影响极小
    - 现有监控：当前系统监控已足够

**删除内容统计**：
- 原文档：~1570 行
- 删除内容：~1000 行（基于错误理解的实现细节）
- 保留内容：~570 行（问题分析、修正方案、合理建议）

---

## 十、常见问题（FAQ）

### Q1: 为什么不能像原方案那样创建独立的子 Agent？

**A**: 因为这会导致：
1. **成本暴增**：每个子 agent 运行完整 ReAct 循环，Token 成本 4.5 倍
2. **复杂度高**：错误处理、状态同步、token 计数汇总等问题
3. **不是真需求**：当前系统已有并行处理，上下文超限是低频场景

### Q2: Claude Code 的 agents 是怎么工作的？

**A**: Claude Code 的 agents 是：
- **系统提示模板**（Persona），而非独立进程
- 主 Agent 根据任务切换系统提示和工具限制
- 仍然是同一个会话上下文

### Q3: 当前系统的并行处理有什么问题吗？

**A**: 当前系统的并行处理（document_summary_node）已经很好：
- ✅ 使用 asyncio.gather 真正并行
- ✅ 信号量控制并发数
- ✅ 完善的缓存机制
- ✅ 流式输出进度

没有必要替换。

### Q4: 什么时候会遇到上下文超限问题？

**A**: 真正的上下文超限场景：
- 100+ 篇长文档的综述生成
- 每篇文档压缩后仍有 2-3k tokens
- 总上下文超过 200k tokens

**但这是低频场景**，不应为此大规模重构。

### Q5: 方案 A 能解决什么问题？

**A**: 方案 A（Markdown 配置化）能解决：
- ✅ 系统提示管理分散问题
- ✅ 扩展性：新任务类型无需改代码
- ✅ 可维护性：配置文件易于理解和修改
- ✅ 热加载：无需重启即可更新提示

### Q6: 如果未来真的需要更复杂的方案怎么办？

**A**: 基于数据驱动的决策：
1. 收集真实的上下文超限案例
2. 测量 Token 成本和响应延迟
3. 评估不同方案的成本收益
4. 选择最合适的方案

**不要基于臆测过度设计。**

### Q7: 原方案的作者花了这么多精力，有什么价值吗？

**A**: 有价值的部分：
- ✅ Markdown 配置文件格式设计优秀
- ✅ 工具限制机制值得借鉴
- ✅ 热加载思想正确
- ✅ 深入分析了 Claude Code 架构（虽然理解有偏差）

**学习价值**：
- 理解参考架构的重要性
- 过度工程化的代价
- 成本评估的必要性

---

## 十一、致谢与版本历史

### 致谢

感谢原方案作者的详细调研和设计工作。虽然方案存在架构理解偏差，但其中的许多思考和设计（如 Markdown 配置、工具限制）仍有借鉴价值。

### 版本历史

**v1.0** (2026-01-06 初版)
- 原始方案：子 Agent 独立执行实例
- 包含完整的实现细节（~1570 行）

**v2.0** (2026-01-06 重大修订)
- 指出原方案的架构理解偏差
- 提出修正方案 A（轻量级 Prompt 模板系统）
- 删除基于错误理解的大量实现细节
- 新增成本对比分析和FAQ
- 文档精简至 ~800 行，聚焦实用性

---

## 十二、最后的话

这份文档的修订过程本身就是一个很好的案例研究：

1. **深入理解参考架构**：不能只看表面形式，要理解设计原理
2. **成本收益分析**：任何架构决策都要考虑成本
3. **数据驱动决策**：基于真实问题，而非臆测
4. **渐进式演进**：从小的改进开始，验证效果
5. **保持简单**：复杂度是技术债务的主要来源

希望这份修订后的文档能帮助团队做出明智的决策。

**记住：最好的代码是不需要写的代码。** 🚀

---

*文档结束*

### 5.1 后端影响

#### 5.1.1 Agent System 改动

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `agent_system/src/agent/agent.py` | 修改 | 注册新工具，移除 Pipeline 路由 |
| `agent_system/src/tools/__init__.py` | 修改 | 导出新工具 |
| `agent_system/src/tools/registry.py` | 修改 | 注册子 Agent 工具 |
| `agent_system/api.py` | 修改 | 添加子 Agent 配置热加载端点 |
| `agent_system/config/agents/*.md` | 新增 | 子 Agent 定义文件 |
| `agent_system/src/subagent/` | 新增 | 子 Agent 模块 |

**主要改动点**：

1. **移除 Pipeline 路由**：
```python
# 修改前 (agent.py)
if route == "react":
    # ReAct 路线
elif route == "pipeline":
    # Pipeline 路线

# 修改后
# 统一使用 ReAct 循环，Pipeline 逻辑由子 Agent 实现
async for event in agent_nodes.react_agent_node_stream(state):
    yield event
```

2. **注册新工具**：
```python
# 在 agent.py 中
from src.tools.spawn_sub_agent_tool import SpawnSubAgentTool, SpawnParallelSubAgentsTool

# 创建工具时
spawn_tool = SpawnSubAgentTool()
spawn_tool.parent_llm = runtime_llm
spawn_tool.parent_tools = [recall_tool, web_search_tool, ...]
spawn_tool.parent_context = {"kb_id": kb_id, "user_id": user_id, ...}

tools = [recall_tool, web_search_tool, spawn_tool, spawn_parallel_tool, ...]
```

#### 5.1.2 后端 API 改动

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `src/rag/service.py` | 无改动 | 接口保持不变 |
| `src/rag/schemas.py` | 无改动 | 数据模型保持不变 |
| `src/rag/controller.py` | 无改动 | 路由保持不变 |

**兼容性说明**：
- 后端 API 接口完全保持不变
- 前端无需修改任何 API 调用
- 内部实现从 Pipeline 切换到子 Agent，对外透明

### 5.2 前端影响

#### 5.2.1 SSE 事件类型

**现有事件（保持不变）**：
- `thinking_start` / `thinking_end`
- `thought_chunk`
- `answer_chunk`
- `final_answer`
- `error`
- `token_usage`

**新增事件（可选支持）**：
| 事件类型 | 数据结构 | 说明 |
|---------|---------|------|
| `sub_agent_start` | `{agent_type, task, index}` | 子 Agent 开始执行 |
| `sub_agent_progress` | `{agent_type, index, iteration, content}` | 子 Agent 进度 |
| `sub_agent_complete` | `{agent_type, index, success, output}` | 子 Agent 完成 |

**前端处理建议**：
```typescript
// web/hooks/useRAGChat.ts

// 新增事件处理（可选）
case 'sub_agent_start':
  // 显示子 Agent 开始执行的提示
  setSubAgentStatus(prev => ({
    ...prev,
    [data.index]: { status: 'running', type: data.agent_type }
  }));
  break;

case 'sub_agent_complete':
  // 更新子 Agent 完成状态
  setSubAgentStatus(prev => ({
    ...prev,
    [data.index]: { status: data.success ? 'done' : 'error' }
  }));
  break;
```

#### 5.2.2 UI 展示建议

**方案 A：简单模式（推荐初期）**
- 不展示子 Agent 细节
- 只显示整体思考过程
- 前端无需改动

**方案 B：进度展示模式**
- 显示子 Agent 执行进度条
- 类似现有的 `doc_summary_*` 事件处理
- 复用 `DocumentProgress` 组件

```tsx
// 复用现有组件
<DocumentProgress
  documents={subAgentTasks}
  title="正在并行处理..."
/>
```


### 5.3 Token 计数影响

子 Agent 引入后，Token 计数需要汇总：

```python
# 主 Agent Token 计数
main_agent_tokens = {
    "input_tokens": 1000,
    "output_tokens": 500
}

# 子 Agent Token 计数（需要累加）
sub_agent_1_tokens = {"input_tokens": 800, "output_tokens": 300}
sub_agent_2_tokens = {"input_tokens": 900, "output_tokens": 400}

# 总计
total_tokens = {
    "input_tokens": 1000 + 800 + 900,  # 2700
    "output_tokens": 500 + 300 + 400   # 1200
}
```

**实现方式**：
```python
# 在 SubAgentExecutor 中
class SubAgentExecutor:
    async def run(self, task, context) -> SubAgentResult:
        # ... 执行逻辑 ...
        
        return SubAgentResult(
            output=response.content,
            iterations=i + 1,
            token_usage={
                "input_tokens": self.token_counter.input_tokens,
                "output_tokens": self.token_counter.output_tokens
            }
        )

# 在主 Agent 中汇总
class SpawnParallelSubAgentsTool:
    async def _arun(self, tasks):
        results = await executor.execute(parallel_tasks)
        
        # 汇总 token 使用
        total_input = sum(r.token_usage.get("input_tokens", 0) for r in results)
        total_output = sum(r.token_usage.get("output_tokens", 0) for r in results)
        
        # 更新父 Agent 的 token 计数器
        self.parent_context["token_counter"].input_tokens += total_input
        self.parent_context["token_counter"].output_tokens += total_output
```

### 5.4 会话管理影响

**关键原则**：子 Agent 不保存独立会话历史

```python
# 主 Agent 会话
session_history = [
    {"role": "user", "content": "帮我总结这5篇论文"},
    {"role": "assistant", "content": "综合总结结果..."}  # 只保存最终结果
]

# 子 Agent 会话（不持久化）
# 每次执行都是全新的上下文
sub_agent_messages = [
    {"role": "system", "content": "你是文档总结专家..."},
    {"role": "user", "content": "文档内容...\n\n任务: 总结核心观点"}
]
```

**好处**：
- 避免会话膨胀
- 子 Agent 上下文干净
- 主 Agent 历史简洁

---

## 六、迁移策略

### 6.1 分阶段实施

#### 阶段一：基础设施（1-2 天）
- [ ] 创建 `agent_system/src/subagent/` 模块
- [ ] 实现 `SubAgentLoader` 配置加载器
- [ ] 实现 `SubAgentExecutor` 执行器
- [ ] 实现 `ParallelExecutor` 并行执行器
- [ ] 创建基础子 Agent 定义文件

#### 阶段二：工具集成（1 天）
- [ ] 实现 `SpawnSubAgentTool`
- [ ] 实现 `SpawnParallelSubAgentsTool`
- [ ] 注册工具到主 Agent
- [ ] 更新 ReAct prompt 模板

#### 阶段三：Pipeline 迁移（2-3 天）
- [ ] 将 `multi_doc_summary` 策略迁移到子 Agent
- [ ] 将 `chunk_recall` 策略迁移到子 Agent
- [ ] 移除 Pipeline 路由逻辑
- [ ] 更新意图识别逻辑

#### 阶段四：测试与优化（2 天）
- [ ] 单元测试
- [ ] 集成测试
- [ ] 性能测试
- [ ] 并发测试

### 6.2 回滚方案

保留 Pipeline 代码作为备选：

```python
# 在 settings.py 中添加开关
USE_SUB_AGENT_MODE = os.getenv("USE_SUB_AGENT_MODE", "true").lower() == "true"

# 在 agent.py 中
if settings.USE_SUB_AGENT_MODE:
    # 新的子 Agent 模式
    tools = [recall_tool, spawn_tool, spawn_parallel_tool, ...]
else:
    # 旧的 Pipeline 模式
    if route == "pipeline":
        # ... 原有逻辑
```


---

## 七、子 Agent 定义示例

### 7.1 document-summarizer.md

```markdown
---
name: document-summarizer
description: 总结单个文档的核心内容，提取关键信息和主要观点
model: inherit
tools: recall, read_document
max_iterations: 5
timeout: 120
---

你是文档总结专家，擅长提取学术论文和技术文档的核心信息。

## 核心职责
1. 阅读文档全文或关键片段
2. 提取核心观点和关键数据
3. 生成结构化摘要

## 输出格式

### 主题
[一句话概括文档主题]

### 核心观点
1. [观点1]
2. [观点2]
3. [观点3]

### 关键数据/结论
- [数据/结论1]
- [数据/结论2]

### 研究方法（如适用）
[简述研究方法]

## 注意事项
- 保持客观，不添加主观评价
- 引用原文关键语句时使用引号
- 标注不确定的信息
- 如果文档内容不完整，说明缺失部分
```

### 7.2 document-comparator.md

```markdown
---
name: document-comparator
description: 对比分析多个文档，找出异同点和关联
model: inherit
tools: recall, read_document
max_iterations: 8
timeout: 180
---

你是文献对比分析专家，擅长发现多篇文档之间的关联、差异和互补之处。

## 核心职责
1. 理解每篇文档的核心观点
2. 识别文档间的共同主题
3. 分析观点的异同
4. 发现潜在的矛盾或互补关系

## 输出格式

### 文档概览
| 文档 | 主题 | 核心观点 |
|------|------|---------|
| 文档1 | ... | ... |
| 文档2 | ... | ... |

### 共同点
1. [共同点1]
2. [共同点2]

### 差异点
1. [差异1]: 文档A认为...，文档B认为...
2. [差异2]: ...

### 互补关系
[描述文档间如何互相补充]

### 综合结论
[基于对比分析的综合结论]

## 注意事项
- 客观呈现各文档观点
- 明确标注观点来源
- 不强行制造关联
```

### 7.3 review-generator.md

```markdown
---
name: review-generator
description: 基于多篇文献生成综述报告
model: inherit
tools: recall, read_document
max_iterations: 10
timeout: 300
---

你是学术综述撰写专家，擅长整合多篇文献生成结构化的综述报告。

## 核心职责
1. 理解研究领域背景
2. 梳理文献脉络
3. 综合各方观点
4. 生成结构化综述

## 输出格式

### 1. 引言
[研究背景和综述目的]

### 2. 研究现状
#### 2.1 [主题1]
[相关文献综述]

#### 2.2 [主题2]
[相关文献综述]

### 3. 主要发现
[综合各文献的主要发现]

### 4. 研究趋势
[领域发展趋势分析]

### 5. 结论与展望
[总结和未来研究方向]

### 参考文献
[引用的文献列表]

## 注意事项
- 保持学术写作风格
- 正确引用文献
- 逻辑清晰，层次分明
- 避免简单罗列，要有分析和综合
```

### 7.4 web-researcher.md

```markdown
---
name: web-researcher
description: 从互联网搜索和整理信息
model: inherit
tools: web_search
max_iterations: 5
timeout: 120
---

你是网络信息研究专家，擅长从互联网搜索、筛选和整理有价值的信息。

## 核心职责
1. 根据任务需求制定搜索策略
2. 执行多轮搜索获取信息
3. 筛选和验证信息可靠性
4. 整理成结构化报告

## 搜索策略
1. 先用宽泛关键词了解概况
2. 再用精确关键词深入特定方面
3. 交叉验证多个来源

## 输出格式

### 搜索概要
- 搜索关键词: [使用的关键词]
- 信息来源数: [数量]

### 主要发现
1. [发现1]
   - 来源: [URL]
   - 可信度: 高/中/低

2. [发现2]
   - 来源: [URL]
   - 可信度: 高/中/低

### 综合结论
[基于搜索结果的综合结论]

### 信息局限
[说明搜索结果的局限性]

## 注意事项
- 优先选择权威来源
- 标注信息时效性
- 对矛盾信息进行说明
- 承认信息不完整时的局限
```


---

## 八、ReAct Prompt 模板更新

### 8.1 更新后的 ReAct Agent Prompt

```markdown
# agent_system/src/prompts/templates/react_agent.md (更新)

你是一个智能助手，可以使用工具来完成用户的任务。

## 可用工具

### 信息检索工具
- **recall(query)**: 从文档知识库检索相关信息
- **web_search(query)**: 从互联网搜索信息
- **read_document(doc_id)**: 读取完整文档内容

### 子 Agent 工具
- **spawn_sub_agent(task, context, agent_type)**: 创建子 Agent 处理独立任务
  - 适用场景: 需要独立上下文处理文档、任务可以委托给专门的 Agent
  - agent_type 可选: document-summarizer, document-comparator, literature-qa, review-generator, web-researcher

- **spawn_parallel_sub_agents(tasks)**: 并行创建多个子 Agent
  - 适用场景: 需要同时处理多个文档、任务相互独立可并行
  - tasks 格式: [{"task": "...", "context": "...", "agent_type": "..."}]

### 完成工具
- **finish(answer)**: 完成任务，输出最终答案

## 工作流程

1. **分析任务**: 理解用户需求，判断任务复杂度
2. **选择策略**:
   - 简单问题: 直接使用 recall/web_search 获取信息后回答
   - 单文档深度分析: 使用 read_document 或 spawn_sub_agent
   - 多文档处理: 使用 spawn_parallel_sub_agents 并行处理
3. **执行并观察**: 执行工具，观察结果
4. **综合回答**: 基于收集的信息生成最终答案

## 多文档处理示例

当用户要求处理多个文档时（如"总结这5篇论文"）:

```
Thought: 用户需要总结5篇论文，我应该并行处理每篇文档以提高效率
Action: spawn_parallel_sub_agents
Action Input: {
  "tasks": [
    {"task": "总结文档的核心观点和主要发现", "context": "[文档1内容]", "agent_type": "document-summarizer"},
    {"task": "总结文档的核心观点和主要发现", "context": "[文档2内容]", "agent_type": "document-summarizer"},
    ...
  ]
}
Observation: [各文档的总结结果]

Thought: 所有文档总结完成，现在需要综合这些总结生成最终答案
Action: finish
Action Input: {"answer": "[综合总结]"}
```

## 输出格式

每次响应必须遵循以下格式:

```
Thought: [你的思考过程]
Action: [工具名称]
Action Input: [工具参数，JSON格式]
```

或者完成任务时:

```
Thought: [最终思考]
Action: finish
Action Input: {"answer": "[最终答案]"}
```

## 注意事项

1. 每次只执行一个 Action
2. 仔细观察工具返回的 Observation
3. 如果信息不足，继续使用工具获取更多信息
4. 子 Agent 有独立上下文，适合处理大文档
5. 并行处理可以显著提高多文档任务的效率
```


---

## 九、API 端点扩展

### 9.1 子 Agent 管理端点

```python
# agent_system/api.py (新增端点)

@app.get("/agents")
async def list_agents():
    """列出所有可用的子 Agent"""
    from src.subagent.loader import get_sub_agent_loader
    
    loader = get_sub_agent_loader()
    agents = loader.load_all()
    
    return {
        "agents": [
            {
                "name": config.name,
                "description": config.description,
                "model": config.model,
                "tools": config.tools,
                "max_iterations": config.max_iterations,
                "timeout": config.timeout
            }
            for config in agents.values()
        ]
    }

@app.post("/agents/reload")
async def reload_agents():
    """热重载子 Agent 配置"""
    from src.subagent.loader import get_sub_agent_loader
    
    loader = get_sub_agent_loader()
    loader.reload()
    
    return {
        "success": True,
        "message": "Sub-agent configurations reloaded",
        "agent_count": len(loader.list_agents())
    }

@app.get("/agents/{agent_name}")
async def get_agent_detail(agent_name: str):
    """获取子 Agent 详细信息"""
    from src.subagent.loader import get_sub_agent_loader
    
    loader = get_sub_agent_loader()
    config = loader.get(agent_name)
    
    if not config:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")
    
    return {
        "name": config.name,
        "description": config.description,
        "system_prompt": config.system_prompt,
        "model": config.model,
        "tools": config.tools,
        "max_iterations": config.max_iterations,
        "timeout": config.timeout
    }
```

---

## 十、测试计划

### 10.1 单元测试

```python
# agent_system/tests/test_subagent.py

import pytest
from src.subagent.loader import SubAgentLoader, SubAgentConfig
from src.subagent.executor import SubAgentExecutor, SubAgentResult

class TestSubAgentLoader:
    def test_load_valid_agent(self, tmp_path):
        """测试加载有效的 Agent 配置"""
        agent_file = tmp_path / "test-agent.md"
        agent_file.write_text("""---
name: test-agent
description: Test agent
model: inherit
tools: recall
---

You are a test agent.
""")
        
        loader = SubAgentLoader(str(tmp_path))
        config = loader.get("test-agent")
        
        assert config is not None
        assert config.name == "test-agent"
        assert config.tools == ["recall"]
    
    def test_load_invalid_agent(self, tmp_path):
        """测试加载无效的 Agent 配置"""
        agent_file = tmp_path / "invalid.md"
        agent_file.write_text("No frontmatter")
        
        loader = SubAgentLoader(str(tmp_path))
        config = loader.get("invalid")
        
        assert config is None

class TestSubAgentExecutor:
    @pytest.mark.asyncio
    async def test_simple_execution(self, mock_llm):
        """测试简单执行"""
        config = SubAgentConfig(
            name="test",
            description="test",
            system_prompt="You are a test agent",
            model="inherit"
        )
        
        executor = SubAgentExecutor(
            config=config,
            parent_llm=mock_llm,
            parent_tools=[],
            parent_context={}
        )
        
        result = await executor.run("Test task")
        
        assert result.success
        assert result.output is not None
```

### 10.2 集成测试

```python
# agent_system/tests/test_integration.py

@pytest.mark.asyncio
async def test_parallel_document_summary():
    """测试并行文档总结"""
    from src.subagent.parallel import ParallelExecutor, ParallelTask
    
    tasks = [
        ParallelTask(
            task="总结核心观点",
            context="文档1内容...",
            agent_type="document-summarizer"
        ),
        ParallelTask(
            task="总结核心观点",
            context="文档2内容...",
            agent_type="document-summarizer"
        )
    ]
    
    executor = ParallelExecutor(
        parent_llm=mock_llm,
        parent_tools=[],
        parent_context={}
    )
    
    results = await executor.execute(tasks)
    
    assert len(results) == 2
    assert all(r.success for r in results)
```

---

## 十一、性能考量

### 11.1 并发控制

```python
# 默认配置
MAX_CONCURRENT_SUB_AGENTS = 5  # 最大并发子 Agent 数
SUB_AGENT_TIMEOUT = 120        # 单个子 Agent 超时（秒）
PARALLEL_TIMEOUT = 300         # 并行执行总超时（秒）
```

### 11.2 资源管理

- **LLM 连接复用**: 子 Agent 复用父 Agent 的 LLM 配置
- **工具实例共享**: 工具实例在父子 Agent 间共享
- **内存管理**: 子 Agent 执行完成后释放上下文

### 11.3 监控指标

```python
# 建议监控的指标
metrics = {
    "sub_agent_spawn_count": Counter,      # 子 Agent 创建次数
    "sub_agent_success_rate": Gauge,       # 成功率
    "sub_agent_avg_duration": Histogram,   # 平均执行时间
    "parallel_execution_count": Counter,   # 并行执行次数
    "token_usage_by_agent_type": Counter   # 按类型统计 token 使用
}
```

---

## 十二、总结

### 12.1 核心优势

1. **架构统一**: 移除 Pipeline/ReAct 分裂，统一使用 ReAct + 子 Agent
2. **上下文隔离**: 子 Agent 独立上下文，解决多文档超限问题
3. **并行处理**: 支持多文档并行处理，提升效率
4. **热插拔**: Markdown 定义子 Agent，无需改代码即可扩展
5. **LLM 自主决策**: 由 LLM 决定何时使用子 Agent，更智能

### 12.2 兼容性保证

- 后端 API 接口完全不变
- 前端无需强制改动
- 可选支持新的 SSE 事件展示子 Agent 进度
- 保留回滚开关

### 12.3 下一步行动

1. 评审本方案
2. 确认实施优先级
3. 开始阶段一实施
4. 持续测试和优化

---

## 附录：参考资料

- [Claude Code Plugins](https://github.com/anthropics/claude-code/tree/main/plugins)
- [LangChain Tools](https://python.langchain.com/docs/modules/agents/tools/)
- [ReAct Pattern](https://arxiv.org/abs/2210.03629)
