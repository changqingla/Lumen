---
name: domestic-global-research-status
description: Write a domestic-versus-international research status analysis. Use when the user asks for research status across domestic and international contexts, progress comparisons, or cross-region research differences. If documents have been uploaded, write from document evidence first and supplement with web search only when evidence is insufficient. Prefer direct KB reading tools and do not depend on local reader_kb copy paths.
---

# Domestic-Global Research Status Skill

## Execution Rules

1. Use `kb_list_docs` first to confirm the document scope.
2. Documents first, web second: extract evidence from documents first, and use `web_search` only when document evidence is insufficient.
3. If there are 3 or more documents, preferably use `spawn_agent` first to extract document evidence in parallel.
4. If there are fewer than 3 documents, the main agent may read directly.
5. When supplementing with web search, search only for missing information and avoid redundant accumulation.

## Subagent Document Extraction Requirements (3+ Documents)

Each subagent `task_description` should require extraction of at least:
- Research direction and problem definition
- Representative domestic work, institutions, and methods
- Representative international work, institutions, and methods
- Key results and evaluation metrics
- Development-stage judgment and limitations
- Quotable evidence, including document ID and line numbers

Use only the following `allowed_tools`:
- `kb_list_docs`
- `kb_read_doc`
- `kb_read_doc_lines`
- `kb_search_doc`

## Web Supplement Rules

1. Call `web_search` only in the following cases:
   - Documents lack evidence for either the domestic or international side.
   - Recent progress from the last two years is missing.
   - Key institutions, benchmark data, or policy background are missing.
2. List information gaps first, then run targeted search queries.
3. If `web_search` is unavailable or disabled, clearly state that web evidence cannot be supplemented.

## Output Structure (Required)

```markdown
# Domestic and International Research Status on {Topic}

## 1. Research Scope and Problem Definition
## 2. Domestic Research Status
## 3. International Research Status
## 4. Comparative Analysis (Directions / Methods / Metrics / Applications)
## 5. Research Gaps and Development Trends
## 6. Conclusions and Recommendations
## References (Document Sources + Web Sources)
```

## Quality Bar

1. Both the domestic and international sections must contain substantive content. Neither side may be missing.
2. Every key judgment must include evidence mapping, such as document paths or web sources.
3. Provide at least 3 domestic-versus-international differences and explain their causes.
4. Include research gaps and next-step directions. Do not stop at descriptive reporting.
5. If web materials support conclusions, mark their timeliness with dates or years.
6. Do not use document IDs in the final report. Use document names instead when references are needed.

## Final Delivery Requirements

1. Organize the final result as a complete Markdown document, preferably generated in the sandbox, for example by using `sandbox_write_file` to write `documents/domestic-global-research-status-{topic}.md`.
2. After generation, call `sandbox_publish_file` to publish the Markdown file so the frontend can display a downloadable artifact. Do not stop after only calling `write_file`.
3. If self-checking is needed, use `sandbox_read_file`, `sandbox_read_lines`, or `sandbox_list_directory` to inspect file content and paths.
4. The final response must include delivery information for the published Markdown document and clearly tell the user that the file is available for download. Do not only say that it has been generated, and do not only paste the body text.
5. Do not expose internal MinIO paths in the final response. If describing the result, only state that the Markdown document has been generated and made available for download, along with its purpose.
