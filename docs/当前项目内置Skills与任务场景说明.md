# Lumen 内置 Skills 与任务场景

更新时间：2026-07-16

本文档记录当前仓库实际提供的 Skill。发现、解析、启停和安装协议见
[技能模块技术文档](./技能模块技术文档.md)。

## 当前合同

- 仓库内置 Skill 位于 `runtimes/skills/public/`，当前有 14 个唯一名称。
- 用户安装的 Skill 位于 `runtimes/skills/custom/`；除目录说明外，该目录由 Git 忽略。
- Loader 递归发现 `public` 与 `custom` 下的 `SKILL.md`，跳过隐藏目录，并按名称排序。
- 无法解析的单个 `SKILL.md` 会被忽略；成功解析出的名称必须全局唯一，重复名称会使加载明确失败。
- `SKILL.md` 定义能力内容；启用状态保存在运行态
  `runtimes/config/extensions/extensions_config.json`。
- 真实扩展配置由 Git 忽略。仓库中的
  `runtimes/config/extensions/extensions_config.example.json` 只用于说明格式，不代表当前部署状态。
- 未在运行态配置中显式禁用的 Skill 默认启用。

## 内置清单

| Skill | 主要用途 | 典型交付物 |
| --- | --- | --- |
| `chart-visualization` | 为结构化数据选择图表并生成可视化 | 图表图片与生成参数 |
| `consulting-analysis` | 组织咨询级研究框架、证据和战略结论 | Markdown 分析框架或正式报告 |
| `data-analysis` | 分析 Excel/CSV、多 Sheet 数据 | 统计摘要、查询结果、导出数据 |
| `deep-research` | 对需要联网证据的问题做多角度研究 | 带来源的研究材料与结论 |
| `doc-comparison` | 比较多份文档或论文的异同 | 对比矩阵、差异与选型建议 |
| `docx` | 创建、读取、编辑 Word 文档 | `.docx` 文件 |
| `domestic-global-research-status` | 对比国内外研究进展 | 国内外研究现状专题报告 |
| `frontend-design` | 设计并实现前端页面或组件 | HTML/CSS/React 等前端代码 |
| `literature-review` | 组织研究脉络、方法、争议与空白 | 文献综述或 related work |
| `multi-doc-summary` | 综合多份材料的主题、共识和分歧 | 多文档综合摘要 |
| `paper-translation` | 将论文或 PDF 转换的 Markdown 翻译成中文 | 保留参考文献原文的中文 Markdown |
| `podcast-generation` | 将文本改写为双主持人音频节目 | 音频与对话稿 |
| `pptx-generator` | 创建、读取或编辑 PowerPoint | `.pptx` 文件或提取内容 |
| `xlsx` | 创建、读取、编辑或修复电子表格 | `.xlsx`、`.xlsm`、`.csv` 或 `.tsv` |

清单的权威来源是各目录中的 `SKILL.md`，不是本文档的文字快照。新增或删除
内置 Skill 时，应同时更新本文档并运行 Loader 唯一性测试。

## 选择原则

### 研究与知识综合

- 需要最新外部证据时使用 `deep-research`。
- 重点是汇总主线时使用 `multi-doc-summary`。
- 重点是横向差异和选型时使用 `doc-comparison`。
- 需要学术研究脉络时使用 `literature-review`。
- 题目明确要求国内外对照时使用 `domestic-global-research-status`。
- 已有证据，需要形成正式商业分析时使用 `consulting-analysis`。

### 数据与可视化

- 一般数据探索、聚合和查询使用 `data-analysis`。
- 主要输入或交付物是电子表格时使用 `xlsx`。
- 已有结构化结果，需要图形表达时使用 `chart-visualization`。

### 文件交付

- Word 文档使用 `docx`。
- 演示文稿使用 `pptx-generator`。
- 学术论文中文翻译使用 `paper-translation`。
- 文本音频化使用 `podcast-generation`。

### 前端实现

页面、组件、站点或现有界面优化使用 `frontend-design`。仓库当前没有名为
`web-design-guidelines` 的内置 Skill，不应在工作流中引用它。

## 管理接口

Gateway 当前提供以下接口：

| 方法与路径 | 作用 |
| --- | --- |
| `GET /api/skills` | 列出全部 Skill，包括禁用项 |
| `GET /api/skills/{skill_name}` | 获取单个 Skill |
| `PUT /api/skills/{skill_name}` | 以 `{ "enabled": true/false }` 更新启用状态 |
| `POST /api/skills/install` | 从线程目录中的 `.skill` ZIP 归档安装自定义 Skill |

启停操作只更新运行态 `extensions_config.json`，不会修改 `SKILL.md`。安装流程在
私有暂存目录中完成路径、成员类型、资源上限和 frontmatter 校验，再原子发布到
`custom`；如果名称已由任意 `public` 或 `custom` Skill 占用，则返回冲突。
