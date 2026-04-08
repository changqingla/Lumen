# Lumen 当前项目内置 Skills 与任务场景说明

更新时间：2026-03-18

## 1. 文档目标

本文档面向 Lumen 项目成员，介绍当前仓库已经内置的 Skills、它们分别解决什么问题、适合在哪些任务场景下使用，以及多个 Skill 之间如何组合协同。

这是一份“能力地图”文档，重点回答三个问题：

1. 当前项目到底支持哪些 Skill。
2. 每个 Skill 更适合处理什么类型的任务。
3. 面对具体业务需求时，应该优先选择哪一类 Skill 组合。

如果需要了解 Skill 的发现、加载、启停、安装和 API 机制，请参考《`Skills管理模块技术文档.md`》。

## 2. 当前状态快照

基于 2026-03-18 的当前仓库状态：

1. 内置 Skill 目录位于 `runtimes/skills/`。
2. 当前共发现 12 个一级 Skill 目录，即 12 个内置 Skill。
3. `runtimes/config/extensions_config.json` 当前未对 skills 做额外禁用配置：

```json
{
  "skills": {}
}
```

这意味着当前仓库内置 Skill 默认全部启用；只有在 `extensions_config.json` 的 `skills` 段中显式写入 `enabled: false` 时，某个 Skill 才会被禁用。

## 3. 能力全景图

从任务形态上看，当前内置 Skills 大致覆盖三类工作：

| 类别 | 典型 Skill | 主要覆盖任务 |
| --- | --- | --- |
| 研究与内容综合 | `deep-research`、`multi-doc-summary`、`doc-comparison`、`literature-review`、`domestic-global-research-status`、`consulting-analysis` | 开放域调研、多文档总结、论文综述、国内外研究现状、行业/市场/品牌/财务研究报告 |
| 数据与内容生产 | `data-analysis`、`chart-visualization`、`pptx`、`podcast-generation` | Excel/CSV 分析、图表生成、PPT 产出、文本转播客 |
| 前端与体验 | `frontend-design`、`web-design-guidelines` | 页面/组件设计实现、UI/UX 审核、可访问性与规范检查 |

换句话说，当前 Skills 能力已经覆盖了 Reader 中比较典型的“研究分析 -> 数据整理 -> 可视化表达 -> 内容交付 -> 前端实现/审查”链路。

## 4. 当前内置 Skill 清单

| Skill 名称 | 核心能力 | 典型输入 | 典型输出 | 覆盖场景 |
| --- | --- | --- | --- | --- |
| `chart-visualization` | 为数据自动选择合适图表并生成图像 | 结构化数据、指标序列、分类统计结果 | 图表图片、图表参数 | 折线图、柱状图、雷达图、地图、流程图、词云等可视化 |
| `consulting-analysis` | 生成咨询级研究框架和最终报告 | 研究主题、数据摘要、图表、调研结论 | 结构化分析框架、咨询报告 | 市场分析、行业研究、品牌分析、财务分析、竞品分析、尽调报告 |
| `data-analysis` | 对 Excel/CSV 做结构化分析 | Excel、CSV、多 Sheet 表格 | SQL 查询结果、统计摘要、导出文件 | 数据探索、汇总统计、过滤、分组、透视、跨表关联 |
| `deep-research` | 对开放域问题进行系统化联网研究 | 用户问题、研究主题、内容生成需求 | 多角度资料、事实依据、案例与趋势信息 | 概念研究、主题调研、行业信息收集、内容生成前置调研 |
| `doc-comparison` | 对多篇文档做结构化对比 | 多篇文档/论文 | 对比矩阵、异同分析、选型建议 | 方案对比、论文比较、横向评估、技术路线选型 |
| `domestic-global-research-status` | 输出“国内外研究现状”专题内容 | 文档资料、必要时补充联网资料 | 国内外研究现状报告 | 中外研究差异、国内外进展对比、研究空白归纳 |
| `frontend-design` | 设计并实现高质量前端界面 | 页面需求、组件需求、产品定位 | 前端页面/组件代码 | 落地页、官网、Dashboard、海报页、交互页面、美化现有 UI |
| `literature-review` | 生成高质量文献综述 | 多篇论文/研究资料 | 综述报告、研究脉络、争议与空白分析 | survey、related work、文献综述、研究方向梳理 |
| `multi-doc-summary` | 对多篇文档做主题化综合总结 | 多篇资料、论文、报告 | 主题总结、共识/分歧提炼、行动建议 | 汇总多份材料、提炼共同结论、快速形成 briefing |
| `podcast-generation` | 把文本内容转成双主持人播客 | 文章、报告、文案、说明文档 | MP3 播客、对话式 transcript | 内容音频化、播客栏目、报告转音频 |
| `pptx` | 围绕 `.pptx` 的读、写、改、生成 | 演示文稿需求、现有 PPTX 文件、模板 | `.pptx` 文件、提取内容、更新后的演示稿 | 生成幻灯片、编辑模板、解析 PPT、合并/拆分 Deck |
| `web-design-guidelines` | 审查 UI 代码是否符合 Web 界面规范 | 前端文件、组件文件、页面代码 | 审核问题清单 | UI review、UX audit、可访问性检查、设计规范审查 |

## 5. 按任务场景理解这些 Skill

### 5.1 研究分析与知识综合

这一组 Skill 主要解决“材料很多，但需要高质量结论”的问题：

| Skill | 更适合的任务 | 什么时候优先用 |
| --- | --- | --- |
| `deep-research` | 开放域联网调研 | 用户问题依赖互联网最新资料，或者任何内容生成前需要先做研究 |
| `multi-doc-summary` | 多文档综合总结 | 目标是“汇总和提炼主线”，不是强调严格对比 |
| `doc-comparison` | 多文档对比分析 | 目标是找差异、做横向评估、输出选型建议 |
| `literature-review` | 学术综述 | 需要建立研究脉络、方法谱系、争议点和研究空白 |
| `domestic-global-research-status` | 国内外研究现状专题 | 题目天然要求“国内 vs 国外”两个板块的并行分析 |
| `consulting-analysis` | 咨询级分析报告 | 目标不是简单总结，而是要形成完整分析框架、战略判断和正式报告 |

这一类任务覆盖的典型用户诉求包括：

1. “帮我总结这几篇论文的共同观点和差异。”
2. “写一篇关于某主题的文献综述。”
3. “基于已有资料和联网搜索，写一份行业研究报告。”
4. “整理某技术方向的国内外研究现状。”
5. “把多份市场资料整合成咨询风格分析报告。”

### 5.2 数据分析与图表表达

这一组 Skill 主要解决“有结构化数据，但还没有分析结果或表达结果”的问题：

| Skill | 更适合的任务 | 什么时候优先用 |
| --- | --- | --- |
| `data-analysis` | 表格数据分析 | 用户上传 Excel/CSV，想做查询、统计、汇总、透视、导出 |
| `chart-visualization` | 结果可视化 | 已经有数据或统计结果，需要生成合适的图表图片 |

典型场景包括：

1. 销售、用户、运营、财务等 Excel 数据的探索分析。
2. 多 Sheet 数据的聚合、过滤、关联分析。
3. 用柱状图、折线图、漏斗图、词云、地图等方式展示结果。
4. 为咨询报告或 PPT 生成插图级图表素材。

可以把它们理解为一条常见链路：

`data-analysis` 负责“算出来”，`chart-visualization` 负责“画出来”。

### 5.3 内容生产与交付物生成

这一组 Skill 主要解决“已经有内容素材，但需要转成可交付产物”的问题：

| Skill | 更适合的任务 | 什么时候优先用 |
| --- | --- | --- |
| `pptx` | 演示文稿交付 | 任务涉及 `.pptx` 文件的创建、编辑、解析、转换、模板修改 |
| `podcast-generation` | 文本音频化 | 需要把文稿、报告、文章转成适合收听的播客音频 |
| `consulting-analysis` | 正式报告生产 | 需要沉淀为完整 Markdown 研究报告或咨询输出 |

典型场景包括：

1. 研究报告转 PPT。
2. 市场分析结果转成正式演示文稿。
3. 长文档、文章、报告转成播客栏目。
4. 把多源分析结果包装成适合汇报和传播的内容。

### 5.4 前端设计与质量审核

这一组 Skill 主要解决“页面要做出来”或“页面做完要审核”的问题：

| Skill | 更适合的任务 | 什么时候优先用 |
| --- | --- | --- |
| `frontend-design` | 设计并落地页面/组件 | 用户要求实现页面、组件、站点、海报页，或者美化现有界面 |
| `web-design-guidelines` | 审核现有 UI 代码 | 用户要做 UI review、UX audit、可访问性检查、规范审查 |

典型场景包括：

1. 设计品牌官网、落地页、Dashboard、表单页。
2. 生成风格鲜明的 HTML/CSS/React 界面。
3. 检查现有前端代码是否符合 Web 界面规范。
4. 审核可访问性、层级结构、信息密度与交互体验问题。

## 6. 几组容易混淆的 Skill 如何区分

### 6.1 `multi-doc-summary`、`doc-comparison`、`literature-review`、`domestic-global-research-status`

这四个 Skill 都与“多文档阅读”有关，但输出目标并不相同：

| Skill | 关键词 | 适合的结果形态 |
| --- | --- | --- |
| `multi-doc-summary` | 综合总结 | 主题化归纳、TL;DR、共识/分歧、行动建议 |
| `doc-comparison` | 对比评估 | 对比矩阵、差异点、优劣势、选型建议 |
| `literature-review` | 学术综述 | 研究脉络、方法比较、争议、研究空白 |
| `domestic-global-research-status` | 国内外现状 | 国内研究、国外研究、差异分析、发展趋势 |

一句话区分：

1. 想“总结主线”，用 `multi-doc-summary`。
2. 想“比较谁更好/哪里不一样”，用 `doc-comparison`。
3. 想“写综述/related work”，用 `literature-review`。
4. 想“写国内外研究现状”，用 `domestic-global-research-status`。

### 6.2 `deep-research` 与 `consulting-analysis`

这两个 Skill 都会出现在研究类任务中，但角色不同：

1. `deep-research` 更像“调研方法论”，负责把外部资料查全、查深、查准。
2. `consulting-analysis` 更像“正式报告生产器”，负责把研究问题、数据、图表和证据组织成咨询级输出。

简单说：

1. 先查资料，用 `deep-research`。
2. 要成稿，用 `consulting-analysis`。

### 6.3 `data-analysis` 与 `chart-visualization`

1. `data-analysis` 负责结构化分析、SQL 查询、统计摘要和导出。
2. `chart-visualization` 负责把结果画成合适的图。

如果用户说“帮我算”，优先 `data-analysis`；如果用户说“帮我画”，优先 `chart-visualization`；如果两者都要，一般组合使用。

### 6.4 `frontend-design` 与 `web-design-guidelines`

1. `frontend-design` 是建设型 Skill，适合“从 0 到 1 做页面/组件”。
2. `web-design-guidelines` 是审核型 Skill，适合“检查现有 UI 有哪些问题”。

## 7. 典型 Skill 组合链路

### 7.1 行业研究与汇报链路

适合行业分析、竞品分析、品牌分析、投资研究等场景：

1. `deep-research`：先做多角度调研。
2. `multi-doc-summary` 或 `doc-comparison`：把多份资料沉淀为结构化结论。
3. `data-analysis`：分析用户补充的表格数据。
4. `chart-visualization`：生成报告所需图表。
5. `consulting-analysis`：形成咨询级报告。
6. `pptx`：把结论交付成演示文稿。

### 7.2 学术研究链路

适合论文阅读、综述写作、课题调研：

1. `multi-doc-summary`：快速摸清主题共识与主线。
2. `doc-comparison`：比较不同论文的方法、实验和结论差异。
3. `literature-review`：形成学术综述。
4. `domestic-global-research-status`：如果题目要求“国内外研究现状”，再转入该 Skill 输出专题版本。

### 7.3 数据汇报链路

适合 BI 分析、经营汇报、运营周报：

1. `data-analysis`：完成统计、聚合、筛选、关联。
2. `chart-visualization`：输出关键图表。
3. `pptx`：整理成演示文稿用于汇报。

### 7.4 内容多形态分发链路

适合同一份内容需要多种媒介分发的场景：

1. `consulting-analysis`：先生成正式报告。
2. `pptx`：转成汇报材料。
3. `podcast-generation`：转成双主持人音频节目。

### 7.5 前端交付链路

适合产品页面设计与验收：

1. `frontend-design`：实现页面和交互。
2. `web-design-guidelines`：做 UI/UX 规范审核和问题清单输出。

## 8. 当前 Skills 覆盖了哪些任务场景

从业务视角看，当前项目已经覆盖以下高频任务场景：

1. 开放域资料调研与事实收集。
2. 多篇文档/论文/报告的总结、对比、综述与专题写作。
3. 国内外研究现状类章节或独立报告写作。
4. 市场、行业、品牌、财务、投资、竞品等咨询类分析报告。
5. Excel/CSV 数据分析、统计摘要、查询和导出。
6. 各类分析图表与展示图形生成。
7. PPT 的创建、编辑、解析和交付。
8. 文本内容到播客音频的转换。
9. 前端页面、组件、站点和视觉化界面的实现。
10. 现有 UI 的设计规范、UX 和可访问性审查。

如果把它进一步抽象，可以说当前 Skill 体系已经覆盖了：

`研究`、`分析`、`总结`、`对比`、`写作`、`可视化`、`汇报交付`、`音频化传播`、`前端实现`、`界面审核`

这十类核心任务。

## 9. 如何查看和管理当前 Skill

当前项目已经提供了 Skill 管理接口，可用于查看和启停内置 Skill：

### 9.1 查看当前 Skill 列表

调用：

```http
GET /skills
```

返回当前运行时已发现的 Skill 清单及状态，包括：

1. `name`
2. `description`
3. `enabled`
4. `source`
5. `directory`

### 9.2 启用或禁用某个 Skill

调用：

```http
PATCH /skills/{skill_name}/enable
PATCH /skills/{skill_name}/disable
```

启停结果会同步写入 `runtimes/config/extensions_config.json`，并立即影响运行时可见性。

### 9.3 安装新的 Skill

调用：

```http
POST /skills/install
```

安装成功后，注册表会刷新，新的 Skill 会进入当前项目能力集。

## 10. 总结

截至 2026-03-18，Lumen 项目内置的 12 个 Skill 已经形成比较清晰的能力分层：

1. 以 `deep-research`、`multi-doc-summary`、`doc-comparison`、`literature-review`、`domestic-global-research-status`、`consulting-analysis` 为代表的研究分析层。
2. 以 `data-analysis`、`chart-visualization`、`pptx`、`podcast-generation` 为代表的数据表达与交付层。
3. 以 `frontend-design`、`web-design-guidelines` 为代表的前端建设与审核层。

因此，当前项目支持的 Skills 已经不只是“补充几个 prompt 模板”，而是能够覆盖从资料收集、知识综合、结构化分析，到图表、PPT、音频、前端页面等多种交付形态的一整套工作流。
